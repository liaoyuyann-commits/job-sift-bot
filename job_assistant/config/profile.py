# job_assistant/config/profile.py

"""用户简历 & 求职意向配置（模块三）。

业务对应（产品方案 4.3 / ARCHITECTURE.md 模块三）：
    - YAML 配置 user_resume（简历全文）与 user_intention（求职意向文本）
    - 程序不预置任何固定标签与权重，完整文本原样拼接后作为模块五打分的输入
    - 用户随时修改，最新简历与意向在配置加载后即时生效（V1.0 重启生效）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..errors import ConfigError

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    """用户画像：简历全文 + 求职意向全文（模块五岗位匹配打分的输入）。

    设计决策（对应 ARCHITECTURE.md 关键设计决策"用户画像"）：
        - 只保存用户输入文本，不抽取任何固定标签，不设权重
        - resume / intention 均可为空：允许先配置监听群、后补简历意向
    """

    resume: str = ""        # 用户粘贴的简历全文（多行文本）
    intention: str = ""     # 求职意向（意向岗位/城市/可接受行业/硬性排除等）

    @property
    def has_resume(self) -> bool:
        """是否已填写简历。"""
        return bool(self.resume.strip())

    @property
    def has_intention(self) -> bool:
        """是否已填写求职意向。"""
        return bool(self.intention.strip())

    @property
    def is_configured(self) -> bool:
        """是否已配置画像（简历或意向至少一项非空）。"""
        return self.has_resume or self.has_intention

    def full_text(self) -> str:
        """拼接为供 LLM 使用的完整画像文本（模块五打分输入之一）。

        仅拼接非空段落；【用户简历】/【用户求职意向】为分段标记，
        帮助 LLM 理解输入结构，不属于预置的用户画像标签。
        """
        parts: list[str] = []
        if self.has_resume:
            parts.append(f"【用户简历】\n{self.resume.strip()}")
        if self.has_intention:
            parts.append(f"【用户求职意向】\n{self.intention.strip()}")
        return "\n\n".join(parts)


def _parse_text_field(raw: Any, key: str) -> str:
    """解析字符串配置字段：仅接受字符串，拒绝数字/列表/映射（避免静默强转）。"""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ConfigError(f"{key} 必须是字符串，实际为 {type(raw).__name__}")
    # 保留内部换行与缩进，仅去除首尾空白
    return raw.strip()


def parse_profile(root: dict) -> UserProfile:
    """从 YAML 根节点解析用户画像（模块三）。

    Args:
        root: load_config 反序列化后的配置根节点（dict）。

    Returns:
        UserProfile：resume / intention 为去除首尾空白的原始文本。

    Raises:
        ConfigError: user_resume / user_intention 类型非字符串（错误码 CFG.LOAD.001）。
    """
    resume = _parse_text_field(root.get("user_resume"), "user_resume")
    intention = _parse_text_field(root.get("user_intention"), "user_intention")
    profile = UserProfile(resume=resume, intention=intention)

    # 敏感信息保护（日志规范）：简历/意向全文可能含手机号等隐私，
    # 日志只记录长度，绝不打印内容
    if not profile.has_resume:
        logger.warning("user_resume 为空：后续岗位打分将缺少简历参考")
    if not profile.has_intention:
        logger.warning("user_intention 为空：后续岗位打分将缺少求职意向参考")
    logger.info(
        "用户画像加载完成: resume_len=%d intention_len=%d",
        len(profile.resume), len(profile.intention),
    )
    return profile
