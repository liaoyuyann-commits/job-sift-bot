# job_assistant/multimodal/__init__.py

"""模块二：多模态消息解析模块。

职责（对应 ARCHITECTURE.md 模块二）：
    - 文本直传（text_branch）
    - 图片下载 + 本地 OCR（ocr_engine）
    - URL 网页抓取 + HTML 正文清洗（web_fetcher）
    - 三分支编排，合并为完整文本供模块四解析（processor）

失败策略：单条分支失败标记异常、不阻断整体流程（ARCHITECTURE.md 非功能需求）。
"""
