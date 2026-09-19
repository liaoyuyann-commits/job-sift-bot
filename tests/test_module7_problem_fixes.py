# tests/test_module7_problem_fixes.py

"""问题修复回归测试（缺陷&需求报告 问题1/4/5 + 直连 DeepSeek 链路）。

覆盖范围：
    1. 问题1（宣讲会地点误填工作地点）：extractor 地点字段校验
       - location 值本身命中宣讲/场地特征词 → 标记待人工复核
       - 原文含宣讲类关键词且 location 与其同段 → 标记复核（上下文识别）
       - 原文含宣讲关键词但 location 不在同段 → 不误标
       - 正常工作地点 → 不误标
    2. 问题4（缺岗位核心字段仍打分）：
       - 仅公司名缺岗位名 → 标记基础信息残缺
       - scorer 对残缺/待复核岗位跳过 LLM 调用，incomplete/review_required 置位
    3. 问题5（打分缺联网检索补全）：
       - _clean / _parse_bing 纯函数解析
       - research_company 空公司/全失败时返回空串（打分侧降级标注）
       - 搜索引擎成功 + 百科失败时仍返回搜索摘要（不整体丢弃）
    4. 链路（重大问题6 修复）：LLM 配置直连 DeepSeek 官方地址

运行方式：python tests/test_module7_problem_fixes.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# 保证可直接从项目根目录运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.matcher.scorer import JobScorer  # noqa: E402
from job_assistant.multimodal.web_fetcher import WebFetchError  # noqa: E402
from job_assistant.parser.extractor import JobExtractor  # noqa: E402
from job_assistant.parser.schema import MISSING, JobPosting, MessageCategory  # noqa: E402
from job_assistant.config.profile import UserProfile  # noqa: E402
from job_assistant.research.company_research import (  # noqa: E402
    _clean,
    _parse_bing,
    research_company,
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


def make_posting(**kw) -> JobPosting:
    """构造完整可打分岗位（默认值齐全，供单字段覆盖测试）。"""
    defaults = dict(
        company_name="XX科技有限公司",
        job_title="Java 后端开发工程师",
        location="西安",
        education="本科及以上",
        tech_stack="Java, Spring Boot, MySQL",
        apply_method="校招官网投递",
        deadline="2026-10-01",
        source="校招官网",
        category=MessageCategory.RECRUITMENT,
    )
    defaults.update(kw)
    return JobPosting(**defaults)


# ============ 问题1：宣讲会地点误填工作地点 ============

print("\n== 问题1：地点字段区分校验 ==")


def test_lecture_venue_value_level() -> None:
    # a) location 值本身含宣讲特征词 → 标记复核
    p = make_posting(location="西安电子科技大学长安校区（宣讲会地点）")
    JobExtractor._apply_quality_checks(p, "【校招】XX科技招聘 Java 后端，工作地点西安。宣讲会于长安校区举行。")
    check("值级：location 含'宣讲会'标记复核", bool(p.review_reason), p.review_reason)
    check("值级：宣讲地点禁止写入工作地点", p.location == MISSING, p.location)
    check("值级：原地点迁移至 apply_method", "西安电子科技大学长安校区" in p.apply_method, p.apply_method)
    check("值级：不标记残缺", p.incomplete_reason == "", p.incomplete_reason)


def test_lecture_venue_context_level() -> None:
    # b) 上下文：原文含宣讲关键词，且 location 与其同段（±40 字符）→ 标记复核
    text = "【线下宣讲】XX科技将举办校园宣讲会，宣讲地点：西安电子科技大学长安校区。招聘 Java 后端工程师。"
    p = make_posting(location="西安电子科技大学长安校区")
    JobExtractor._apply_quality_checks(p, text)
    check("上下文：location 与宣讲信息同段标记复核", bool(p.review_reason), p.review_reason)
    check("上下文：复核原因含'宣讲'", "宣讲" in p.review_reason, p.review_reason)
    check("上下文：宣讲地点禁止写入工作地点", p.location == MISSING, p.location)
    check("上下文：原地点迁移至 apply_method", "西安电子科技大学长安校区" in p.apply_method, p.apply_method)


def test_lecture_venue_not_same_segment() -> None:
    # c) 原文含宣讲关键词，但 location 不在宣讲同段 → 不误标
    text = ("XX科技明日将在 A 校举行校园宣讲会。"
            "岗位信息：Java 后端开发工程师，工作地点在深圳市南山区科技园，"
            "五险一金，双休。")
    p = make_posting(location="深圳市南山区科技园")
    JobExtractor._apply_quality_checks(p, text)
    check("上下文：正常地点不误标", p.review_reason == "", p.review_reason)


def test_normal_location() -> None:
    p = make_posting(location="杭州西湖区")
    JobExtractor._apply_quality_checks(p, "XX科技招聘 Java 后端，工作地点杭州西湖区。")
    check("正常：普通工作地点不标记", p.review_reason == "", p.review_reason)


def test_apply_quality_checks_via_extract() -> None:
    # d) 通过 extract 全链路：FakeLLM 返回误填地点的抽取结果
    class FakeLLM:
        def __init__(self, response: str) -> None:
            self.response = response
        def complete(self, prompt: str, system: str = "", *, temperature: float = 0.2) -> str:
            return self.response

    llm = FakeLLM(
        '{"company_name": "厦门松霖机器人", "job_title": "机器人算法工程师", '
        '"location": "西安电子科技大学长安校区（宣讲会地点）", "education": "本科", '
        '"tech_stack": "C++", "apply_method": "现场投递", "deadline": "2026-09-25"}'
    )
    posting = JobExtractor(llm).extract(
        "【校招】厦门松霖机器人宣讲会：地点西安电子科技大学长安校区，招聘机器人算法工程师",
    )
    check("extract 链路：location 冲突标记复核", bool(posting.review_reason), posting.review_reason)
    check("extract 链路：宣讲地点禁止写入工作地点", posting.location == MISSING, posting.location)
    check("extract 链路：原地点迁移至 apply_method", "西安电子科技大学长安校区" in posting.apply_method, posting.apply_method)


# ============ 问题4：缺岗位核心字段仍打分 ============

print("\n== 问题4：基础信息残缺前置校验 ==")


def test_incomplete_missing_job_title() -> None:
    p = make_posting(job_title=MISSING)
    JobExtractor._apply_quality_checks(p, "XX科技来校招聘。")
    check("残缺：缺岗位名标记 incomplete_reason", "岗位名称" in p.incomplete_reason, p.incomplete_reason)


def test_incomplete_missing_company() -> None:
    p = make_posting(company_name=MISSING)
    JobExtractor._apply_quality_checks(p, "急招 Java 后端，双休不加班。")
    check("残缺：缺公司名标记 incomplete_reason", "公司名称" in p.incomplete_reason, p.incomplete_reason)


def test_scorer_skips_incomplete_without_llm() -> None:
    class BoomLLM:
        def complete(self, *a, **kw):
            raise AssertionError("残缺岗位不应调用 LLM")

    scorer = JobScorer(BoomLLM(), UserProfile(resume="211 本科", intention="后端开发"), threshold=80)
    p = make_posting(job_title=MISSING, incomplete_reason="缺少岗位名称")
    r = scorer.score(p)
    check("scorer：残缺岗位跳过 LLM", r.incomplete is True, str(r))
    check("scorer：残缺岗位不推荐", r.is_recommended is False, str(r))
    check("scorer：残缺岗位分数为 0", r.score == 0, str(r.score))


def test_scorer_skips_review_without_llm() -> None:
    class BoomLLM:
        def complete(self, *a, **kw):
            raise AssertionError("待复核岗位不应调用 LLM")

    scorer = JobScorer(BoomLLM(), UserProfile(resume="211 本科", intention="后端开发"), threshold=80)
    p = make_posting(review_reason="工作地点疑似宣讲/招聘场地，需人工复核")
    r = scorer.score(p)
    check("scorer：待复核岗位跳过 LLM", r.review_required is True, str(r))
    check("scorer：待复核岗位不推荐", r.is_recommended is False, str(r))


# ============ 问题5：联网检索补全 ============

print("\n== 问题5：公司外部资料联网检索 ==")

_BING_SAMPLE = (
    '<li class="b_algo"><h2 class=""><a target="_blank" href="https://example.com/a">'
    "某公司 <strong>风评</strong> 怎么样</a></h2>"
    '<div class="b_caption"><p class="b_lineclamp2">加班强度中等，<strong>双休</strong>。</p></div></li>'
    '<li class="b_algo"><h2><a target="_blank" href="https://www.qq.com">'
    "无关结果</a></h2>"
    '<div class="b_caption"><p>内容</p></div></li>'
)


def test_clean_strips_tags() -> None:
    check("clean：去除 HTML 标签", _clean("<strong>双休</strong>") == "双休", _clean("<strong>双休</strong>"))
    check("clean：压缩空白", _clean("  a   b  ") == "a b", _clean("  a   b  "))


def test_parse_bing() -> None:
    items = _parse_bing(_BING_SAMPLE)
    check("parse_bing：解析出 2 条", len(items) == 2, str(len(items)))
    check("parse_bing：标题去标签", items[0]["title"] == "某公司 风评 怎么样", str(items))
    check("parse_bing：摘要去标签", "加班强度中等" in items[0]["snippet"], str(items))
    check("parse_bing：链接保留", items[0]["url"] == "https://example.com/a", str(items))


def test_research_empty_company() -> None:
    check("research：空公司名返回空", research_company("") == "", "非空!")
    check("research：MISSING 返回空", research_company(MISSING) == "", "非空!")


def test_research_all_fail_degrades() -> None:
    with patch("job_assistant.research.company_research._search_web", return_value=[]):
        with patch(
            "job_assistant.research.company_research.fetch_web_page",
            side_effect=WebFetchError("网络错误: 模拟失败"),
        ):
            r = research_company("某公司", timeout=1.0)
    check("research：全部失败返回空串（打分侧降级标注）", r == "", repr(r))


def test_research_search_ok_baike_fail() -> None:
    with patch(
        "job_assistant.research.company_research._search_web",
        return_value=[{"title": "某公司 工作体验", "url": "https://example.com/x", "snippet": "加班少，双休"}],
    ):
        with patch(
            "job_assistant.research.company_research.fetch_web_page",
            side_effect=WebFetchError("网络错误: 模拟失败"),
        ):
            r = research_company("某公司", timeout=1.0, max_chars=500)
    check("research：搜索成功百科失败仍返回摘要", "搜索引擎检索结果" in r and "加班少" in r, repr(r))


def test_research_max_chars_truncate() -> None:
    with patch(
        "job_assistant.research.company_research._search_web",
        return_value=[{"title": "标题", "url": "https://example.com/x", "snippet": "内容" * 500}],
    ):
        with patch(
            "job_assistant.research.company_research.fetch_web_page",
            side_effect=WebFetchError("网络错误: 模拟失败"),
        ):
            r = research_company("某公司", timeout=1.0, max_chars=100)
    check("research：截断生效", len(r) <= 100, str(len(r)))




# ============ 问题4增强：残缺字段自动搜索补全 ============

print("\n== 问题4增强：残缺字段自动搜索补全 ==")


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
    def complete(self, prompt: str, system: str = "", *, temperature: float = 0.2) -> str:
        return self.response


def test_enrich_fills_job_title() -> None:
    from job_assistant.research.company_research import enrich_missing_fields
    p = make_posting(job_title=MISSING, incomplete_reason="缺少岗位名称")
    with patch(
        "job_assistant.research.company_research._search_smcn",
        return_value=[{"title": "XX科技 校招 Java后端工程师", "url": "https://x.com/jobs",
                       "snippet": "招聘 Java 后端开发工程师，双休不加班"}],
    ):
        r = enrich_missing_fields(
            "XX科技来校招聘，有后端开发岗位。", p, _FakeLLM(
                '{"company_name": "【信息未提及】", "job_title": "Java后端开发工程师"}'
            ), timeout=1.0,
        )
    check("enrich：补全岗位名", r.get("job_title") == "Java后端开发工程师", str(r))
    check("enrich：残缺标记清空", p.incomplete_reason == "", p.incomplete_reason)
    check("enrich：返回搜索资料含风评", "双休不加班" in r.get("external_info", ""), str(r)[:80])


def test_enrich_fills_company() -> None:
    from job_assistant.research.company_research import enrich_missing_fields
    p = make_posting(company_name=MISSING, incomplete_reason="缺少公司名称")
    with patch(
        "job_assistant.research.company_research._search_smcn",
        return_value=[{"title": "深圳云海科技 校招后端", "url": "https://yh.com/j",
                       "snippet": "云海科技招聘 Java 后端，弹性工作"}],
    ):
        r = enrich_missing_fields(
            "急招 Java 后端，双休。", p, _FakeLLM(
                '{"company_name": "深圳云海科技有限公司", "job_title": "【信息未提及】"}'
            ), timeout=1.0,
        )
    check("enrich：补全公司名", r.get("company_name") == "深圳云海科技有限公司", str(r))
    check("enrich：残缺标记清空", p.incomplete_reason == "", p.incomplete_reason)


def test_enrich_no_results_keeps_incomplete() -> None:
    from job_assistant.research.company_research import enrich_missing_fields
    p = make_posting(job_title=MISSING, incomplete_reason="缺少岗位名称")
    with patch("job_assistant.research.company_research._search_smcn", return_value=[]), \
            patch("job_assistant.research.company_research._search_baidu", return_value=[]):
        r = enrich_missing_fields("XX科技来校招聘。", p, _FakeLLM("{}"), timeout=1.0)
    check("enrich：搜索无结果返回空", r == {}, repr(r))
    check("enrich：保持残缺标记", p.incomplete_reason == "缺少岗位名称", p.incomplete_reason)


def test_enrich_llm_unknown_keeps_incomplete() -> None:
    from job_assistant.research.company_research import enrich_missing_fields
    p = make_posting(job_title=MISSING, incomplete_reason="缺少岗位名称")
    with patch(
        "job_assistant.research.company_research._search_smcn",
        return_value=[{"title": "结果A", "url": "https://x.com", "snippet": "无关内容"}],
    ):
        r = enrich_missing_fields(
            "XX科技来校招聘。", p, _FakeLLM(
                '{"company_name": "【信息未提及】", "job_title": "【信息未提及】"}'
            ), timeout=1.0,
        )
    check("enrich：LLM 无法确认返回空", r == {}, repr(r))
    check("enrich：保持残缺标记", p.incomplete_reason == "缺少岗位名称", p.incomplete_reason)


def test_enrich_skip_when_complete() -> None:
    from job_assistant.research.company_research import enrich_missing_fields
    p = make_posting()
    r = enrich_missing_fields("完整岗位信息", p, _FakeLLM("{}"), timeout=1.0)
    check("enrich：字段齐全不触发", r == {}, repr(r))


def test_enrich_llm_failure_keeps_incomplete() -> None:
    from job_assistant.research.company_research import enrich_missing_fields
    class BoomLLM:
        def complete(self, *a, **kw):
            raise RuntimeError("模拟 LLM 故障")
    p = make_posting(job_title=MISSING, incomplete_reason="缺少岗位名称")
    with patch(
        "job_assistant.research.company_research._search_smcn",
        return_value=[{"title": "结果A", "url": "https://x.com", "snippet": "内容"}],
    ):
        r = enrich_missing_fields("XX科技来校招聘。", p, BoomLLM(), timeout=1.0)
    check("enrich：LLM 故障返回空", r == {}, repr(r))
    check("enrich：保持残缺标记", p.incomplete_reason == "缺少岗位名称", p.incomplete_reason)



# ============ 重大问题6 修复：直连 DeepSeek 官方地址 ============

print("\n== 链路：DeepSeek 官方直连配置 ==")


def test_llm_direct_deepseek_config() -> None:
    from job_assistant.config.settings import LLMConfig, _parse_llm
    cfg = _parse_llm({"base_url": "https://api.deepseek.com/v1", "api_key": "sk-test", "model": "deepseek-chat"})
    check("配置：base_url 为官方地址", cfg.base_url == "https://api.deepseek.com/v1", cfg.base_url)
    default = LLMConfig()
    check("默认：base_url 为官方地址", default.base_url == "https://api.deepseek.com/v1", default.base_url)


# ============ 入口 ============

def main() -> None:
    print("测试开始（问题修复回归）")
    tests = [
        test_lecture_venue_value_level,
        test_lecture_venue_context_level,
        test_lecture_venue_not_same_segment,
        test_normal_location,
        test_apply_quality_checks_via_extract,
        test_incomplete_missing_job_title,
        test_incomplete_missing_company,
        test_scorer_skips_incomplete_without_llm,
        test_scorer_skips_review_without_llm,
        test_clean_strips_tags,
        test_parse_bing,
        test_research_empty_company,
        test_research_all_fail_degrades,
        test_research_search_ok_baike_fail,
        test_research_max_chars_truncate,
        test_enrich_fills_job_title,
        test_enrich_fills_company,
        test_enrich_no_results_keeps_incomplete,
        test_enrich_llm_unknown_keeps_incomplete,
        test_enrich_skip_when_complete,
        test_enrich_llm_failure_keeps_incomplete,
        test_llm_direct_deepseek_config,
    ]
    for t in tests:
        t()
    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    if _FAIL:
        print("存在失败用例，请检查！")
        sys.exit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    main()