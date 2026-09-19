# job_assistant/multimodal/processor.py

"""多模态处理编排入口（模块二）：三分支合并为完整文本。

输入：模块一输出的 GroupMessage
输出：MultimodalResult（合并完整文本 + 各分支产物 + 失败标记）
失败策略：单条分支失败标记异常、不阻断整体（ARCHITECTURE.md 非功能需求）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config.settings import DataConfig, MultimodalConfig
from ..errors import MultimodalError
from ..listener.group_filter import GroupMessage
from .ocr_engine import OCREngine, download_image
from .web_fetcher import extract_urls, fetch_web_page, url_stem

logger = logging.getLogger(__name__)


@dataclass
class MultimodalResult:
    """模块二输出：合并完整文本与各分支产物（模块四解析的输入）。"""

    group_id: int
    message_id: int
    merged_text: str                              # 合并后的完整文本
    ocr_texts: list[str] = field(default_factory=list)   # 每条图片 OCR 结果
    web_texts: list[str] = field(default_factory=list)   # 每个网页正文
    errors: list[str] = field(default_factory=list)      # 失败分支标记（含错误码）

    @property
    def has_errors(self) -> bool:
        """是否存在失败分支（单条失败不阻断整体流程）。"""
        return bool(self.errors)


class MultimodalProcessor:
    """多模态处理编排器：文本直传 / 图片 OCR / 网页抓取 → 合并完整文本。"""

    def __init__(
        self,
        multimodal: MultimodalConfig,
        data: DataConfig,
        ocr_engine: OCREngine | None = None,
    ) -> None:
        self._config = multimodal
        self._data = data
        # OCR 引擎支持注入（测试用替身）；默认按开关惰性创建（含识别超时控制）
        self._ocr = ocr_engine if ocr_engine is not None else (
            OCREngine(lang=multimodal.ocr_lang, timeout=multimodal.ocr_timeout)
            if multimodal.enable_ocr else None
        )
        self._dirs = self._data.paths
        self._ensure_dirs()

    # ---------- 生命周期 ----------

    def _ensure_dirs(self) -> None:
        """按配置创建全部数据目录（不存在时）。"""
        for directory in self._dirs.values():
            directory.mkdir(parents=True, exist_ok=True)

    # ---------- 编排入口 ----------

    def process(self, msg: GroupMessage) -> MultimodalResult:
        """处理一条群消息：归档原始消息 → 三分支解析 → 合并完整文本。

        Args:
            msg: 模块一输出的 GroupMessage。

        Returns:
            MultimodalResult：merged_text 为供模块四解析的完整文本；
            单条分支失败记录在 errors 中，不阻断其余分支。
        """
        parts: list[str] = [msg.raw_text] if msg.raw_text else []
        ocr_texts: list[str] = []
        web_texts: list[str] = []
        errors: list[str] = []

        self._save_raw_msg(msg)

        # 分支 2：图片 OCR（受 enable_ocr 与图片数量控制）
        if self._ocr is not None and msg.image_urls:
            for url in msg.image_urls:
                try:
                    text = self._process_image(url, msg.group_id, msg.message_id)
                except MultimodalError as e:
                    errors.append(str(e))
                    logger.warning("图片分支失败: url=%s 错误=%s", url, e)
                    # 问题修复 3：失败不丢弃消息，标注图片线索供人工复核
                    parts.append(f"[图片 {url}：OCR 识别失败，图片内容需人工复核]")
                    continue
                if text:
                    ocr_texts.append(text)
                    parts.append(f"[图片OCR {url}]\n{text}\n[/图片OCR]")

        # 分支 3：网页抓取（受 enable_web_fetch 开关控制）
        # 链接由模块二自行正则提取（产品方案 4.2 URL 分支第 1 步）
        if self._config.enable_web_fetch:
            for url in extract_urls(msg.raw_text):
                try:
                    text = self._process_web(url, msg.group_id, msg.message_id)
                except MultimodalError as e:
                    errors.append(str(e))
                    logger.warning("网页分支失败: url=%s 错误=%s", url, e)
                    # 问题修复 3：反爬/超时失败不丢弃消息，保留原始链接供人工打开
                    parts.append(f"[网页链接 {url}：内容抓取失败，需人工点开链接查看]")
                    continue
                if text:
                    web_texts.append(text)
                    parts.append(f"[网页正文 {url}]\n{text}\n[/网页正文]")

        merged = "\n\n".join(part for part in parts if part and part.strip())
        logger.info(
            "多模态处理完成: group_id=%s message_id=%s merged_len=%d ocr=%d web=%d errors=%d",
            msg.group_id, msg.message_id, len(merged),
            len(ocr_texts), len(web_texts), len(errors),
        )
        return MultimodalResult(
            group_id=msg.group_id,
            message_id=msg.message_id,
            merged_text=merged,
            ocr_texts=ocr_texts,
            web_texts=web_texts,
            errors=errors,
        )

    # ---------- 内部实现 ----------

    def _save_text(self, directory: Path, stem: str, content: str) -> Path:
        """写文本文件到指定目录（utf-8），返回保存路径。"""
        target = directory / f"{stem}.txt"
        target.write_text(content, encoding="utf-8")
        logger.info("数据归档: %s (%d 字节)", target.name, len(content.encode("utf-8")))
        return target

    def _save_raw_msg(self, msg: GroupMessage) -> Path:
        """原始消息归档到 raw_msg 目录（信息沉淀，本地留存）。"""
        stem = f"g{msg.group_id}_m{msg.message_id}"
        content = (
            f"# 原始消息 | group={msg.group_id} msg={msg.message_id} "
            f"user={msg.user_id} time={msg.timestamp}\n{msg.raw_text}"
        )
        return self._save_text(self._dirs["raw_msg"], stem, content)

    def _process_image(self, url: str, group_id: int, message_id: int) -> str:
        """图片分支：下载（带消息前缀防覆盖）→ OCR → 归档 OCR 结果 → 返回识别文本。"""
        img_path = download_image(
            url, self._dirs["image_raw"],
            timeout=self._config.web_timeout,
            max_bytes=self._config.image_max_bytes,
            prefix=f"g{group_id}_m{message_id}_",
        )
        text = self._ocr.recognize(img_path)
        if not text:
            logger.info("OCR 无有效文本: url=%s", url)
            return ""
        # OCR 结果文件名与图片同名（含消息前缀），便于对照
        stem = img_path.stem
        content = f"# OCR 结果 | group={group_id} msg={message_id} | 来源={url}\n{text}"
        self._save_text(self._dirs["ocr_result"], stem, content)
        logger.info("OCR 完成: url=%s 文本长度=%d", url, len(text))
        return text

    def _process_web(self, url: str, group_id: int, message_id: int) -> str:
        """网页分支：抓取 → 正文清洗 → 归档网页正文 → 返回正文文本。

        需登录 / JS 强渲染页面由 fetch_web_page 显式识别，按策略跳过并记录日志。
        """
        page = fetch_web_page(
            url, timeout=self._config.web_timeout, max_bytes=self._config.web_max_bytes,
        )
        if not page.text:
            if page.skip_reason:
                logger.info("网页策略跳过: url=%s 原因=%s", url, page.skip_reason)
            else:
                logger.info("网页无有效正文: url=%s", url)
            return ""
        stem = f"g{group_id}_m{message_id}_{url_stem(url)}"
        content = f"# 网页正文 | group={group_id} msg={message_id} | 来源={url}\n{page.text}"
        self._save_text(self._dirs["web_content"], stem, content)
        logger.info("网页抓取完成: url=%s 正文长度=%d", url, len(page.text))
        return page.text
