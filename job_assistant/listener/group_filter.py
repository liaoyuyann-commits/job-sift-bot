# job_assistant/listener/group_filter.py

"""群消息白名单过滤与规范化。

业务对应（产品方案 4.1 群监听配置模块）：
    - NapCat 收到消息优先判断群 ID，不匹配直接丢弃，不进入下游流程
    - 输出统一结构的 GroupMessage，附带图片地址与网页链接，供模块二多模态处理
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 网页链接提取正则：仅匹配 http/https，避免 QQ 表情链接等干扰
_URL_PATTERN = re.compile(r"https?://[^\s，。；、）】\"']+", re.IGNORECASE)


@dataclass
class GroupMessage:
    """规范化后的群消息（模块一的输出，模块二的输入）。"""

    message_id: int                # 消息唯一 ID
    group_id: int                  # 群号
    user_id: int                   # 发送者 QQ 号
    timestamp: int                 # 发送时间戳（秒）
    raw_text: str                  # 纯文本内容（text 段拼接）
    image_urls: list[str] = field(default_factory=list)   # 图片地址（供 OCR）
    urls: list[str] = field(default_factory=list)         # 网页链接（供抓取）
    raw: dict[str, Any] = field(default_factory=dict)     # 原始 OneBot v11 事件，便于扩展


def _extract_text(message: list[dict[str, Any]]) -> str:
    """拼接消息段中的 text 内容。"""
    parts: list[str] = []
    for seg in message:
        if isinstance(seg, dict) and seg.get("type") == "text":
            text = seg.get("data", {}).get("text", "")
            if text:
                parts.append(text)
    return "".join(parts)


def _extract_image_urls(message: list[dict[str, Any]]) -> list[str]:
    """提取 image 消息段的图片资源地址。"""
    urls: list[str] = []
    for seg in message:
        if isinstance(seg, dict) and seg.get("type") == "image":
            url = seg.get("data", {}).get("url", "")
            if url:
                urls.append(url)
    return urls


def _extract_web_urls(text: str) -> list[str]:
    """从文本中提取 http/https 网页链接。"""
    return list(dict.fromkeys(_URL_PATTERN.findall(text)))  # 正则去重保序


def filter_message(event: dict[str, Any], monitor_group_ids: list[int]) -> GroupMessage | None:
    """对单条 OneBot v11 事件做群白名单过滤并规范化。

    Args:
        event: NapCat 推送的事件（JSON 反序列化后）。
        monitor_group_ids: 监听群白名单；空列表表示不监听任何群。

    Returns:
        命中白名单的 GroupMessage；被丢弃（非群消息 / 群不在白名单 / 空白名单）返回 None。
    """
    # 只处理群消息事件，其余（notice/request/meta）一律忽略。
    # 兼容 post_type="message_sent"：历史消息/自己发送的群消息也是有效招聘信息
    # （NapCat get_group_msg_history 会把登录 QQ 自己发的消息标为 message_sent）
    if event.get("post_type") not in ("message", "message_sent"):
        return None
    if event.get("message_type") != "group":
        return None

    # 空白名单兜底：不监听任何群
    if not monitor_group_ids:
        return None

    group_id = event.get("group_id")
    if group_id not in monitor_group_ids:
        # 白名单外群直接丢弃，不进入下游流程（日志仅记录群号，不打印消息内容）
        logger.info("丢弃非监听群消息: group_id=%s", group_id)
        return None

    message = event.get("message") or []
    raw_text = _extract_text(message)
    msg = GroupMessage(
        message_id=int(event.get("message_id", 0)),
        group_id=int(group_id),
        user_id=int(event.get("user_id", 0)),
        timestamp=int(event.get("time", 0)),
        raw_text=raw_text,
        image_urls=_extract_image_urls(message),
        urls=_extract_web_urls(raw_text),
        raw=event,
    )
    logger.info(
        "命中监听群消息: group_id=%s message_id=%s text_len=%d images=%d urls=%d",
        msg.group_id, msg.message_id, len(msg.raw_text), len(msg.image_urls), len(msg.urls),
    )
    return msg
