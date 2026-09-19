# job_assistant/multimodal/text_branch.py

"""文本直传分支（模块二分支 1）。

文本不经过任何改写，直接作为合并文本的组成部分透传下游。
本模块独立成文件，便于后续在此追加文本清洗等扩展。
"""

from __future__ import annotations


def process_text(raw_text: str) -> str:
    """文本分支：原始文本直接透传。

    Args:
        raw_text: 消息纯文本。

    Returns:
        与入参相同的文本（透传，不做任何改写）。
    """
    return raw_text
