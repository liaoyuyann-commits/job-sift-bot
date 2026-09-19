# job_assistant/__init__.py

"""AI 求职信息智能筛选助手 —— 本地轻量化求职信息自动化采集与筛选工具。

包结构（见根目录 ARCHITECTURE.md）：
    main.py        入口：启停控制、模块装配
    config/        全局配置（监听群、简历、意向、开关）
    listener/      模块一：群监听配置（NapCat 只读监听 + 白名单过滤）
    multimodal/    模块二：多模态消息解析（文本/OCR/网页三分支）
    parser/        模块四：AI 结构化抽取（分类 + 8 字段抽取）
    matcher/       模块五：岗位匹配打分（LLM 动态打分 + 阈值过滤）
    storage/       模块六：本地数据归档与日报（去重 + JSON 全量存档 + Markdown 日报）
"""

__version__ = "0.6.0"
