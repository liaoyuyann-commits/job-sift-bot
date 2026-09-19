# -*- coding: utf-8 -*-
"""临时脚本：按新阈值 50 重算已存档岗位的推荐标记（验证后删除）。"""
import json
import shutil
from pathlib import Path

JOBS = Path(r"C:\Users\26440\Desktop\AI 求职信息智能筛选助手\data\jobs")
BAK = Path(r"C:\Users\26440\Desktop\AI 求职信息智能筛选助手\data\jobs_backup_20260920_0235")
NEW_THRESHOLD = 50

# 1. 备份（幂等）
if not BAK.exists():
    shutil.copytree(JOBS, BAK)
    print("已备份 jobs ->", BAK.name, "文件数:", len(list(BAK.glob("*.json"))))
else:
    print("备份已存在，跳过:", BAK.name)

# 2. 重算 recommended / threshold
changed = 0
total = 0
newly_recommended = []  # 本次由 False -> True 的岗位
for p in sorted(JOBS.glob("*.json")):
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print("跳过损坏文件:", p.name, e)
        continue
    if not isinstance(data, dict):
        continue
    total += 1
    try:
        score = float(data.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    incomplete = bool(data.get("incomplete")) or bool(data.get("review_required"))
    new_rec = (not incomplete) and score >= NEW_THRESHOLD
    old_rec = bool(data.get("recommended"))
    data["recommended"] = new_rec
    data["threshold"] = NEW_THRESHOLD
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    changed += 1
    if new_rec and not old_rec:
        newly_recommended.append(f"{data.get('company_name')} | {data.get('job_title')} | score={data.get('score')}")

print(f"共 {total} 条，全部更新（recommended/threshold -> {NEW_THRESHOLD}）")
print(f"本次新增推荐岗位 {len(newly_recommended)} 个:")
for item in newly_recommended:
    print("  ★", item)

# 3. 统计
recs = sum(1 for p in JOBS.glob("*.json")
            if json.loads(p.read_text(encoding="utf-8")).get("recommended"))
print("重算后推荐岗位总数:", recs)
