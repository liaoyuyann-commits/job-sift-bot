# tests/test_module1_listener.py

"""模块一（群监听配置模块）自测脚本。

覆盖范围：
    1. group_filter 白名单过滤（命中/未命中/空白名单/非群消息）
    2. 文本、图片 URL、网页链接提取
    3. config 加载与校验（合法配置/空数组/非法群号/缺文件/坏 YAML）

运行方式：python tests/test_module1_listener.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 保证可直接从项目根目录运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.config.settings import load_config  # noqa: E402
from job_assistant.errors import ConfigError  # noqa: E402
from job_assistant.listener.group_filter import filter_message  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def make_event(group_id: int, message: list[dict], message_id: int = 1) -> dict:
    """构造 OneBot v11 群消息事件。"""
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": 10001,
        "message_id": message_id,
        "time": 1779000000,
        "message": message,
        "raw_message": "",
    }


def test_group_filter() -> None:
    print("== group_filter 白名单过滤 ==")
    whitelist = [111, 222]

    # 1. 白名单内群命中
    msg = filter_message(make_event(111, [{"type": "text", "data": {"text": "Java 后端招聘"}}]), whitelist)
    check("白名单内群消息通过", msg is not None and msg.group_id == 111)

    # 2. 白名单外群丢弃
    check("白名单外群消息丢弃", filter_message(make_event(333, [{"type": "text", "data": {"text": "x"}}]), whitelist) is None)

    # 3. 空白名单：不监听任何群
    check("空白名单全部丢弃", filter_message(make_event(111, [{"type": "text", "data": {"text": "x"}}]), []) is None)

    # 4. 非群消息事件（notice）忽略
    check(
        "非群消息事件忽略",
        filter_message({"post_type": "notice", "message_type": "group", "group_id": 111}, whitelist) is None,
    )

    # 5. 文本提取
    msg = filter_message(
        make_event(111, [{"type": "text", "data": {"text": "公司A"}}, {"type": "face", "data": {"id": "1"}},
                         {"type": "text", "data": {"text": "招后端"}}]),
        whitelist,
    )
    check("文本段拼接提取", msg is not None and msg.raw_text == "公司A招后端")

    # 6. 图片地址提取
    msg = filter_message(
        make_event(222, [{"type": "image", "data": {"url": "https://p.qpic.cn/a.jpg"}}]), whitelist,
    )
    check("图片 URL 提取", msg is not None and msg.image_urls == ["https://p.qpic.cn/a.jpg"])

    # 7. 网页链接提取（去重）
    text = "详情见 https://a.com/job/1 和 https://a.com/job/1 及 http://b.cn/x"
    msg = filter_message(make_event(111, [{"type": "text", "data": {"text": text}}]), whitelist)
    check("网页链接提取去重", msg is not None and msg.urls == ["https://a.com/job/1", "http://b.cn/x"])

    # 8. 元数据保留
    msg = filter_message(make_event(222, [{"type": "text", "data": {"text": "y"}}], message_id=99), whitelist)
    check("元数据保留", msg is not None and msg.message_id == 99 and msg.user_id == 10001 and msg.timestamp == 1779000000)


def test_config() -> None:
    print("== config 加载与校验 ==")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. 合法配置
        good = root / "good.yaml"
        good.write_text(
            "napcat:\n  ws_url: \"ws://127.0.0.1:3001\"\n  reconnect_interval: 3\n"
            "monitor_group_ids:\n  - 111\n  - 222\n",
            encoding="utf-8",
        )
        cfg = load_config(good)
        check("合法配置加载", cfg.monitor_group_ids == [111, 222] and cfg.napcat.reconnect_interval == 3)

        # 2. 空数组：不监听任何群
        empty = root / "empty.yaml"
        empty.write_text("napcat:\n  ws_url: \"ws://127.0.0.1:3001\"\nmonitor_group_ids: []\n", encoding="utf-8")
        check("空群列表合法", load_config(empty).monitor_group_ids == [])

        # 3. 缺文件
        try:
            load_config(root / "missing.yaml")
            check("缺文件抛 ConfigError", False)
        except ConfigError:
            check("缺文件抛 ConfigError", True)

        # 4. 非法群号（负数）
        bad = root / "bad.yaml"
        bad.write_text("monitor_group_ids:\n  - -5\n", encoding="utf-8")
        try:
            load_config(bad)
            check("非法群号抛 ConfigError", False)
        except ConfigError:
            check("非法群号抛 ConfigError", True)

        # 5. 坏 YAML
        broken = root / "broken.yaml"
        broken.write_text("napcat: [unclosed\n", encoding="utf-8")
        try:
            load_config(broken)
            check("坏 YAML 抛 ConfigError", False)
        except ConfigError:
            check("坏 YAML 抛 ConfigError", True)

        # 6. ws_url 非法协议
        badurl = root / "badurl.yaml"
        badurl.write_text("napcat:\n  ws_url: \"http://127.0.0.1:3001\"\n", encoding="utf-8")
        try:
            load_config(badurl)
            check("非法 ws_url 抛 ConfigError", False)
        except ConfigError:
            check("非法 ws_url 抛 ConfigError", True)


def main() -> None:
    test_group_filter()
    test_config()
    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
