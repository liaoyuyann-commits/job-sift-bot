# -*- coding: utf-8 -*-
"""调试：查看搜索结果 URL 与详情页筛选结果"""
import sys
import logging

sys.path.insert(0, r"C:\Users\26440\Desktop\AI 求职信息智能筛选助手")
logging.basicConfig(level=logging.WARNING)

from job_assistant.config.settings import load_config
from job_assistant.parser.llm_client import HttpLLMClient
from job_assistant.parser.schema import JobPosting
from job_assistant.research import company_research as cr

cfg = load_config(r"C:\Users\26440\Desktop\AI 求职信息智能筛选助手\job_assistant\config\config.yaml")
llm = HttpLLMClient(cfg.llm)

queries = cr._build_search_queries(llm, "普渡机器人", "")
print("检索词:", queries)
items = []
for q in queries:
    items.extend(cr._search_multi(q, 10.0))
    if len(items) >= cr._SEARCH_TOP_N:
        break
print("结果条数:", len(items))
for it in items:
    print(f"  score={cr._detail_score(it['url'])} | {it['title'][:30]} | {it['url']}")
print("选中详情页:", cr._pick_detail_urls(items))
print("_MAX_BYTES:", getattr(cr, "_MAX_BYTES", "未定义"))
