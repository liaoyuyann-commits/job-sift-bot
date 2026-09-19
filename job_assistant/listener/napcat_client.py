# job_assistant/listener/napcat_client.py

"""NapCat 只读监听客户端（模块一核心）。

设计要点（对应产品方案 4.1 与风控规范）：
    - 以客户端身份连接 NapCat 正向 WebSocket，仅接收事件，代码层面不提供任何发送能力
    - 断线自动重连（间隔可配），记录运行时长，支持手动启停
    - 收到的消息经 group_filter 白名单过滤后回调 on_message
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus, WebSocketException

from ..config.settings import AppConfig
from ..errors import ListenerError
from .group_filter import GroupMessage, filter_message

logger = logging.getLogger(__name__)

# 事件循环空闲检查间隔（秒）：用于感知停止信号，及时退出阻塞的 recv
_IDLE_POLL_INTERVAL = 1.0


class NapCatListener:
    """NapCat 消息监听器：连接、过滤、回调，启停可控，纯只读。"""

    def __init__(
        self,
        config: AppConfig,
        on_message: Callable[[GroupMessage], None] | None = None,
        on_connected: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._on_message = on_message or (lambda msg: None)
        self._on_connected = on_connected  # 每次连接成功触发（含断线重连），用于历史补拉
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()   # 线程安全的停止信号
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._received_count = 0             # 收到的群消息事件数（含被过滤的）
        self._passed_count = 0               # 命中白名单的消息数

    # ---------- 生命周期：手动启停 ----------

    def start(self) -> None:
        """启动监听（后台线程运行事件循环，不阻塞调用方）。"""
        if self._thread and self._thread.is_alive():
            raise ListenerError("监听器已在运行中，请先停止", error_code="LST.RUN.002")
        self._stop_evt.clear()
        self._started_at = time.time()
        self._stopped_at = None
        self._thread = threading.Thread(target=self._thread_main, name="napcat-listener", daemon=True)
        self._thread.start()
        logger.info("监听已启动: ws_url=%s", self._config.napcat.ws_url)

    def stop(self) -> None:
        """停止监听，并等待后台线程退出。"""
        self._stop_evt.set()
        if self._loop is not None:
            # 跨线程唤醒事件循环，使其及时退出阻塞的 recv
            self._loop.call_soon_threadsafe(self._stop_evt_threadsafe.set)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._stopped_at = time.time()
        logger.info(
            "监听已停止: 运行时长=%.1fs, 收到事件=%d, 命中白名单=%d",
            self.runtime_seconds, self._received_count, self._passed_count,
        )

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_evt.is_set())

    @property
    def runtime_seconds(self) -> float:
        """本次运行时长（秒）；未启动过返回 0。"""
        if self._started_at is None:
            return 0.0
        end = self._stopped_at or time.time()
        return max(0.0, end - self._started_at)

    @property
    def stats(self) -> dict[str, int]:
        """监听统计：供日志与后续模块使用。"""
        return {"received": self._received_count, "passed": self._passed_count}

    # ---------- 内部实现 ----------

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:
            # 事件循环内部未捕获异常：记录但不抛出到线程外，避免静默崩溃
            logger.exception("监听线程异常退出")
            self._stop_evt.set()

    def _handle_raw(self, raw: Any) -> None:
        """处理一条原始 WS 报文：解析 JSON → 白名单过滤 → 回调。"""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("收到非 JSON 报文，已忽略")
            return
        if not isinstance(event, dict):
            return

        # 只关心 message 事件；notice/request/meta 由 filter_message 内部忽略
        self._received_count += 1
        msg = filter_message(event, self._config.monitor_group_ids)
        if msg is None:
            return
        self._passed_count += 1
        self._on_message(msg)

    async def _run(self) -> None:
        """事件循环主逻辑：连接 → 收消息 → 断线重连。"""
        self._loop = asyncio.get_running_loop()
        # 线程安全停止事件：由 stop() 通过 call_soon_threadsafe 触发
        self._stop_evt_threadsafe = asyncio.Event()
        url = self._config.napcat.ws_url
        interval = max(1, self._config.napcat.reconnect_interval)

        while not self._stop_evt.is_set():
            try:
                async with connect(url) as ws:
                    logger.info("已连接 NapCat: %s", url)
                    # 连接成功（含断线重连）触发补拉回调：独立线程执行，
                    # 避免阻塞事件循环（补拉为 HTTP 阻塞调用，低频一次性）
                    if self._on_connected is not None:
                        threading.Thread(
                            target=self._on_connected, name="napcat-on-connected", daemon=True,
                        ).start()
                    while not self._stop_evt.is_set():
                        try:
                            # 1 秒超时轮询停止信号，保证 stop() 可及时生效
                            raw = await asyncio.wait_for(ws.recv(), timeout=_IDLE_POLL_INTERVAL)
                        except asyncio.TimeoutError:
                            continue
                        self._handle_raw(raw)
                # 正常退出连接（如服务端主动关闭），按重连策略继续
            except (OSError, InvalidStatus, WebSocketException, asyncio.TimeoutError) as e:
                if self._stop_evt.is_set():
                    break
                logger.error("NapCat 连接失败/断开: %s，%d 秒后重连", e, interval)
                try:
                    await asyncio.wait_for(self._stop_evt_threadsafe.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue
            except Exception:
                logger.exception("监听循环发生未预期异常")
                if self._stop_evt.is_set():
                    break
                try:
                    await asyncio.wait_for(self._stop_evt_threadsafe.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue
