# tests/test_module1_backfill.py

"""模块一扩展：历史消息补拉（NapCat `get_group_msg_history` 扩展 API）自测脚本。

覆盖范围：
    1. history_to_message：历史记录 → GroupMessage（补 post_type/message_type、白名单过滤）
    2. scan_seen_ids：raw_msg 文件名解析（去重依据）
    3. run_backfill：拉取 → 去重 → 回调 全流程（注入假 fetcher，无需联网）
    4. 开关关闭 / 拉取失败 / 白名单外 / 同批重复 边界

运行方式：python tests/test_module1_backfill.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 保证可直接从项目根目录运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.config.settings import AppConfig, NapCatConfig  # noqa: E402
from job_assistant.listener.group_filter import GroupMessage  # noqa: E402
from job_assistant.listener.history_backfill import (  # noqa: E402
    history_to_message,
    run_backfill,
    scan_seen_ids,
)

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


def make_cfg(
    *,
    backfill: bool = True,
    count: int = 50,
    groups: list[int] | None = None,
) -> AppConfig:
    return AppConfig(
        napcat=NapCatConfig(
            history_backfill=backfill,
            backfill_count=count,
            backfill_http_url="http://127.0.0.1:3002",
        ),
        monitor_group_ids=groups or [111],
    )


def make_record(
    message_id: int,
    group_id: int = 111,
    text: str = "招聘 后端开发",
    *,
    with_post_type: bool = True,
) -> dict:
    record = {
        "message_id": message_id,
        "group_id": group_id,
        "user_id": 888,
        "time": 1789746000,
        "message": [{"type": "text", "data": {"text": text}}],
    }
    if with_post_type:
        record["message_type"] = "group"
        record["post_type"] = "message"
    return record


# ---------- 1. history_to_message ----------

def test_history_to_message() -> None:
    print("== history_to_message: 历史记录转换 ==")

    # 带事件字段的历史记录（NapCat 实测返回结构）→ 直接转换
    msg = history_to_message(make_record(1), [111])
    check("带事件字段转换", isinstance(msg, GroupMessage)
          and msg.message_id == 1 and msg.group_id == 111
          and msg.raw_text == "招聘 后端开发")

    # 缺 post_type / message_type 的历史记录 → setdefault 补齐后转换
    msg = history_to_message(make_record(2, with_post_type=False), [111])
    check("缺事件字段自动补齐", isinstance(msg, GroupMessage) and msg.message_id == 2)

    # 白名单外群 → None（与实时消息同一套过滤）
    check("白名单外群丢弃", history_to_message(make_record(3, group_id=999), [111]) is None)

    # 空白名单 → None
    check("空白名单丢弃", history_to_message(make_record(4), []) is None)


# ---------- 2. scan_seen_ids ----------

def test_scan_seen_ids() -> None:
    print("== scan_seen_ids: 已处理消息索引 ==")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "g111_m222.txt").write_text("x", encoding="utf-8")
        (root / "g111_m333.txt").write_text("x", encoding="utf-8")
        (root / "g999_m444.txt").write_text("x", encoding="utf-8")
        (root / "not_a_msg.txt").write_text("x", encoding="utf-8")
        seen = scan_seen_ids(root)
        check("解析 g/m 文件名", seen == {(111, 222), (111, 333), (999, 444)})
        check("非 g/m 文件名忽略", len(seen) == 3)

    check("目录不存在返回空", scan_seen_ids(Path(tmp := "C:/nonexistent_xyz")) == set())


# ---------- 3. run_backfill 全流程 ----------

def test_run_backfill() -> None:
    print("== run_backfill: 拉取 → 去重 → 回调 ==")

    # 3.1 开关关闭 → 不执行任何拉取
    with tempfile.TemporaryDirectory() as tmp:
        calls: list[GroupMessage] = []
        stats = run_backfill(make_cfg(backfill=False), calls.append, tmp)
        check("开关关闭全 0", stats["fetched"] == 0 and stats["delivered"] == 0
              and stats["handled_groups"] == 0)

    # 3.2 正常流程：3 条历史（1 条已处理跳过、2 条进入链路），同批重复只算 1 次
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        (raw / "g111_m100.txt").write_text("旧消息", encoding="utf-8")  # 已处理过

        def fake_fetcher(http_base: str, group_id: int, count: int) -> list[dict]:
            assert http_base == "http://127.0.0.1:3002"
            assert group_id == 111
            return [
                make_record(300),        # 新
                make_record(200),        # 新
                make_record(100),        # 已处理 → 跳过
                make_record(300),        # 同批重复 → 只算 1 次
                make_record(500, group_id=999),  # 白名单外 → 丢弃不计
            ]

        calls: list[GroupMessage] = []
        stats = run_backfill(make_cfg(), calls.append, raw, fetcher=fake_fetcher)
        check("拉取数=5", stats["fetched"] == 5)
        check("进入链路=2", stats["delivered"] == 2)
        check("跳过=2（已处理1+同批重复1）", stats["skipped"] == 2)
        check("成功群数=1", stats["handled_groups"] == 1)
        check("回调消息 ID 正确", sorted(m.message_id for m in calls) == [200, 300])
        check("回调消息为 GroupMessage", all(isinstance(m, GroupMessage) for m in calls))

    # 3.3 单群拉取失败 → 不阻断，failed_groups 计数
    with tempfile.TemporaryDirectory() as tmp:
        def bad_fetcher(*_args) -> list[dict]:
            raise OSError("网络错误")

        stats = run_backfill(make_cfg(groups=[111, 222]), (lambda m: None), tmp,
                             fetcher=bad_fetcher)
        check("失败群数=2", stats["failed_groups"] == 2)
        check("无成功群", stats["handled_groups"] == 0 and stats["delivered"] == 0)

    # 3.4 多群混合：111 成功、222 失败
    with tempfile.TemporaryDirectory() as tmp:
        def mixed_fetcher(http_base: str, group_id: int, count: int) -> list[dict]:
            if group_id == 222:
                raise OSError("群不可达")
            return [make_record(1), make_record(2)]

        calls: list[GroupMessage] = []
        stats = run_backfill(make_cfg(groups=[111, 222]), calls.append, tmp,
                             fetcher=mixed_fetcher)
        check("多群混合成功 1 群", stats["handled_groups"] == 1 and stats["failed_groups"] == 1)
        check("多群混合进入链路=2", stats["delivered"] == 2)


def main() -> None:
    test_history_to_message()
    test_scan_seen_ids()
    test_run_backfill()
    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
