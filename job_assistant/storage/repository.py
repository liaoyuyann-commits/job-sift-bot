# job_assistant/storage/repository.py

"""模块六：本地数据归档（岗位去重 + JSON 全量永久存档）。

业务对应（产品方案 4.6 / ARCHITECTURE.md 模块六）：
    - 存储规则：所有识别到的岗位（无论分数高低），结构化 JSON 全量永久存入
      ./data/jobs 目录；岗位 JSON 字段一次性定义完整，兼容后续 V2.0 Web UI 读取
    - 去重规则：基于「公司名称 + 岗位名称」去重，重复岗位保留首次记录
    - 失败策略：单条岗位归档失败标记返回，不阻断整体流程（对齐模块二/四/五）

存储记录格式（底层存储格式保持稳定，后续不再修改）：
    JobPosting.to_dict() 的 11 个字段（8 业务字段 + category + 群号/消息 ID 溯源）
    + 模块五打分字段（score / reason / threshold / recommended）
    + 归档时间戳（recorded_at）
    字段只增不减，V2.0 Web UI 可直接读取同一记录结构。

设计决策：
    - 文件名以「YYYY-MM-DD_」日期前缀开头，日报按当日新增岗位聚合
    - 去重索引启动时扫描历史存档重建（个人规模 N 可接受，一次 O(N)）
    - 公司名/岗位名缺失（【信息未提及】）时无法可靠去重，直接归档不丢弃
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from ..errors import JobSaveError
from ..matcher.scorer import MatchResult
from ..parser.schema import MISSING

logger = logging.getLogger(__name__)

# 文件名非法字符清洗（公司/岗位名可能含 / \ : * ? " < > | 等，避免路径歧义）
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\s]+')
_FILENAME_SEGMENT_MAX = 40


def _clean_segment(text: str) -> str:
    """把公司/岗位名清洗为安全的文件名片段（去非法字符、限长、防空）。"""
    cleaned = _UNSAFE_RE.sub("_", text.strip())
    return cleaned[:_FILENAME_SEGMENT_MAX] or "未知"


class JobRepository:
    """岗位归档仓储：按「公司 + 岗位」去重，全量 JSON 永久存档。

    Args:
        jobs_dir: 岗位 JSON 存档目录（对应 config.data.jobs，不存在时自动创建）。
    """

    def __init__(self, jobs_dir: str | Path) -> None:
        self._dir = Path(jobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # 去重键（公司\x1f岗位）→ 已存档文件路径；启动时扫描重建
        self._index: dict[str, Path] = {}
        self._rebuild_index()

    # ---------- 编排入口 ----------

    def save_job(self, match: MatchResult, *, extra: dict | None = None) -> bool:
        """归档一条打分成功的岗位（推荐 + 仅存档全量落盘）。

        Args:
            match: 模块五打分结果（score / reason / recommended 一并写入存档）。
            extra: 可选的附加字段（如手动录入的薪资/备注），写入存档记录，
                不改变既有字段语义；默认 None 不影响自动抓取链路。

        Returns:
            True=本次新写入；False=去重跳过（保留首次记录）或归档失败。
            打分失败（has_error）的岗位不入档，仅记录 WARN。
        """
        if match.has_error:
            logger.warning(
                "打分失败岗位不入档: job=%s error=%s", match.job.brief(), match.error,
            )
            return False

        key = self._dedupe_key(match.job.company_name, match.job.job_title)
        if key is not None and key in self._index:
            logger.info(
                "去重跳过（已归档，保留首次记录）: job=%s 已存在=%s",
                match.job.brief(), self._index[key].name,
            )
            return False

        try:
            path = self._write_record(match, extra)
        except JobSaveError as e:
            logger.warning("岗位归档失败: job=%s 错误=%s", match.job.brief(), e)
            return False
        except Exception as e:  # 未预期异常：标记失败不阻断
            logger.warning("岗位归档异常: job=%s 错误=%s", match.job.brief(), e)
            return False

        if key is not None:
            self._index[key] = path
        logger.info(
            "岗位归档: %s score=%d 类型=%s", path.name, match.score,
            "推荐" if match.is_recommended else "仅存档",
        )
        return True

    # ---------- 读取 ----------

    def load_all(self) -> list[dict]:
        """读取全部历史岗位记录（按文件名升序，即时间顺序）；损坏文件跳过并 WARN。"""
        return [
            record for path in sorted(self._dir.glob("*.json"))
            if (record := self._read_record(path)) is not None
        ]

    def load_by_date(self, day: date) -> list[dict]:
        """读取指定日期的岗位记录（按文件名日期前缀过滤，即当日新增岗位）。

        Args:
            day: 目标日期（如 date.today()）。
        """
        prefix = f"{day.isoformat()}_"
        return [
            record for path in sorted(self._dir.glob(f"{prefix}*.json"))
            if (record := self._read_record(path)) is not None
        ]

    def load_recommended(self, day: date) -> list[dict]:
        """读取指定日期的推荐岗位记录（recommended=True，日报输入）。

        过滤依据为存档时写入的 recommended 标记（评分当时阈值判定），
        与当日控制台推荐展示保持一致。
        """
        return [r for r in self.load_by_date(day) if r.get("recommended")]

    # ---------- 内部实现 ----------

    def _rebuild_index(self) -> None:
        """启动时扫描历史存档重建去重索引（个人规模 N 可接受，一次 O(N)）。"""
        for path in self._dir.glob("*.json"):
            record = self._read_record(path)
            if record is None:
                continue
            key = self._dedupe_key(
                str(record.get("company_name", "")),
                str(record.get("job_title", "")),
            )
            if key is not None:
                # 同键多文件时保留最早写入的（文件名按日期+消息序排序取首个）
                self._index.setdefault(key, path)

    @staticmethod
    def normalize_company_name(company: str) -> str:
        """公司名归一化（去重用）：业务线/子公司别名映射到主公司，去除后缀与地区括号。

        例：华为半导体业务部/华为海思 → 华为；沐曦集成电路（上海）股份有限公司 → 沐曦股份；
           深圳市普渡科技股份有限公司 → 普渡机器人；宇树科技股份有限公司 → 宇树科技。
        返回空串表示无有效公司名（不参与去重）。
        """
        name = (company or "").strip()
        if not name or name == MISSING:
            return ""
        # 1) 已知业务线/别名 → 主公司（优先级最高，精确匹配）
        alias = {
            "华为半导体业务部": "华为",
            "华为海思": "华为",
            "海思": "华为",
            "海思半导体": "华为",
            "华为半导体": "华为",
            "深圳市普渡科技股份有限公司": "普渡机器人",
            "深圳普渡科技": "普渡机器人",
            "普渡科技": "普渡机器人",
            "宇树科技股份有限公司": "宇树科技",
            "深圳宇树科技": "宇树科技",
            "沐曦集成电路（上海）股份有限公司": "沐曦股份",
            "沐曦集成电路": "沐曦股份",
            "西安华讯科技有限责任公司": "华讯科技",
            "深圳华讯科技": "华讯科技",
            "中国石油集团东方地球物理勘探有限责任公司": "东方物探",
            "深圳市纵维立方科技有限公司": "纵维立方",
            "上海龙旗科技股份有限公司": "龙旗",
            "神龙汽车有限公司": "神龙汽车",
            "王力安防科技股份有限公司": "王力安防",
            "王力安防科技": "王力安防",
        }
        if name in alias:
            return alias[name]
        # 2) 去除地区括号（全角/半角）
        name = re.sub(r"[（(].*?[）)]", "", name).strip()
        # 3) 去除公司后缀
        for suffix in ("股份有限公司", "有限责任公司", "股份公司", "有限公司"):
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()
                break
        return name

    @staticmethod
    def normalize_job_title(job_title: str) -> str:
        """岗位名归一化（去重用）：去【信息未提及】、J 编号、括号内容与空白。

        返回空串表示泛校招/无具体岗位名（宣讲会、校园招聘公告等）。
        """
        title = (job_title or "").strip()
        if not title or title == MISSING:
            return ""
        title = title.replace(MISSING, "")
        title = re.sub(r"J\d+", "", title)
        title = re.sub(r"[（(].*?[）)]", "", title)
        title = re.sub(r"\s+", "", title).strip()
        return title

    @staticmethod
    def _is_generic_recruitment(job_title: str) -> bool:
        """是否泛校招类岗位（宣讲会/校园招聘公告/应届生招聘等，无具体岗位名）。"""
        title = (job_title or "").strip()
        if not title or title == MISSING:
            return True
        if "半导体" in title:
            return True  # 华为半导体业务部等业务线招聘，视为同一校招入口
        generic_words = ("校招", "校园招聘", "应届", "招聘", "宣讲会", "秋招", "春招",
                         "全球招聘", "信动力", "管培生")
        specific_words = ("工程师", "开发", "算法", "产品", "设计", "销售", "运营",
                          "测试", "硬件", "软件", "数据", "架构", "研发", "技术",
                          "管理", "营销", "职能", "采购", "供应链", "质量")
        # 岗位枚举列表（"芯片类、软件类、AI类、硬件类、系统类"）视为同一泛校招入口
        if sum(1 for w in specific_words if w in title) >= 2:
            return True
        return any(w in title for w in generic_words) and not any(
            w in title for w in specific_words
        )

    @staticmethod
    def _dedupe_key(company: str, job_title: str) -> str | None:
        """去重键：归一化公司名称 + 归一化岗位名称（产品方案 F7 增强版）。

        公司名/岗位名归一化后参与去重（华为半导体业务部与华为视为同一公司、
        相同岗位补全信息视为重复）；关键字段缺失时返回 None（不去重归档，
        不丢弃任何岗位信息）。
        """
        norm_company = JobRepository.normalize_company_name(company)
        norm_title = JobRepository.normalize_job_title(job_title)
        if not norm_company:
            return None
        # 泛校招（无具体岗位名）统一用一个占位键，同一公司只保留一条
        if not norm_title or JobRepository._is_generic_recruitment(job_title):
            norm_title = "\x1fgeneric"
        return f"{norm_company}\x1f{norm_title}"

    def _write_record(self, match: MatchResult, extra: dict | None = None) -> Path:
        """组装稳定存档记录并写盘（模块六底层方法；写盘失败抛 JobSaveError）。"""
        record = self._to_record(match, extra)
        path = self._dir / self._build_filename(match)
        try:
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            raise JobSaveError(f"岗位 JSON 归档失败: {path.name} ({e})") from e
        return path

    @staticmethod
    def _to_record(match: MatchResult, extra: dict | None = None) -> dict:
        """归档记录 = JobPosting 全部字段 + 打分结果 + 质量标记 + 归档时间戳。

        字段只增不减：V2.0 Web UI 可读取既有 JobPosting 字段，并直接复用
        score / reason / recommended 展示打分详情；incomplete / review_required
        标记残缺与待复核岗位（问题修复 1/4），日报据此开辟独立板块。
        extra（如手动录入的薪资/备注）在标准字段之后追加，不覆盖既有字段。
        """
        record = match.job.to_dict()
        record.update({
            "score": match.score,
            "reason": match.reason,
            "threshold": match.threshold,
            "recommended": match.is_recommended,
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            # 质量标记（问题修复 1/4/5）：基础信息残缺 / 待人工复核 / 是否联网核验
            "incomplete": match.incomplete,
            "review_required": match.review_required,
            "incomplete_reason": match.job.incomplete_reason,
            "review_reason": match.job.review_reason,
            "external_researched": match.external_researched,
        })
        if extra:
            record.update(extra)
        return record

    def _build_filename(self, match: MatchResult) -> str:
        """文件名：{日期}_g{群号}_m{消息ID}_{公司}_{岗位}.json。

        日期前缀供日报按「当日新增」聚合；群号/消息 ID 保留溯源；
        公司/岗位经非法字符清洗后入文件名，便于人工浏览。
        """
        return (
            f"{date.today().isoformat()}_"
            f"g{match.job.group_id}_m{match.job.message_id}_"
            f"{_clean_segment(match.job.company_name)}_"
            f"{_clean_segment(match.job.job_title)}.json"
        )

    @staticmethod
    def _read_record(path: Path) -> dict | None:
        """读取单条岗位记录；损坏/不可读文件跳过并 WARN（单条失败不阻断）。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            logger.warning("岗位存档读取失败，跳过: %s", path.name)
            return None


def recompute_recommended(jobs_dir: str | Path, threshold: int) -> dict:
    """按当前阈值全量重算已存档岗位的推荐标记（阈值修改后调用）。

    业务语义（用户诉求：每次修改推荐阈值，全部已存档岗位与新阈值重新比对）：
        - 重算规则：recommended = (score >= threshold)，与打分时判定逻辑一致
        - threshold 字段同步更新为新阈值，保证看板/日报展示与当前推荐线一致
        - 只重算标记与阈值字段，不重新打分、不改分数与打分理由
        - 单条失败跳过不阻断；写回为全量覆盖单文件，幂等可重复执行

    Returns:
        统计: {"scanned": 扫描文件数, "updated": 标记变化并写回数,
               "failed": 读取/写回失败数}
    """
    directory = Path(jobs_dir)
    scanned = updated = failed = 0
    if not directory.is_dir():
        return {"scanned": 0, "updated": 0, "failed": 0}

    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                continue
        except (OSError, json.JSONDecodeError):
            failed += 1
            logger.warning("推荐标记重算跳过损坏文件: %s", path.name)
            continue

        scanned += 1
        try:
            score = float(record.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        new_recommended = score >= threshold
        if record.get("recommended") != new_recommended or record.get("threshold") != threshold:
            record["recommended"] = new_recommended
            record["threshold"] = threshold
            try:
                path.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                updated += 1
            except OSError as e:
                failed += 1
                logger.warning("推荐标记重算写回失败: %s (%s)", path.name, e)

    logger.info(
        "推荐标记全量重算完成: 扫描=%d 更新=%d 失败=%d 阈值=%d",
        scanned, updated, failed, threshold,
    )
    return {"scanned": scanned, "updated": updated, "failed": failed}


def dedupe_jobs(jobs_dir: str | Path) -> dict:
    """按归一化去重键全量清理重复岗位（启动/定时触发，用户诉求：小助手自动去重）。

    规则（与 JobRepository._dedupe_key 一致）：
        - 公司名归一化（华为半导体业务部/华为海思 → 华为，去公司后缀）
        - 岗位名归一化（去 J 编号/括号/【信息未提及】；泛校招同一公司只保留一条）
        - 每组保留最优：推荐优先 → 分数高 → 信息完整 → 文件新
        - 重复记录移动到 jobs_dedup_backup_<时间戳>/（不删除，可恢复）
        - 无公司名/损坏文件不参与去重（不丢弃任何岗位信息）

    Returns:
        统计: {"scanned": 参与扫描数, "groups": 重复组数, "kept": 保留数,
               "moved": 合并移动数, "backup_dir": 备份目录}
    """
    directory = Path(jobs_dir)
    if not directory.is_dir():
        return {"scanned": 0, "groups": 0, "kept": 0, "moved": 0, "backup_dir": ""}

    groups: dict[str, list[Path]] = {}
    scanned = 0
    for path in sorted(directory.glob("*.json")):
        record = JobRepository._read_record(path)
        if not record:
            continue
        scanned += 1
        key = JobRepository._dedupe_key(
            record.get("company_name", ""), record.get("job_title", ""),
        )
        if key is None:
            continue  # 缺公司名：不去重
        groups.setdefault(key, []).append(path)

    def _completeness(record: dict) -> int:
        fields = ("company_name", "job_title", "location", "education",
                  "tech_stack", "apply_method", "deadline")
        return sum(1 for f in fields
                   if record.get(f) not in (None, "", MISSING))

    def _rank(path: Path):
        record = JobRepository._read_record(path) or {}
        try:
            score = float(record.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        return (bool(record.get("recommended")), score,
                _completeness(record), path.name)

    kept = moved = dup_groups = 0
    backup_dir = directory.parent / f"jobs_dedup_backup_{datetime.now():%Y%m%d_%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for key, paths in groups.items():
        if len(paths) <= 1:
            kept += 1
            continue
        dup_groups += 1
        best = max(paths, key=_rank)
        best_rec = JobRepository._read_record(best) or {}
        for path in paths:
            if path == best:
                kept += 1
                continue
            dest = backup_dir / path.name
            try:
                path.rename(dest)
                moved += 1
            except OSError as e:
                logger.warning("去重移动失败: %s (%s)", path.name, e)
        logger.info(
            "自动去重合并: 公司=%s 岗位=%s 保留=%s 合并=%d 条",
            best_rec.get("company_name"), (best_rec.get("job_title") or "")[:30],
            best.name, len(paths) - 1,
        )
    logger.info(
        "岗位自动去重完成: 扫描=%d 重复组=%d 保留=%d 合并移动=%d 备份=%s",
        scanned, dup_groups, kept, moved, backup_dir.name,
    )
    return {"scanned": scanned, "groups": dup_groups, "kept": kept,
            "moved": moved, "backup_dir": str(backup_dir)}
