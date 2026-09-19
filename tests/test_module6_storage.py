# tests/test_module6_storage.py

"""模块六（本地数据归档与日报模块）自测脚本。

覆盖范围：
    1. JobRepository 归档：目录自动创建、推荐/仅存档全量落盘、打分失败不入档
    2. 去重：公司+岗位去重（保留首次记录）、关键字段缺失不去重、去重索引重建
    3. 文件名：日期前缀、非法字符清洗、溯源信息
    4. 读取：load_all / load_by_date / load_recommended、损坏 JSON 跳过
    5. DailyReport 日报：仅推荐岗位、分数降序、含分数/理由/来源、空日报兜底
    6. errors：STO 域错误码（STO.RUN.001 / STO.SAVE.001 / STO.REPORT.001）

运行方式：python tests/test_module6_storage.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# 保证可直接从项目根目录运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.errors import ReportError, StorageError  # noqa: E402
from job_assistant.matcher.scorer import MatchResult  # noqa: E402
from job_assistant.parser.schema import MISSING, JobPosting, MessageCategory  # noqa: E402
from job_assistant.storage.repository import JobRepository  # noqa: E402
from job_assistant.storage.report import DailyReport, ReportResult  # noqa: E402

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


def make_job(
    company: str = "美团", title: str = "Java 开发工程师",
    group_id: int = 111, message_id: int = 222,
) -> JobPosting:
    return JobPosting(
        company_name=company, job_title=title, location="西安",
        tech_stack="Java、Spring", source="校招官网",
        group_id=group_id, message_id=message_id,
    )


def make_match(job: JobPosting, score: int = 85, reason: str = "匹配") -> MatchResult:
    return MatchResult(job=job, score=score, reason=reason, threshold=80)


def make_repo(tmp: str) -> JobRepository:
    return JobRepository(Path(tmp) / "jobs")


# ---------- 1. JobRepository 归档 ----------

def test_repository_save() -> None:
    print("== JobRepository: 归档 ==")
    with tempfile.TemporaryDirectory() as tmp:
        jobs_dir = Path(tmp) / "jobs"
        repo = make_repo(tmp)
        check("目录自动创建", jobs_dir.is_dir())

        # 1. 推荐岗位（≥ 阈值）落盘
        ok = repo.save_job(make_match(make_job(), score=90))
        check("推荐岗位归档成功", ok)
        check("JSON 文件已写入", len(list(jobs_dir.glob("*.json"))) == 1)

        # 2. 仅存档岗位（< 阈值）同样全量落盘（产品方案 F6）
        ok = repo.save_job(make_match(make_job("字节", "后端开发"), score=60))
        check("仅存档岗位归档成功", ok)
        check("全量存档（推荐+仅存档）", len(list(jobs_dir.glob("*.json"))) == 2)

        # 3. 打分失败岗位不入档
        failed = MatchResult(job=make_job("失败公司", "岗位A"), error="[MAT.SCORE.001] x")
        ok = repo.save_job(failed)
        check("打分失败不入档", not ok and len(list(jobs_dir.glob("*.json"))) == 2)

        # 4. 记录内容完整：JobPosting 字段 + 打分字段 + 时间戳
        record = json.loads(next(jobs_dir.glob("*.json")).read_text(encoding="utf-8"))
        check("记录含 8 业务字段", all(k in record for k in (
            "company_name", "job_title", "location", "education",
            "tech_stack", "apply_method", "deadline", "source")))
        check("记录含打分与时间戳", "score" in record and "reason" in record
              and "recommended" in record and "recorded_at" in record)


# ---------- 2. 去重 ----------

def test_dedupe() -> None:
    print("== JobRepository: 去重 ==")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)

        # 1. 相同公司+岗位 → 第二次跳过（保留首次记录）
        first = make_match(make_job(message_id=1), score=90)
        dup = make_match(make_job(message_id=2), score=95)
        check("首次写入", repo.save_job(first))
        check("重复写入跳过", not repo.save_job(dup))
        today = date.today()
        records = repo.load_by_date(today)
        check("仅保留 1 条记录", len(records) == 1)
        check("保留首次记录内容", records[0]["message_id"] == 1 and records[0]["score"] == 90)

        # 2. 不同公司/岗位 → 不冲突
        check("不同岗位可归档", repo.save_job(make_match(make_job("腾讯", "前端", message_id=3))))
        check("不同公司可归档", repo.save_job(make_match(make_job("字节", "后端", message_id=4))))
        check("3 条不重复记录", len(repo.load_by_date(today)) == 3)

        # 3. 关键字段缺失（【信息未提及】）→ 不去重，全部归档
        missing_job = make_job(company=MISSING, title=MISSING, message_id=5)
        check("缺失字段岗位 1 可归档", repo.save_job(make_match(missing_job, score=70)))
        missing_job2 = make_job(company=MISSING, title=MISSING, message_id=6)
        check("缺失字段岗位 2 不误判重复", repo.save_job(make_match(missing_job2, score=70)))
        check("缺失字段岗位全部保留", len(repo.load_by_date(today)) == 5)


def test_rebuild_index() -> None:
    print("== JobRepository: 去重索引重建 ==")
    with tempfile.TemporaryDirectory() as tmp:
        jobs_dir = Path(tmp) / "jobs"
        jobs_dir.mkdir(parents=True)
        # 预写一条历史记录，再重建索引（模拟重启后去重仍生效）
        record = make_match(make_job(message_id=1), score=88).job.to_dict()
        record.update(score=88, reason="r", threshold=80, recommended=True,
                      recorded_at="2026-09-18T10:00:00")
        (jobs_dir / "2026-09-18_g111_m1_美团_Java开发工程师.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")

        repo = make_repo(tmp)
        ok = repo.save_job(make_match(make_job(message_id=2), score=88))
        check("重启后重复岗位仍被去重", not ok and len(repo.load_all()) == 1)


# ---------- 3. 文件名 ----------

def test_filename() -> None:
    print("== JobRepository: 文件名 ==")
    with tempfile.TemporaryDirectory() as tmp:
        jobs_dir = Path(tmp) / "jobs"
        repo = make_repo(tmp)
        repo.save_job(make_match(make_job("华为/荣耀", "OD 开发(西安)", group_id=123, message_id=456)))
        files = list(jobs_dir.glob("*.json"))
        check("生成 1 个文件", len(files) == 1)
        name = files[0].name
        check("日期前缀", name.startswith(date.today().isoformat() + "_"))
        check("含群号/消息溯源", "g123_m456" in name)
        check("非法字符已清洗", "/" not in name and ":" not in name and " " not in name)


# ---------- 4. 读取 ----------

def test_load() -> None:
    print("== JobRepository: 读取 ==")
    with tempfile.TemporaryDirectory() as tmp:
        jobs_dir = Path(tmp) / "jobs"
        repo = make_repo(tmp)
        repo.save_job(make_match(make_job("A", "岗位1", message_id=1), score=90))
        repo.save_job(make_match(make_job("B", "岗位2", message_id=2), score=60))

        # 1. load_by_date：当日命中，他日为空
        check("当日记录读取", len(repo.load_by_date(date.today())) == 2)
        other = date.today() - timedelta(days=1)
        check("他日记录为空", repo.load_by_date(other) == [])

        # 2. load_recommended：只含推荐岗位
        recs = repo.load_recommended(date.today())
        check("推荐过滤", len(recs) == 1 and recs[0]["score"] == 90)

        # 3. load_all：全部历史
        check("全量读取", len(repo.load_all()) == 2)

        # 4. 损坏 JSON 跳过不崩溃
        (jobs_dir / "2026-09-17_g1_m1_bad_bad.json").write_text("{broken", encoding="utf-8")
        check("损坏文件跳过", len(repo.load_all()) == 2)


# ---------- 5. DailyReport 日报 ----------

def test_report() -> None:
    print("== DailyReport: 日报生成 ==")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        # 2 推荐（85/95）+ 1 仅存档（60）→ 日报只含 2 推荐、降序
        repo.save_job(make_match(make_job("美团", "Java 开发", message_id=1), score=85, reason="技术栈匹配"))
        repo.save_job(make_match(make_job("字节", "后端开发", message_id=2), score=95, reason="高度对口"))
        repo.save_job(make_match(make_job("腾讯", "前端开发", message_id=3), score=60, reason="低分"))

        report = DailyReport(Path(tmp) / "report")
        result = report.generate(repo, threshold=80)
        check("返回 ReportResult", isinstance(result, ReportResult))
        check("落盘文件名", result.path.name == f"{date.today().isoformat()}.md")
        check("落盘文件存在", result.path.is_file())
        check("推荐计数", result.recommended_count == 2)
        check("当日全量计数", result.archived_count == 3)

        md = result.markdown
        check("日报含标题与日期", f"求职岗位日报（{date.today().isoformat()}）" in md)
        check("降序排列（95 在 85 前）", md.index("字节") < md.index("美团"))
        check("含分数", "95 分" in md and "85 分" in md)
        check("含打分理由", "高度对口" in md and "技术栈匹配" in md)
        check("含信息来源", "校招官网" in md)
        check("低分岗位不入日报", "腾讯" not in md and "低分" not in md)
        check("含消息来源溯源", "群 111" in md and "消息 2" in md)


def test_report_sort_stable() -> None:
    print("== DailyReport: 同分稳定排序 ==")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        repo.save_job(make_match(make_job("A", "岗位1", message_id=1), score=80, reason="r1"))
        repo.save_job(make_match(make_job("B", "岗位2", message_id=2), score=80, reason="r2"))
        md = DailyReport(Path(tmp) / "report").generate(repo).markdown
        check("同分保持归档顺序", md.index("A") < md.index("B"))


def test_report_empty() -> None:
    print("== DailyReport: 空日报兜底 ==")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        report = DailyReport(Path(tmp) / "report")
        result = report.generate(repo)
        check("空日报推荐数 0", result.recommended_count == 0)
        check("空日报仍落盘", result.path.is_file())
        check("空日报提示文案", "今日无推荐岗位" in result.markdown)


# ---------- 6. errors ----------

def test_errors() -> None:
    print("== errors: STO 域错误码 ==")
    check("StorageError 兜底码", StorageError("x").error_code == "STO.RUN.001")
    check("JobSaveError 码", __import__("job_assistant.errors", fromlist=["JobSaveError"]).JobSaveError("x").error_code == "STO.SAVE.001")
    check("ReportError 码", ReportError("x").error_code == "STO.REPORT.001")
    check("STO 域归属", issubclass(ReportError, StorageError))


def main() -> None:
    test_repository_save()
    test_dedupe()
    test_rebuild_index()
    test_filename()
    test_load()
    test_report()
    test_report_sort_stable()
    test_report_empty()
    test_errors()
    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
