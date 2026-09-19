# job_assistant/listener/__init__.py

"""模块一：群监听配置模块。

职责（对应 ARCHITECTURE.md 模块一）：
    - 连接 NapCat 只读接收群消息（绝不发送任何消息）
    - 按 monitor_group_ids 白名单过滤，不在列表内的群消息直接丢弃
    - 输出规范化 GroupMessage，供模块二（多模态解析）消费
"""
