# job_assistant/storage/__init__.py

"""模块六：本地数据归档与日报（storage 包）。

职责（对应 ARCHITECTURE.md 模块六）：
    - 岗位去重归档：基于「公司名称 + 岗位名称」去重，全量 JSON 永久存档
    - 每日日报：读取当日推荐岗位（≥ 阈值），按分数降序输出 Markdown

对外暴露：岗位归档仓储（JobRepository）、每日日报生成器（DailyReport）、
日报结果（ReportResult）。
"""

from .repository import JobRepository
from .report import DailyReport, ReportResult

__all__ = [
    "JobRepository",
    "DailyReport",
    "ReportResult",
]
