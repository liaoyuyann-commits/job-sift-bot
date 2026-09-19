# -*- coding: utf-8 -*-
"""存量岗位一键去重工具（配合 repository 归一化去重键使用）。

规则：
    - 去重键 = 归一化公司 + 归一化岗位（华为半导体业务部/华为海思 → 华为；
      泛校招/半导体业务线岗位同一公司只保留一条；相同岗位只保留一条）
    - 每组保留最优：推荐优先 → 分数高优先 → 信息完整优先 → 文件新优先
    - 其余记录移动到 data/jobs_dedup_backup_<时间戳>/（不直接删除，可恢复）

用法：python _dedupe_jobs.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from job_assistant.config.settings import load_config  # noqa: E402
from job_assistant.parser.schema import MISSING  # noqa: E402
from job_assistant.storage.repository import JobRepository  # noqa: E402


def _completeness(record: dict) -> int:
    """信息完整度：非【信息未提及】/空的业务字段数量（保留信息更全的记录）。"""
    fields = ("company_name", "job_title", "location", "education",
              "tech_stack", "apply_method", "deadline")
    return sum(1 for f in fields
               if record.get(f) not in (None, "", MISSING))


def main() -> None:
    cfg = load_config(ROOT / "job_assistant" / "config" / "config.yaml")
    jobs_dir = Path(cfg.data.paths["jobs"])

    # 1) 读取全部记录并按键分组
    groups: dict[str, list[tuple[Path, dict]]] = {}
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        key = JobRepository._dedupe_key(
            record.get("company_name", ""), record.get("job_title", ""),
        )
        if key is None:
            continue  # 缺公司名：不去重
        groups.setdefault(key, []).append((path, record))

    # 2) 逐组选最优保留
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = jobs_dir.parent / f"jobs_dedup_backup_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    kept = moved = 0
    kept_names: list[str] = []
    for key, items in groups.items():
        if len(items) <= 1:
            kept += 1
            continue
        # 最优：推荐优先 → 分数高 → 信息完整 → 文件新
        def rank(item):
            path, record = item
            return (
                bool(record.get("recommended")),
                float(record.get("score") or 0),
                _completeness(record),
                path.name,
            )
        best_path, best_record = max(items, key=rank)
        for path, record in items:
            if path == best_path:
                kept += 1
                kept_names.append(f"{path.name}（保留）")
                continue
            dest = backup_dir / path.name
            path.rename(dest)
            moved += 1
            print(f"[去重] {best_record.get('company_name')} | "
                  f"{best_record.get('job_title','')[:30]} | "
                  f"score={best_record.get('score')} → 合并入 {path.name}")
            print(f"       移动备份: {dest.name}")

    # 3) 汇总
    print(f"\n共处理 {sum(len(v) for v in groups.values())} 条，"
          f"保留 {kept} 条，合并移动 {moved} 条")
    print(f"备份目录: {backup_dir}")


if __name__ == "__main__":
    main()
