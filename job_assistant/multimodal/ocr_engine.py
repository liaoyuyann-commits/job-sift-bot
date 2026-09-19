# job_assistant/multimodal/ocr_engine.py

"""图片分支：本地下载图片并 OCR 识别（模块二分支 2）。

设计要点（对应 ARCHITECTURE.md 模块二关键设计）：
    - 图片下载复用 web_fetcher 的限量读取逻辑（超时/大小上限）
    - PaddleOCR 惰性加载：未安装时给出明确中文提示，不静默失败
    - 识别放入守护线程执行并设超时：超时抛 OcrError，落实"OCR 资源可控"
    - 识别失败抛 OcrError，由编排层捕获并标记，不阻断整体流程
    - _flatten_ocr_result 兼容 PaddleOCR 2.x / 3.x 常见返回结构
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from ..errors import ImageDownloadError, OcrError
from .web_fetcher import fetch_url_bytes, image_ext, url_stem

logger = logging.getLogger(__name__)


def download_image(
    url: str,
    save_dir: str | Path,
    *,
    timeout: float = 10.0,
    max_bytes: int = 5_242_880,
    prefix: str = "",
) -> Path:
    """下载图片到本地并返回保存路径。

    Args:
        url: 图片 URL。
        save_dir: 保存目录（不存在时自动创建）。
        timeout: 下载超时（秒）。
        max_bytes: 图片大小上限（字节）。
        prefix: 文件名前缀（建议传消息标识，避免不同消息的同 URL 图片互相覆盖）。

    Raises:
        ImageDownloadError: 网络错误、HTTP 非 2xx、超时或超过大小上限。
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    raw = fetch_url_bytes(
        url, timeout=timeout, max_bytes=max_bytes,
        error_type=ImageDownloadError, too_large_type=ImageDownloadError,
    )
    target = save_dir / f"{prefix}{url_stem(url)}{image_ext(url)}"
    target.write_bytes(raw)
    logger.info("图片下载完成: url=%s -> %s, %d 字节", url, target.name, len(raw))
    return target


class OCREngine:
    """本地 OCR 引擎：惰性加载 PaddleOCR，识别失败/超时抛 OcrError。"""

    def __init__(self, lang: str = "ch", timeout: float = 30.0) -> None:
        self.lang = lang
        self.timeout = timeout      # 识别超时（秒），对应产品方案"OCR 资源可控"
        self._ocr = None            # 惰性加载，首次 recognize 时才初始化

    def _ensure_loaded(self) -> None:
        if self._ocr is not None:
            return
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            raise OcrError(
                "PaddleOCR 未安装：请执行 "
                "python -m pip install paddleocr paddlepaddle "
                "（或关闭 multimodal.enable_ocr 开关）"
            ) from None
        try:
            # 先按 3.x 签名创建；2.x 需要 use_angle_cls 参数，TypeError 时回退
            self._ocr = PaddleOCR(lang=self.lang, show_log=False)
        except TypeError:
            self._ocr = PaddleOCR(lang=self.lang, show_log=False, use_angle_cls=True)
        except Exception as e:
            raise OcrError(f"PaddleOCR 初始化失败: {e}") from e

    def recognize(self, image_path: str | Path) -> str:
        """识别本地图片，返回拼接后的全部文本行；失败或超时抛 OcrError。

        实现说明：PaddleOCR 调用放入守护线程执行，join 超时后标记失败。
        Python 无法强制终止线程，超时线程随进程退出回收（单个识别资源占用有限）。

        Args:
            image_path: 本地图片路径（由 download_image 生成）。

        Returns:
            识别文本（多行）；图片无文字时返回空串。
        """
        result_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        def _run() -> None:
            try:
                self._ensure_loaded()
                result = self._ocr.ocr(str(image_path))
            except OcrError as e:
                # 依赖缺失/初始化失败等：透传原错误码
                result_queue.put(("error", e))
                return
            except Exception:
                # 兼容 3.x 的 predict 接口（2.x 的 ocr 接口在部分版本已废弃）
                try:
                    result = self._ocr.predict(str(image_path))
                except Exception as e:
                    result_queue.put(("error", e))
                    return
            result_queue.put(("ok", result))

        worker = threading.Thread(target=_run, name="ocr-worker", daemon=True)
        worker.start()
        worker.join(timeout=self.timeout)
        if worker.is_alive():
            raise OcrError(f"OCR 识别超时（>{self.timeout} 秒），已标记失败")

        status, payload = result_queue.get_nowait()
        if status == "error":
            if isinstance(payload, OcrError):
                raise payload
            raise OcrError(f"OCR 识别失败: {payload}") from payload
        return _flatten_ocr_result(payload)


def _flatten_ocr_result(result: object) -> str:
    """递归提取 OCR 返回结构中的全部文本行（保序去重）。

    兼容结构：
        - PaddleOCR 2.x: [[[box, (text, score)], ...], ...]
        - PaddleOCR 3.x: {'res': [{'rec_texts': [...]}, ...]} 等
    """
    lines: list[str] = []

    def walk(node: object) -> None:
        if node is None:
            return
        if isinstance(node, str):
            text = node.strip()
            if text:
                lines.append(text)
        elif isinstance(node, dict):
            # 3.x 常见键：res / rec_texts / texts
            for key in ("res", "rec_texts", "texts"):
                walk(node.get(key))
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        # 其余类型（坐标、置信度分数等）不产生文本

    walk(result)
    return "\n".join(dict.fromkeys(lines)).strip()
