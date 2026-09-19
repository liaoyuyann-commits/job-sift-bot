# tests/test_module4_parser.py

"""模块四（AI 岗位结构化抽取模块）自测脚本。

覆盖范围：
    1. schema：MISSING 标记、JobPosting 默认值 / to_dict / from_dict、字段清洗
    2. MessageCategory：label / is_job_related / normalize_category 容错
    3. classifier：FakeLLM 注入全部分类、未知值兜底 OTHER、LLM 失败抛异常
    4. extractor：成功抽取（含代码块剥离）、缺失字段、格式非法重试、parse 编排容错
    5. settings：llm 配置段加载与校验、load_config 集成

运行方式：python tests/test_module4_parser.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 保证可直接从项目根目录运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.errors import ConfigError, LLMCallError, LLMFormatError  # noqa: E402
from job_assistant.parser.classifier import MessageClassifier  # noqa: E402
from job_assistant.parser.extractor import JobExtractor, extract_json_object  # noqa: E402
from job_assistant.parser.schema import MISSING, JobPosting, MessageCategory, normalize_category  # noqa: E402
from job_assistant.config.settings import load_config  # noqa: E402

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


# ---------- 1. schema ----------

def test_schema() -> None:
    print("== schema: JobPosting / MISSING ==")

    # 1. MISSING 标记
    check("MISSING 为【信息未提及】", MISSING == "【信息未提及】")

    # 2. 默认岗位所有业务字段为 MISSING
    job = JobPosting()
    check("默认字段均为【信息未提及】",
          job.company_name == MISSING and job.job_title == MISSING
          and job.location == MISSING and job.education == MISSING
          and job.tech_stack == MISSING and job.apply_method == MISSING
          and job.deadline == MISSING and job.source == MISSING)
    check("默认类别为正式招聘", job.category == MessageCategory.RECRUITMENT)

    # 3. to_dict 字段完整（兼容 V2.0 UI 读取）
    job = JobPosting(company_name="字节", job_title="后端", group_id=111, message_id=222)
    data = job.to_dict()
    check("to_dict 含 8 业务字段", all(k in data for k in (
        "company_name", "job_title", "location", "education",
        "tech_stack", "apply_method", "deadline", "source")))
    check("to_dict 含类别与溯源", data["category"] == "recruitment"
          and data["group_id"] == 111 and data["message_id"] == 222)

    # 4. from_dict 往返
    restored = JobPosting.from_dict(data)
    check("from_dict 往返一致",
          restored.company_name == "字节" and restored.job_title == "后端"
          and restored.group_id == 111 and restored.message_id == 222
          and restored.category == MessageCategory.RECRUITMENT)

    # 5. from_dict 缺失字段容错 / 多余字段忽略
    restored = JobPosting.from_dict({"company_name": "腾讯"})
    check("from_dict 缺失字段标 MISSING", restored.location == MISSING and restored.deadline == MISSING)
    check("from_dict 忽略未知字段", JobPosting.from_dict({"future_field": 1}).job_title == MISSING)

    # 6. 列表字段清洗为顿号拼接（LLM 可能输出数组）
    job = JobPosting.from_dict({"tech_stack": ["Python", "Java"]})
    check("列表字段顿号拼接", job.tech_stack == "Python、Java")
    check("空列表标 MISSING", JobPosting.from_dict({"tech_stack": []}).tech_stack == MISSING)

    # 7. brief 摘要
    job = JobPosting(company_name="华为", job_title="OD 开发")
    check("brief 摘要格式", job.brief() == "华为/OD 开发（正式招聘）")


# ---------- 2. MessageCategory ----------

def test_category() -> None:
    print("== MessageCategory ==")

    check("招聘相关: 宣讲/招聘/内推",
          MessageCategory.LECTURE.is_job_related
          and MessageCategory.RECRUITMENT.is_job_related
          and MessageCategory.REFERRAL.is_job_related)
    check("非招聘相关: 闲聊/诈骗/其他",
          not MessageCategory.SPAM.is_job_related
          and not MessageCategory.FRAUD.is_job_related
          and not MessageCategory.OTHER.is_job_related)
    check("中文 label", MessageCategory.RECRUITMENT.label == "正式招聘")

    # normalize_category 容错
    check("枚举直通", normalize_category(MessageCategory.SPAM) == MessageCategory.SPAM)
    check("值字符串映射", normalize_category("recruitment") == MessageCategory.RECRUITMENT)
    check("大写容忍", normalize_category("REFERRAL") == MessageCategory.REFERRAL)
    check("中文字面量映射", normalize_category("正式招聘") == MessageCategory.RECRUITMENT)
    check("未知值兜底 OTHER", normalize_category("whatever") == MessageCategory.OTHER)
    check("None 兜底 OTHER", normalize_category(None) == MessageCategory.OTHER)


# ---------- 3. classifier ----------

def test_classifier() -> None:
    print("== classifier: 消息分类 ==")

    for value, category in [
        ("lecture", MessageCategory.LECTURE),
        ("recruitment", MessageCategory.RECRUITMENT),
        ("referral", MessageCategory.REFERRAL),
        ("spam", MessageCategory.SPAM),
        ("fraud", MessageCategory.FRAUD),
    ]:
        llm = FakeLLM(f'{{"category": "{value}"}}')
        result = MessageClassifier(llm).classify("测试消息")
        check(f"分类 {value} 正确", result == category)

    # 代码块包装
    llm = FakeLLM('```json\n{"category": "recruitment"}\n```')
    check("代码块包装解析", MessageClassifier(llm).classify("x") == MessageCategory.RECRUITMENT)

    # 无法解析 → OTHER（不抛异常）
    llm = FakeLLM("我不知道这是什么")
    check("非 JSON 兜底 OTHER", MessageClassifier(llm).classify("x") == MessageCategory.OTHER)

    # LLM 服务故障 → 抛 LLMCallError
    llm = FakeLLM(LLMCallError("网络错误"))
    try:
        MessageClassifier(llm).classify("x")
        check("LLM 故障抛 LLMCallError", False)
    except LLMCallError:
        check("LLM 故障抛 LLMCallError", True)


# ---------- 4. extractor ----------

_EXTRACT_OK = (
    '{"company_name": "美团", "job_title": "Java 开发工程师", "location": "北京", '
    '"education": "本科", "tech_stack": ["Java", "Spring"], "apply_method": "校招官网投递", '
    '"deadline": "2026-10-31", "source": "校招官网"}'
)


def test_extractor() -> None:
    print("== extractor: 结构化抽取 ==")

    # 1. extract 成功：字段正确 + 列表拼接 + 溯源
    llm = FakeLLM(_EXTRACT_OK)
    job = JobExtractor(llm).extract("文本", group_id=11, message_id=22)
    check("抽取公司名", job.company_name == "美团")
    check("抽取岗位名", job.job_title == "Java 开发工程师")
    check("列表字段拼接", job.tech_stack == "Java、Spring")
    check("溯源写入", job.group_id == 11 and job.message_id == 22)

    # 2. 缺失字段标 MISSING
    llm = FakeLLM('{"company_name": "腾讯"}')
    job = JobExtractor(llm).extract("文本")
    check("缺失字段标 MISSING", job.location == MISSING and job.deadline == MISSING)

    # 3. 代码块包装抽取成功
    llm = FakeLLM('```json\n' + _EXTRACT_OK + '\n```')
    job = JobExtractor(llm).extract("文本")
    check("代码块包装抽取", job.company_name == "美团")

    # 4. JSON 解析失败 → LLMFormatError
    llm = FakeLLM("抱歉，我无法解析")
    try:
        JobExtractor(llm).extract("文本")
        check("非法输出抛 LLMFormatError", False)
    except LLMFormatError:
        check("非法输出抛 LLMFormatError", True)

    # 5. parse 编排：招聘相关 → job 非空
    llm = FakeLLM('{"category": "recruitment"}', _EXTRACT_OK)
    result = JobExtractor(llm).parse("文本", group_id=1, message_id=2)
    check("parse 招聘类产出岗位", result.job is not None and result.job.company_name == "美团")
    check("parse 无错误", not result.has_error)
    check("岗位类别回填", result.job.category == MessageCategory.RECRUITMENT)

    # 6. parse 编排：闲聊 → 不抽取（仅一次 LLM 调用）
    llm = FakeLLM('{"category": "spam"}')
    result = JobExtractor(llm).parse("闲聊文本")
    check("parse 闲聊不抽取", result.job is None and not result.has_error)
    check("parse 闲聊仅 1 次调用", len(llm.calls) == 1)

    # 7. parse 编排：LLM 调用失败 → error 非空（不阻断）
    llm = FakeLLM(LLMCallError("服务不可用"))
    result = JobExtractor(llm).parse("文本")
    check("parse LLM 失败标记错误", result.has_error and "PAR.LLM.001" in result.error)

    # 8. parse 编排：抽取格式非法 → 重试一次后成功（retried=True）
    llm = FakeLLM('{"category": "recruitment"}', "垃圾输出", _EXTRACT_OK)
    extractor = JobExtractor(llm, max_retries=1)
    result = extractor.parse("文本")
    check("parse 重试后成功", result.job is not None and result.retried and not result.has_error)
    check("parse 共 3 次调用（分类+2 次抽取）", len(llm.calls) == 3)

    # 9. parse 编排：重试仍失败 → error 非空
    llm = FakeLLM('{"category": "recruitment"}', "垃圾1", "垃圾2")
    result = JobExtractor(llm, max_retries=1).parse("文本")
    check("parse 重试后失败标记", result.has_error and "PAR.LLM.002" in result.error)

    # 10. extract_json_object 三种形态
    check("extract_json_object 纯 JSON",
          extract_json_object('{"a": 1}') == {"a": 1})
    check("extract_json_object 代码块",
          extract_json_object('```json\n{"a": 1}\n```') == {"a": 1})
    check("extract_json_object 内嵌 JSON",
          extract_json_object('结果如下：{"a": 1} 请查收') == {"a": 1})
    check("extract_json_object 无法提取返回 None",
          extract_json_object("没有 JSON") is None)


# ---------- 5. settings ----------

def test_llm_config() -> None:
    print("== settings: llm 配置段 ==")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. 完整 llm 段
        good = root / "good.yaml"
        good.write_text(
            "monitor_group_ids:\n  - 111\n"
            "llm:\n  base_url: \"http://127.0.0.1:9999/v1\"\n"
            "  api_key: \"sk-test\"\n  model: \"qwen-max\"\n  timeout: 30\n",
            encoding="utf-8",
        )
        cfg = load_config(good)
        check("llm 段加载", cfg.llm.base_url == "http://127.0.0.1:9999/v1"
              and cfg.llm.api_key == "sk-test" and cfg.llm.model == "qwen-max"
              and cfg.llm.timeout == 30.0)

        # 2. 旧配置缺省 llm 段 → 默认值
        old = root / "old.yaml"
        old.write_text("monitor_group_ids:\n  - 111\n", encoding="utf-8")
        cfg = load_config(old)
        check("缺省 llm 段用默认值",
              cfg.llm.base_url == "https://api.deepseek.com/v1" and cfg.llm.timeout == 60.0)

        # 3. base_url 非 http(s) → ConfigError
        bad = root / "bad_url.yaml"
        bad.write_text("llm:\n  base_url: \"ftp://x\"\n", encoding="utf-8")
        try:
            load_config(bad)
            check("非法 base_url 抛 ConfigError", False)
        except ConfigError:
            check("非法 base_url 抛 ConfigError", True)

        # 4. api_key 数字 → ConfigError（拒绝静默强转）
        bad = root / "bad_key.yaml"
        bad.write_text("llm:\n  api_key: 12345\n", encoding="utf-8")
        try:
            load_config(bad)
            check("数字 api_key 抛 ConfigError", False)
        except ConfigError:
            check("数字 api_key 抛 ConfigError", True)

        # 5. timeout 非正数 → ConfigError
        bad = root / "bad_timeout.yaml"
        bad.write_text("llm:\n  timeout: -5\n", encoding="utf-8")
        try:
            load_config(bad)
            check("非正 timeout 抛 ConfigError", False)
        except ConfigError:
            check("非正 timeout 抛 ConfigError", True)


def main() -> None:
    test_schema()
    test_category()
    test_classifier()
    test_extractor()
    test_llm_config()
    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
