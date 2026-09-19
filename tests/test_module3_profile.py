# tests/test_module3_profile.py

"""模块三（用户简历 & 求职意向配置模块）自测脚本。

覆盖范围：
    1. parse_profile 解析：合法简历/意向、缺省为空、类型非法抛 ConfigError
    2. UserProfile 属性：has_resume / has_intention / is_configured
    3. full_text() 拼接完整画像文本（分段标记、非空段落）
    4. load_config 集成：完整 YAML 加载后 cfg.profile 正确

运行方式：python tests/test_module3_profile.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 保证可直接从项目根目录运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.config.profile import UserProfile, parse_profile  # noqa: E402
from job_assistant.config.settings import load_config  # noqa: E402
from job_assistant.errors import ConfigError  # noqa: E402

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


def test_parse_profile() -> None:
    print("== parse_profile 解析 ==")

    # 1. 合法简历 + 意向
    p = parse_profile({
        "user_resume": "教育背景：XX大学 计算机 本科\n项目经历：开发过招聘筛选工具",
        "user_intention": "意向岗位：后端开发\n意向城市：西安",
    })
    check("合法简历+意向解析", p.has_resume and p.has_intention and "后端开发" in p.intention)

    # 2. 缺省字段视为空
    p = parse_profile({})
    check("缺省字段为空", not p.has_resume and not p.has_intention and p.resume == "")

    # 3. 仅简历（意向缺省）
    p = parse_profile({"user_resume": "简历内容"})
    check("仅简历解析", p.has_resume and not p.has_intention)

    # 4. 仅意向（简历缺省）
    p = parse_profile({"user_intention": "意向内容"})
    check("仅意向解析", p.has_intention and not p.has_resume)

    # 5. user_resume 为数字 → ConfigError
    try:
        parse_profile({"user_resume": 123})
        check("数字简历抛 ConfigError", False)
    except ConfigError:
        check("数字简历抛 ConfigError", True)

    # 6. user_resume 为列表 → ConfigError
    try:
        parse_profile({"user_resume": ["a", "b"]})
        check("列表简历抛 ConfigError", False)
    except ConfigError:
        check("列表简历抛 ConfigError", True)

    # 7. user_intention 为映射 → ConfigError
    try:
        parse_profile({"user_intention": {"岗位": "后端"}})
        check("映射意向抛 ConfigError", False)
    except ConfigError:
        check("映射意向抛 ConfigError", True)

    # 8. 布尔值（int 子类）同样拒绝
    try:
        parse_profile({"user_resume": True})
        check("布尔简历抛 ConfigError", False)
    except ConfigError:
        check("布尔简历抛 ConfigError", True)


def test_user_profile() -> None:
    print("== UserProfile 行为 ==")

    # 1. is_configured
    check("空画像未配置", not UserProfile().is_configured)
    check("有简历即已配置", UserProfile(resume="x").is_configured)
    check("有意向即已配置", UserProfile(intention="x").is_configured)

    # 2. full_text 拼接（简历 + 意向，含分段标记）
    p = UserProfile(resume="简历A", intention="意向B")
    text = p.full_text()
    check("full_text 含简历段", "【用户简历】\n简历A" in text)
    check("full_text 含意向段", "【用户求职意向】\n意向B" in text)
    check("full_text 段落顺序", text.index("【用户简历】") < text.index("【用户求职意向】"))

    # 3. full_text 仅简历
    check("full_text 仅简历", UserProfile(resume="R").full_text() == "【用户简历】\nR")

    # 4. full_text 空画像返回空串
    check("full_text 空画像为空串", UserProfile().full_text() == "")

    # 5. 首尾空白去除，内部换行保留
    p = parse_profile({"user_resume": "  第一行\n第二行  "})
    check("首尾空白去除", p.resume == "第一行\n第二行")


def test_load_config_integration() -> None:
    print("== load_config 集成 ==")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. 完整 YAML：profile 挂载到 AppConfig
        good = root / "good.yaml"
        good.write_text(
            "napcat:\n  ws_url: \"ws://127.0.0.1:3001\"\n"
            "monitor_group_ids:\n  - 111\n"
            "user_resume: |\n  教育背景：XX大学\n  项目经历：招聘筛选工具\n"
            "user_intention: |\n  意向岗位：后端开发\n  意向城市：西安\n",
            encoding="utf-8",
        )
        cfg = load_config(good)
        check("profile 挂载成功", cfg.profile.has_resume and cfg.profile.has_intention)
        check("块文本保留换行", "教育背景：XX大学" in cfg.profile.resume and "项目经历" in cfg.profile.resume)

        # 2. 旧配置（无 profile 段）兼容：profile 为空
        old = root / "old.yaml"
        old.write_text("napcat:\n  ws_url: \"ws://127.0.0.1:3001\"\nmonitor_group_ids:\n  - 111\n", encoding="utf-8")
        cfg = load_config(old)
        check("旧配置兼容（profile 为空）", not cfg.profile.is_configured)

        # 3. 类型非法经 load_config 抛出
        bad = root / "bad.yaml"
        bad.write_text("user_resume:\n  - 不应是列表\n", encoding="utf-8")
        try:
            load_config(bad)
            check("load_config 拦截非法简历", False)
        except ConfigError:
            check("load_config 拦截非法简历", True)


def main() -> None:
    test_parse_profile()
    test_user_profile()
    test_load_config_integration()
    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
