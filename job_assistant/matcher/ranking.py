# job_assistant/matcher/ranking.py

"""岗位打分排序工具（模块五）：按匹配分数降序排列。

业务对应（产品方案 4.6 日报规则 / ARCHITECTURE.md 模块五）：
    - 每日日报只读取推荐岗位（≥ 阈值），按匹配分数降序排列
    - 排序为稳定排序：同分岗位保持原始顺序（先解析到的在前）
"""

from __future__ import annotations

from .scorer import MatchResult


def rank_by_score(results: list[MatchResult]) -> list[MatchResult]:
    """按匹配分数降序稳定排序（返回新列表，不修改入参）。

    Args:
        results: 一个时间窗内的打分结果（含失败标记项）。

    Returns:
        按 score 降序的新列表；失败项（has_error）固定排在末尾、
        保持原始相对顺序，避免无意义打分参与排序比较。
    """
    failed = [r for r in results if r.has_error]
    scored = [r for r in results if not r.has_error]
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored + failed
