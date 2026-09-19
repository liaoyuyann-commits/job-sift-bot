# job_assistant/research/__init__.py
"""公司外部信息联网检索（问题修复 5）：为岗位匹配打分补充公司背景、WLB、风评、经营信息。"""

from .company_research import research_company

__all__ = ["research_company"]
