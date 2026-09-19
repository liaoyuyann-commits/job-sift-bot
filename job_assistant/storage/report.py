# job_assistant/storage/report.py

"""模块六：每日汇总日报（Markdown，仅推荐岗位按分数降序）。

业务对应（产品方案 4.6 / ARCHITECTURE.md 模块六）：
    - 日报规则：Markdown 日报只读取推荐岗位（≥ 阈值），按匹配分数降序排列，
      附带分数、打分理由、信息来源；低分岗位不写入日报
    - 当日口径：读取 jobs 目录中「当日新增」岗位（文件名日期前缀），
      历史已归档岗位不重复进入当日日报
    - 生成时机：V1.0 手动启停、间歇运行，日报在每次运行结束时生成
      （读取当日全量存档）；同一日多次运行覆盖为最新日报

容错设计：
    - 排序复用模块五语义（分数降序、稳定）：存档记录按 score 降序排列
    - 日报生成/写入失败抛 ReportError（STO.REPORT.001），由 main 记录
      ERROR 后降级退出，不影响当日已归档岗位数据
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..errors import ReportError
from ..parser.schema import MISSING, normalize_category
from .repository import JobRepository

logger = logging.getLogger(__name__)


def _field(record: dict, key: str) -> str:
    """读取记录字段用于日报展示；空值统一回退【信息未提及】（容错旧数据）。"""
    value = record.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return MISSING
    return str(value)


def _score(record: dict) -> int:
    """读取分数用于降序排序；缺失/非法按 -1 排末尾（不抛异常）。"""
    try:
        return int(record.get("score", -1))
    except (TypeError, ValueError):
        return -1


@dataclass
class ReportResult:
    """日报生成结果：落盘路径 + 统计（main 日志/控制台展示用）。"""

    day: date                  # 日报日期
    markdown: str              # 完整 Markdown 正文
    path: Path                 # 落盘文件路径（report/{YYYY-MM-DD}.md）
    recommended_count: int     # 当日推荐岗位数（写入日报）
    archived_count: int        # 当日全量存档岗位数（推荐 + 仅存档）
    review_count: int = 0      # 当日待人工复核岗位数（问题修复 1）
    incomplete_count: int = 0  # 当日基础信息残缺岗位数（问题修复 4）


class DailyReport:
    """每日日报生成器：读取当日推荐岗位 → 降序 → Markdown 落盘。

    Args:
        report_dir: 日报输出目录（对应 config.data.report，不存在时自动创建）。
    """

    def __init__(self, report_dir: str | Path) -> None:
        self._dir = Path(report_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---------- 编排入口 ----------

    def generate(
        self,
        repository: JobRepository,
        *,
        day: date | None = None,
        threshold: int = 80,
    ) -> ReportResult:
        """一步完成当日日报：读取 → 排序 → 渲染 → 落盘。

        Args:
            repository: 岗位归档仓储（读取当日推荐岗位记录）。
            day: 日报日期；None 表示今天。
            threshold: 推荐分数阈值（仅用于日报头部展示，默认 80）。

        Returns:
            ReportResult：markdown 全文、落盘路径、推荐岗位数、全量存档数。

        Raises:
            ReportError: 日报写入失败（STO.REPORT.001）。
        """
        day = day or date.today()
        archived = repository.load_by_date(day)
        recommended = [r for r in archived if r.get("recommended")]
        # 质量标记岗位：独立板块展示（问题修复 1/4），不参与推荐排序
        review_required = [r for r in archived if r.get("review_required")]
        incomplete = [r for r in archived if r.get("incomplete")]
        # 分数降序稳定排序（同分保持归档时间顺序），与模块五 rank_by_score 语义一致
        recommended.sort(key=_score, reverse=True)

        markdown = self._render(
            recommended, review_required, incomplete, day, threshold, len(archived),
        )
        path = self.write(markdown, day)
        logger.info(
            "日报生成: %s 推荐=%d 复核=%d 残缺=%d 当日全量=%d",
            path.name, len(recommended), len(review_required),
            len(incomplete), len(archived),
        )
        return ReportResult(
            day=day,
            markdown=markdown,
            path=path,
            recommended_count=len(recommended),
            archived_count=len(archived),
            review_count=len(review_required),
            incomplete_count=len(incomplete),
        )

    # ---------- 内部实现 ----------

    def write(self, markdown: str, day: date) -> Path:
        """把日报 Markdown 写入 report 目录（{YYYY-MM-DD}.md）。

        Raises:
            ReportError: 磁盘写入失败（STO.REPORT.001）。
        """
        path = self._dir / f"{day.isoformat()}.md"
        try:
            path.write_text(markdown, encoding="utf-8")
        except OSError as e:
            raise ReportError(f"日报写入失败: {path} ({e})") from e
        logger.info(
            "日报已落盘: %s (%d 字节)", path.name, len(markdown.encode("utf-8")),
        )
        return path

    def _render(
        self,
        recommended: list[dict],
        review_required: list[dict],
        incomplete: list[dict],
        day: date,
        threshold: int,
        archived_count: int,
    ) -> str:
        """渲染日报 Markdown 正文（推荐岗位 + 待复核板块 + 残缺板块）。"""
        lines = [
            f"# 求职岗位日报（{day.isoformat()}）",
            "",
            f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"> 推荐岗位：{len(recommended)} 个（匹配分 ≥ 阈值 {threshold}，按分数降序）",
            f"> 待人工复核：{len(review_required)} 个 ｜ 基础信息残缺：{len(incomplete)} 个"
            + (
                "（详见文末板块）" if review_required or incomplete else ""
            ),
            "",
        ]
        if not recommended:
            lines.append(
                f"今日无推荐岗位（当日新增岗位 {archived_count} 个，均低于阈值或未打分）。"
            )
        else:
            for index, record in enumerate(recommended, start=1):
                lines.append(self._render_job(index, record))
        # 独立板块：待人工复核（问题修复 1：字段冲突不直接进入打分，需人工确认）
        if review_required:
            lines += [
                "",
                "## 待人工复核（字段冲突，未自动打分）",
                "",
                "以下岗位在工作地点等字段上疑似提取冲突（如宣讲场地被识别为工作地点），"
                "已标记跳过自动打分，请人工核对原始消息后处理：",
                "",
            ]
            for record in review_required:
                lines.append(self._render_flag(record, "复核"))
        # 独立板块：基础信息残缺（问题修复 4：缺岗位名/公司名，需进一步检索补充）
        if incomplete:
            lines += [
                "",
                "## 基础信息残缺（信息不足，需进一步检索补充）",
                "",
                "以下消息缺少公司名称或岗位名称等核心信息，已标记跳过自动打分，"
                "不纳入推荐池；建议结合原始链接/图片人工检索补充：",
                "",
            ]
            for record in incomplete:
                lines.append(self._render_flag(record, "残缺"))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_flag(record: dict, kind: str) -> str:
        """渲染残缺/复核板块的单条记录（含检索线索，便于人工补充）。"""
        company = _field(record, "company_name")
        job_title = _field(record, "job_title")
        reason = _field(record, "incomplete_reason") or _field(record, "review_reason")
        search = ""
        if company != MISSING and job_title != MISSING:
            search = f" ｜ 检索线索：{company} {job_title} 风评 WLB"
        category = normalize_category(record.get("category")).label
        return (
            f"- **{company} / {job_title}**（{category}）\n"
            f"  - 标记：{reason} ｜ 来源：群 {_field(record, 'group_id')} / "
            f"消息 {_field(record, 'message_id')}{search}"
        )

    @staticmethod
    def _render_job(index: int, record: dict) -> str:
        """渲染单条推荐岗位：分数、理由、信息来源必带（产品方案 4.6）。"""
        # 类别英文枚举值 → 中文标签（正式招聘/宣讲通知/内推信息），提高日报可读性
        category = normalize_category(record.get("category")).label
        researched = bool(record.get("external_researched"))
        company = _field(record, "company_name")
        job_title = _field(record, "job_title")
        # 联网核验标注 + 可一键复制搜索关键词（问题修复 5：WLB 来源可核验）
        verify_line = (
            "- 联网核验：已检索公司外部资料（WLB/风评见打分理由）"
            if researched else
            "- 联网核验：未自动联网核验，WLB 维度需人工自查"
        )
        search_line = ""
        if company != MISSING and job_title != MISSING:
            search_line = (
                f"- 搜索关键词：`{company} {job_title} 风评 WLB`"
                "（复制到搜索引擎即可核验）"
            )
        lines = [
            f"## {index}. {company} / {job_title} — {_score(record)} 分",
            "",
            f"- 工作地点：{_field(record, 'location')}",
            f"- 学历要求：{_field(record, 'education')}",
            f"- 技术栈：{_field(record, 'tech_stack')}",
            f"- 投递方式：{_field(record, 'apply_method')}",
            f"- 截止时间：{_field(record, 'deadline')}",
            f"- 信息来源：{_field(record, 'source')}",
            verify_line,
            f"- 打分理由：{_field(record, 'reason')}",
            f"- 消息来源：群 {_field(record, 'group_id')} / "
            f"消息 {_field(record, 'message_id')}（{category}）",
            "",
        ]
        if search_line:
            # 搜索关键词行插在打分理由之前，便于快速核验
            lines.insert(-2, search_line)
        return "\n".join(lines)
