# job_assistant/parser/schema.py

"""模块四（AI 岗位结构化抽取）数据模型：消息类别 + 结构化岗位字段。

业务对应（产品方案 4.4 / ARCHITECTURE.md 模块四）：
    - 输出字段：公司名称、岗位名称、工作地点、学历要求、技术栈要求、
      投递方式、截止时间、信息来源
    - 容错设计：字段允许为空，缺失字段标记【信息未提及】，不强行补全
    - 岗位 JSON 字段一次性定义完整（to_dict），兼容 V2.0 Web UI 读取，
      后续不改底层存储格式（产品方案 V1.4）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# 缺失字段统一标记（产品方案 4.4：缺失字段标记【信息未提及】）
MISSING = "【信息未提及】"


class MessageCategory(Enum):
    """消息分类（产品方案 2.2-4：区分宣讲/招聘/内推/闲聊广告/诈骗）。

    OTHER 为兜底类别：LLM 输出无法映射到 5 类时归入，避免强猜误伤。
    """

    LECTURE = "lecture"            # 宣讲通知
    RECRUITMENT = "recruitment"    # 正式招聘
    REFERRAL = "referral"          # 内推信息
    SPAM = "spam"                  # 闲聊广告
    FRAUD = "fraud"                # 诈骗信息
    OTHER = "other"                # 无法识别（兜底）

    @property
    def label(self) -> str:
        """中文展示名（控制台/日报展示用）。"""
        return {
            MessageCategory.LECTURE: "宣讲通知",
            MessageCategory.RECRUITMENT: "正式招聘",
            MessageCategory.REFERRAL: "内推信息",
            MessageCategory.SPAM: "闲聊广告",
            MessageCategory.FRAUD: "诈骗信息",
            MessageCategory.OTHER: "其他",
        }[self]

    @property
    def is_job_related(self) -> bool:
        """是否招聘相关（宣讲/招聘/内推）→ 可进入结构化抽取。

        闲聊广告 / 诈骗 / 其他 不抽取岗位，避免把无效信息送入打分链路。
        """
        return self in (
            MessageCategory.LECTURE,
            MessageCategory.RECRUITMENT,
            MessageCategory.REFERRAL,
        )


def normalize_category(value: Any) -> MessageCategory:
    """把 LLM 输出归一化为 MessageCategory。

    容错规则（LLM 输出不可控）：
        - 已是枚举 / 枚举值字符串（"recruitment"）→ 直接映射
        - 中文字面量（"正式招聘"）→ 按 label 反查
        - 无法识别 → OTHER（兜底，不抛异常）
    """
    if isinstance(value, MessageCategory):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        for category in MessageCategory:
            if category.value == text or category.label == value.strip():
                return category
    return MessageCategory.OTHER


def _field_text(value: Any) -> str:
    """把 LLM 抽取的字段值清洗为字符串：空值统一标记【信息未提及】。"""
    if value is None:
        return MISSING
    if isinstance(value, (list, tuple)):
        # 技术栈等多值字段：LLM 可能输出数组，统一用顿号拼接
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(parts) if parts else MISSING
    text = str(value).strip()
    return text if text else MISSING


@dataclass
class JobPosting:
    """结构化岗位数据（模块四输出 → 模块五打分 / 模块六归档）。

    字段设计（产品方案 V1.4：一次性定义完整，兼容 V2.0 UI）：
        - 8 个业务字段：与产品方案 4.4 输出字段一一对应
        - category：消息类别（区分宣讲/招聘/内推，日报展示用）
        - group_id / message_id：消息溯源（V2.0 UI 可回溯原始消息，不参与 LLM 抽取）

    容错设计：任何业务字段缺失时保持默认值【信息未提及】，不强行补全。
    """

    company_name: str = MISSING      # 公司名称
    job_title: str = MISSING         # 岗位名称
    location: str = MISSING          # 工作地点
    education: str = MISSING         # 学历要求
    tech_stack: str = MISSING        # 技术栈要求
    apply_method: str = MISSING      # 投递方式（渠道/邮箱/链接）
    deadline: str = MISSING          # 截止时间
    source: str = MISSING            # 信息来源（LLM 抽取，如"校招官网"）
    category: MessageCategory = MessageCategory.RECRUITMENT  # 消息类别
    group_id: int = 0                # 溯源：来源 QQ 群号
    message_id: int = 0              # 溯源：来源消息 ID
    # 质量标记（业务层抽取后校验产生，不参与 LLM 抽取；不写入 to_dict 岗位结构，
    # 由模块六归档时附加到存档记录，V2.0 UI 可直接读取展示）
    incomplete_reason: str = ""      # 基础信息残缺原因（缺公司名/岗位名）；非空=残缺，跳过自动打分
    review_reason: str = ""          # 待人工复核原因（如工作地点疑似宣讲场地）；非空=复核，跳过自动打分

    def to_dict(self) -> dict[str, Any]:
        """输出完整岗位字典（模块六 JSON 全量存档 / V2.0 UI 读取）。

        字段名保持稳定：后续新增 UI 模块直接复用本结构，不改底层格式。
        """
        return {
            "company_name": self.company_name,
            "job_title": self.job_title,
            "location": self.location,
            "education": self.education,
            "tech_stack": self.tech_stack,
            "apply_method": self.apply_method,
            "deadline": self.deadline,
            "source": self.source,
            "category": self.category.value,
            "group_id": self.group_id,
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobPosting":
        """从字典恢复岗位对象（读回存档 JSON 用，容错缺失字段）。

        仅识别已知字段；未知字段忽略（兼容未来扩展，不报错）。
        """
        data = data or {}
        return cls(
            company_name=_field_text(data.get("company_name")),
            job_title=_field_text(data.get("job_title")),
            location=_field_text(data.get("location")),
            education=_field_text(data.get("education")),
            tech_stack=_field_text(data.get("tech_stack")),
            apply_method=_field_text(data.get("apply_method")),
            deadline=_field_text(data.get("deadline")),
            source=_field_text(data.get("source")),
            category=normalize_category(data.get("category")),
            group_id=int(data.get("group_id") or 0),
            message_id=int(data.get("message_id") or 0),
            # 质量标记：从存档记录恢复（容错缺失，未知字段忽略）
            incomplete_reason=str(data.get("incomplete_reason") or ""),
            review_reason=str(data.get("review_reason") or ""),
        )

    def brief(self) -> str:
        """极简摘要（控制台日志用，避免打印岗位全文占用日志空间）。

        形如：公司/岗位（类别）。
        """
        return f"{self.company_name}/{self.job_title}（{self.category.label}）"
