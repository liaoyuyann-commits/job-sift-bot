# job_assistant/listener/history_backfill.py

"""历史消息补拉（NapCat 扩展 API `get_group_msg_history`）。

业务场景（电脑休眠 / 程序重启期间，正向 WS 只推实时事件、不补历史）：
    连接成功后按群拉取最近 `backfill_count` 条历史消息，逐条走正常消息链路，
    补齐休眠与重启期间漏掉的招聘信息。

安全设计（对齐产品方案"只读优先，风控第一"）：
    - 仅通过 NapCat HTTP 扩展接口拉取历史消息（读操作），不发送任何内容
    - 每次连接只补拉一次、每群条数上限可配（默认 50），频次严格受限
    - 按 (group_id, message_id) 跳过已处理消息（依据 raw_msg 目录文件名），
      避免重复进入 AI 解析与打分（不重复消耗 LLM 调用）
    - 单群拉取失败仅 WARN 记录，不阻断监听主流程
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config.settings import AppConfig
from .group_filter import GroupMessage, filter_message

logger = logging.getLogger(__name__)

# raw_msg 文件名：g{群号}_m{消息ID}.txt
_RAW_MSG_NAME_RE = re.compile(r"^g(\d+)_m(\d+)\.txt$")

# 请求超时（秒）：历史消息拉取是低频一次性操作，给足时间但不过长
_FETCH_TIMEOUT = 15


def scan_seen_ids(raw_msg_dir: str | Path) -> set[tuple[int, int]]:
    """扫描 raw_msg 目录文件名，返回已处理消息 ID 集合 {(group_id, message_id)}。

    用于补拉去重：已经实时处理过的历史消息不再重复进入 AI 解析流程。
    """
    seen: set[tuple[int, int]] = set()
    directory = Path(raw_msg_dir)
    if not directory.is_dir():
        return seen
    for path in directory.glob("g*_m*.txt"):
        match = _RAW_MSG_NAME_RE.match(path.name)
        if match:
            seen.add((int(match.group(1)), int(match.group(2))))
    return seen


def history_to_message(
    record: dict[str, Any],
    monitor_group_ids: list[int],
) -> GroupMessage | None:
    """把一条历史消息记录转换为 GroupMessage（复用白名单过滤与规范化）。

    NapCat 历史消息记录缺少 OneBot 事件级字段（post_type / message_type），
    补齐这两个字段后走 `filter_message`，保证与实时消息同一套过滤与清洗逻辑。
    """
    event = dict(record)
    event.setdefault("post_type", "message")
    event.setdefault("message_type", "group")
    return filter_message(event, monitor_group_ids)


def fetch_group_history(
    http_base: str,
    group_id: int,
    count: int = 50,
) -> list[dict[str, Any]]:
    """调用 NapCat HTTP 扩展接口拉取群历史消息（只读，不发送任何内容）。

    Args:
        http_base: NapCat HTTP 服务地址（如 http://127.0.0.1:3002）。
        group_id: 目标群号。
        count: 拉取条数上限（message_seq=0 从最新往前取）。

    Returns:
        历史消息记录列表（时间倒序，最新在前）。

    Raises:
        OSError: 网络/HTTP 错误或返回结构异常（由调用方 WARN 记录，不阻断）。
    """
    body = json.dumps({
        "group_id": group_id,
        "message_seq": 0,       # 0 = 从最新消息开始向前取
        "count": count,
        "reverse_order": True,  # 最新在前返回
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{http_base.rstrip('/')}/get_group_msg_history",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))

    # 兼容两种返回结构：{"data": {"messages": [...]}} 或 {"data": [...]}
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        messages = data.get("messages") or []
    elif isinstance(data, list):
        messages = data
    else:
        messages = []
    return [m for m in messages if isinstance(m, dict)]


def run_backfill(
    cfg: AppConfig,
    on_message: Callable[[GroupMessage], None],
    raw_msg_dir: str | Path,
    fetcher: Callable[[str, int, int], list[dict[str, Any]]] | None = None,
) -> dict[str, int]:
    """对全部监听群执行一次历史补拉：拉取 → 去重 → 逐条回调。

    设计：每次连接成功调用一次（启动首次 + 断线重连），单群失败不影响其他群；
    已处理消息按 raw_msg 文件名去重，避免重复消耗 LLM 调用。

    Args:
        cfg: 应用配置（napcat.history_backfill / backfill_count / backfill_http_url、
            monitor_group_ids）。
        on_message: 与实时消息同一条消息处理回调（模块二~六全链路）。
        raw_msg_dir: raw_msg 数据目录（去重依据，见 scan_seen_ids）。
        fetcher: 拉取函数（测试注入用）；默认 fetch_group_history。

    Returns:
        统计: {fetched: 拉取总数, delivered: 补拉进入链路数, skipped: 已处理跳过数,
               failed_groups: 拉取失败群数, handled_groups: 成功群数}
    """
    if not cfg.napcat.history_backfill:
        logger.info("历史消息补拉已关闭（napcat.history_backfill=false）")
        return {"fetched": 0, "delivered": 0, "skipped": 0, "failed_groups": 0, "handled_groups": 0}

    fetcher = fetcher or fetch_group_history
    seen = scan_seen_ids(raw_msg_dir)
    http_base = cfg.napcat.backfill_http_url
    limit = max(1, cfg.napcat.backfill_count)

    fetched_total = 0
    delivered_total = 0
    skipped_total = 0
    failed_groups = 0
    handled_groups = 0

    for group_id in cfg.monitor_group_ids:
        try:
            records = fetcher(http_base, group_id, limit)
        except Exception as e:  # noqa: BLE001 - 单群失败仅 WARN，不阻断监听
            failed_groups += 1
            logger.warning("历史消息补拉失败: group_id=%s 错误=%s", group_id, e)
            continue

        handled_groups += 1
        fetched_total += len(records)
        # 最新在前 → 反转后按时间正序处理（旧 → 新，保持时间线一致）
        for record in reversed(records):
            msg = history_to_message(record, cfg.monitor_group_ids)
            if msg is None:
                continue
            key = (msg.group_id, msg.message_id)
            if key in seen:
                skipped_total += 1
                continue
            seen.add(key)  # 本次补拉内也去重（同一批可能含重复）
            delivered_total += 1
            try:
                on_message(msg)
            except Exception as e:  # noqa: BLE001 - 单条消息失败不阻断补拉其余消息
                logger.warning("补拉消息处理失败: group_id=%s message_id=%s 错误=%s",
                               msg.group_id, msg.message_id, e)

    if handled_groups:
        logger.info(
            "历史消息补拉完成: 群数=%d 拉取=%d 进入链路=%d 跳过已处理=%d 失败群=%d",
            handled_groups, fetched_total, delivered_total, skipped_total, failed_groups,
        )
    else:
        logger.info("历史消息补拉: 无可用监听群或全部失败 (failed=%d)", failed_groups)
    return {
        "fetched": fetched_total,
        "delivered": delivered_total,
        "skipped": skipped_total,
        "failed_groups": failed_groups,
        "handled_groups": handled_groups,
    }
