# job_assistant/parser/__init__.py

"""模块四：AI 岗位结构化抽取（parser 包）。

对外暴露：消息分类（MessageClassifier）、结构化抽取编排（JobExtractor）、
岗位字段模型（JobPosting）、消息类别（MessageCategory）、LLM 客户端（LLMClient/HttpLLMClient）。
"""

from .classifier import MessageClassifier
from .extractor import JobExtractor, ParseResult
from .llm_client import HttpLLMClient, LLMClient
from .schema import MISSING, JobPosting, MessageCategory

__all__ = [
    "MessageClassifier",
    "JobExtractor",
    "ParseResult",
    "HttpLLMClient",
    "LLMClient",
    "MISSING",
    "JobPosting",
    "MessageCategory",
]
