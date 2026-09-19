# tests/test_module1_integration.py

"""模块一集成自测：本地模拟 NapCat 正向 WebSocket，验证完整链路。

链路验证：连接 → 接收事件 → 白名单过滤 → on_message 回调 → 手动停止。
运行方式：python tests/test_module1_integration.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.config.settings import AppConfig, NapCatConfig  # noqa: E402
from job_assistant.listener.group_filter import GroupMessage  # noqa: E402
from job_assistant.listener.napcat_client import NapCatListener  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402

HOST, PORT = "127.0.0.1", 39001


def make_event(group_id: int, text: str) -> str:
    """构造一条 OneBot v11 群消息事件 JSON。"""
    return json.dumps({
        "post_type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": 10001,
        "message_id": group_id,  # 用群号当 message_id 便于断言
        "time": 1779000000,
        "message": [{"type": "text", "data": {"text": text}}],
    })


async def fake_napcat_server(ready: threading.Event) -> None:
    """模拟 NapCat 正向 WS：连接建立后推送两条消息后保持连接。"""

    async def handler(ws):
        await ws.send(make_event(111, "白名单群招聘消息"))
        await ws.send(make_event(999, "非监听群消息"))
        # 保持连接，等待客户端自行停止
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass

    async with serve(handler, HOST, PORT):
        ready.set()  # 服务端开始监听后即置位，避免与客户端连接形成死锁
        await asyncio.Future()  # 服务端长期运行，直到测试结束


def main() -> int:
    print("== 模块一集成链路验证（模拟 NapCat 正向 WS）==")

    server_ready = threading.Event()
    server_thread = threading.Thread(
        target=lambda: asyncio.run(fake_napcat_server(server_ready)), daemon=True,
    )
    server_thread.start()
    if not server_ready.wait(timeout=5):
        print("  [FAIL] 模拟服务端启动超时")
        return 1

    cfg = AppConfig(napcat=NapCatConfig(ws_url=f"ws://{HOST}:{PORT}", reconnect_interval=1),
                    monitor_group_ids=[111])
    received: list[GroupMessage] = []
    got = threading.Event()

    def on_message(msg: GroupMessage) -> None:
        received.append(msg)
        got.set()

    listener = NapCatListener(cfg, on_message=on_message)
    listener.start()

    passed = got.wait(timeout=8)
    listener.stop()

    # 断言：只收到白名单内群(111)的消息，非监听群(999)被丢弃
    if not passed:
        print("  [FAIL] 未在超时时间内收到回调消息")
        return 1
    ok = len(received) == 1 and received[0].group_id == 111 and received[0].message_id == 111
    # 收到的事件数 = 2（含被过滤的 999），命中数 = 1
    stats_ok = listener.stats["received"] == 2 and listener.stats["passed"] == 1
    print(f"  [{'PASS' if ok else 'FAIL'}] 白名单过滤正确 (收到回调={len(received)}, group_id={[m.group_id for m in received]})")
    print(f"  [{'PASS' if stats_ok else 'FAIL'}] 统计正确 (received={listener.stats['received']}, passed={listener.stats['passed']})")
    print(f"  [PASS] 手动停止成功, 运行时长={listener.runtime_seconds:.1f}s")
    return 0 if (ok and stats_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
