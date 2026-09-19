# job_assistant/matcher/__init__.py

"""模块五：岗位匹配打分（matcher 包）。

对外暴露：打分器（JobScorer）、打分结果（MatchResult）、排序工具（rank_by_score）。
"""

from .ranking import rank_by_score
from .scorer import JobScorer, MatchResult

__all__ = [
    "JobScorer",
    "MatchResult",
    "rank_by_score",
]
