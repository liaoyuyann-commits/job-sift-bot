# tests/test_module5_matcher.py

"""模块五（岗位匹配打分模块）自测脚本。

覆盖范围：
    1. JobScorer 打分：成功解析、浮点/字符串分数、布尔拒绝、越界截断、理由缺失
    2. 阈值过滤：等于阈值 80 → 推荐；79 → 仅存档（产品方案 5.4 测试场景 1/2）
    3. 容错：格式非法重试、LLM 调用失败标记、画像未配置前置校验
    4. ranking：按分数降序稳定排序、失败项排末尾
    5. settings：recommend_score_threshold 加载与校验、load_config 集成

运行方式：python tests/test_module5_matcher.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 保证可直接从项目根目录运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.config.profile import UserProfile  # noqa: E402
from job_assistant.config.settings import load_config  # noqa: E402
from job_assistant.errors import ConfigError, LLMCallError  # noqa: E402
from job_assistant.matcher.ranking import rank_by_score  # noqa: E402
from job_assistant.matcher.scorer import JobScorer, MatchResult  # noqa: E402
from job_assistant.parser.schema import JobPosting  # noqa: E402

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


class FakeLLM:
    """测试替身：按预设队列依次返回响应；可抛异常模拟 LLM 故障。"""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, prompt: str, system: str = "", *, temperature: float = 0.2) -> str:
        self.calls.append({"prompt": prompt, "system": system, "temperature": temperature})
        if not self.responses:
            raise RuntimeError("FakeLLM: 预设响应已耗尽")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_profile(resume: str = "掌握 Python / Java", intention: str = "意向岗位：后端开发\n意向城市：西安") -> UserProfile:
    return UserProfile(resume=resume, intention=intention)


def make_job(company: str = "美团", title: str = "Java 开发工程师") -> JobPosting:
    return JobPosting(company_name=company, job_title=title, location="西安", tech_stack="Java、Spring")


# ---------- 1. JobScorer 打分 ----------

def test_scorer() -> None:
    print("== JobScorer: 打分 ==")

    # 1. 成功打分：score + reason
    llm = FakeLLM('{"score": 85, "reason": "技术栈匹配，意向城市一致"}')
    result = JobScorer(llm, make_profile()).score(make_job())
    check("成功打分", not result.has_error and result.score == 85)
    check("理由解析", result.reason == "技术栈匹配，意向城市一致")
    check("prompt 含画像", "【用户简历】" in llm.calls[0]["prompt"] and "【用户求职意向】" in llm.calls[0]["prompt"])
    check("prompt 含岗位", "【岗位信息】" in llm.calls[0]["prompt"] and "美团" in llm.calls[0]["prompt"])

    # 2. 浮点分数 85.0 → 85
    result = JobScorer(FakeLLM('{"score": 85.0, "reason": "ok"}'), make_profile()).score(make_job())
    check("浮点分数转整数", result.score == 85)

    # 3. 数字字符串分数
    result = JobScorer(FakeLLM('{"score": "72", "reason": "ok"}'), make_profile()).score(make_job())
    check("字符串分数转整数", result.score == 72)

    # 4. 分数越界截断（120 → 100；-10 → 0），不视为失败
    result = JobScorer(FakeLLM('{"score": 120, "reason": "过高"}'), make_profile()).score(make_job())
    check("越界 120 截断为 100", not result.has_error and result.score == 100)
    result = JobScorer(FakeLLM('{"score": -10, "reason": "过低"}'), make_profile()).score(make_job())
    check("越界 -10 截断为 0", not result.has_error and result.score == 0)

    # 5. reason 缺失 → 空字符串
    result = JobScorer(FakeLLM('{"score": 66}'), make_profile()).score(make_job())
    check("reason 缺失为空串", not result.has_error and result.reason == "")

    # 6. 代码块包装
    result = JobScorer(FakeLLM('```json\n{"score": 90, "reason": "高度匹配"}\n```'), make_profile()).score(make_job())
    check("代码块包装打分", result.score == 90)

    # 7. 布尔 score 拒绝 → 重试后仍失败标记
    llm = FakeLLM('{"category": "x"}', '{"score": true, "reason": "r"}')
    result = JobScorer(llm, make_profile(), max_retries=1).score(make_job())
    check("布尔分数标记失败", result.has_error and "MAT.SCORE.001" in result.error)


# ---------- 2. 阈值过滤 ----------

def test_threshold() -> None:
    print("== JobScorer: 阈值过滤 ==")

    # 产品方案 5.4 测试场景 1：等于阈值 80 → 推荐（完整输出 + 写日报 + JSON 存档）
    llm = FakeLLM('{"score": 80, "reason": "恰好达到阈值"}')
    result = JobScorer(llm, make_profile(), threshold=80).score(make_job())
    check("score=80 且阈值=80 → 推荐", result.is_recommended and not result.has_error)

    # 产品方案 5.4 测试场景 2：79 分 → 仅存档（不推荐）
    llm = FakeLLM('{"score": 79, "reason": "略低于阈值"}')
    result = JobScorer(llm, make_profile(), threshold=80).score(make_job())
    check("score=79 且阈值=80 → 仅存档", not result.is_recommended and not result.has_error)

    # 阈值可配置：85 分在阈值 90 下不推荐、在阈值 80 下推荐
    result = JobScorer(FakeLLM('{"score": 85, "reason": "r"}'), make_profile(), threshold=90).score(make_job())
    check("85 分 < 阈值 90 → 不推荐", not result.is_recommended)
    result = JobScorer(FakeLLM('{"score": 85, "reason": "r"}'), make_profile(), threshold=80).score(make_job())
    check("85 分 ≥ 阈值 80 → 推荐", result.is_recommended)

    # 失败项永不推荐
    llm = FakeLLM(LLMCallError("服务不可用"))
    result = JobScorer(llm, make_profile()).score(make_job())
    check("失败项不推荐", result.has_error and not result.is_recommended)


# ---------- 3. 容错与前置校验 ----------

def test_fault_tolerance() -> None:
    print("== JobScorer: 容错与前置校验 ==")

    # 1. 格式非法 → 重试一次后成功
    llm = FakeLLM("垃圾输出", '{"score": 91, "reason": "重试成功"}')
    result = JobScorer(llm, make_profile(), max_retries=1).score(make_job())
    check("重试后成功", not result.has_error and result.score == 91 and result.retried)

    # 2. 格式非法 → 重试后仍失败
    llm = FakeLLM("抱歉无法解析", "还是垃圾")
    result = JobScorer(llm, make_profile(), max_retries=1).score(make_job())
    check("重试后失败标记", result.has_error and "MAT.SCORE.001" in result.error and result.retried)

    # 3. LLM 调用失败 → 标记 PAR.LLM.001（不阻断）
    llm = FakeLLM(LLMCallError("网络错误"))
    result = JobScorer(llm, make_profile()).score(make_job())
    check("LLM 调用失败标记", result.has_error and "PAR.LLM.001" in result.error)

    # 4. 画像未配置 → 前置校验失败（不调用 LLM）
    llm = FakeLLM()
    result = JobScorer(llm, UserProfile()).score(make_job())
    check("画像未配置标记", result.has_error and "user_resume" in result.error)
    check("画像未配置不调用 LLM", len(llm.calls) == 0)

    # 5. 仅简历 / 仅意向也可打分
    result = JobScorer(FakeLLM('{"score": 60, "reason": "r"}'), UserProfile(resume="R")).score(make_job())
    check("仅简历可打分", not result.has_error and result.score == 60)
    result = JobScorer(FakeLLM('{"score": 60, "reason": "r"}'), UserProfile(intention="I")).score(make_job())
    check("仅意向可打分", not result.has_error)


# ---------- 4. ranking ----------

def test_ranking() -> None:
    print("== ranking: 分数降序排序 ==")

    profile = make_profile()

    def result(score: int) -> MatchResult:
        return JobScorer(FakeLLM(f'{{"score": {score}, "reason": "r"}}'), profile).score(make_job())

    items = [result(60), result(90), result(75)]
    ranked = rank_by_score(items)
    check("降序排序", [r.score for r in ranked] == [90, 75, 60])
    check("不修改入参", [r.score for r in items] == [60, 90, 75])

    # 同分保持原始顺序（稳定）
    a, b, c = result(80), result(80), result(80)
    ranked = rank_by_score([b, a, c])
    check("同分稳定排序", ranked[0] is b and ranked[1] is a and ranked[2] is c)

    # 失败项排末尾
    failed = MatchResult(job=make_job(), error="[MAT.SCORE.001] x")
    ranked = rank_by_score([result(70), failed, result(95)])
    check("失败项排末尾", [r.score for r in ranked if not r.has_error] == [95, 70]
          and ranked[-1] is failed)

    # 空列表
    check("空列表排序", rank_by_score([]) == [])


# ---------- 5. settings ----------

def test_threshold_config() -> None:
    print("== settings: recommend_score_threshold ==")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. 显式配置
        good = root / "good.yaml"
        good.write_text("monitor_group_ids:\n  - 111\nrecommend_score_threshold: 85\n", encoding="utf-8")
        cfg = load_config(good)
        check("阈值加载", cfg.recommend_score_threshold == 85)

        # 2. 缺省 80
        old = root / "old.yaml"
        old.write_text("monitor_group_ids:\n  - 111\n", encoding="utf-8")
        cfg = load_config(old)
        check("缺省阈值 80", cfg.recommend_score_threshold == 80)

        # 3. 越界 → ConfigError
        for bad_value in (101, -1):
            bad = root / f"bad_{bad_value}.yaml"
            bad.write_text(f"recommend_score_threshold: {bad_value}\n", encoding="utf-8")
            try:
                load_config(bad)
                check(f"阈值 {bad_value} 抛 ConfigError", False)
            except ConfigError:
                check(f"阈值 {bad_value} 抛 ConfigError", True)

        # 4. 非整数 → ConfigError
        bad = root / "bad_str.yaml"
        bad.write_text("recommend_score_threshold: \"abc\"\n", encoding="utf-8")
        try:
            load_config(bad)
            check("字符串阈值抛 ConfigError", False)
        except ConfigError:
            check("字符串阈值抛 ConfigError", True)

        # 5. 浮点 80.5 → ConfigError（拒绝静默截断）
        bad = root / "bad_float.yaml"
        bad.write_text("recommend_score_threshold: 80.5\n", encoding="utf-8")
        try:
            load_config(bad)
            check("浮点阈值抛 ConfigError", False)
        except ConfigError:
            check("浮点阈值抛 ConfigError", True)


def main() -> None:
    test_scorer()
    test_threshold()
    test_fault_tolerance()
    test_ranking()
    test_threshold_config()
    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
