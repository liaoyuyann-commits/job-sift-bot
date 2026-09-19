# tests/test_module2_integration.py

"""模块二集成自测：模拟 NapCat 正向 WebSocket + 本地 HTTP 站点，验证完整链路。

链路验证：NapCat 事件 → 群白名单过滤 → 多模态处理（文本 / 图片 OCR / 网页抓取）
           → 合并完整文本 → 数据分目录落盘。
OCR 使用注入的 FakeOCR，避免依赖 PaddleOCR 环境。
运行方式：python tests/test_module2_integration.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.config.settings import (  # noqa: E402
    AppConfig, DataConfig, MultimodalConfig, NapCatConfig,
)
from job_assistant.listener.napcat_client import NapCatListener  # noqa: E402
from job_assistant.multimodal.processor import MultimodalProcessor, MultimodalResult  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402

HOST, PORT = "127.0.0.1", 39002

_MIN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f030005fe02fea72fc9a70000000049454e44ae426082"
)

_HTML = (
    "<html><head><title>后端工程师招聘</title></head>"
    "<body><h1>公司A</h1><p>Java 后端 20k，坐标北京</p></body></html>"
)


class _SiteHandler(BaseHTTPRequestHandler):
    """本地站点：提供招聘网页与图片。"""

    def do_GET(self) -> None:
        if self.path == "/job.html":
            body = _HTML.encode("utf-8")
        elif self.path == "/img.png":
            body = _MIN_PNG
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


class FakeOCR:
    """OCR 替身：返回固定文本。"""

    def recognize(self, path: str) -> str:
        return "OCR识别文本：图像海报 Java 后端"


def make_event(group_id: int, text: str, image_urls: list[str], urls: list[str]) -> str:
    """构造含文本 + 图片 + 网页链接的 OneBot v11 群消息事件。"""
    message: list[dict] = [{"type": "text", "data": {"text": text}}]
    for url in image_urls:
        message.append({"type": "image", "data": {"url": url}})
    return json.dumps({
        "post_type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": 10001,
        "message_id": 555,
        "time": 1779000000,
        "message": message,
    })


async def fake_napcat_server(ready: threading.Event, site_base: str) -> None:
    """模拟 NapCat 正向 WS：推送一条多模态消息后保持连接。"""

    async def handler(ws) -> None:
        # 网页链接必须出现在文本段中，GroupMessage.urls 由正则从文本提取
        await ws.send(make_event(
            111,
            f"【校招】公司A后端工程师，欢迎投递 详情见 {site_base}/job.html",
            image_urls=[f"{site_base}/img.png"],
            urls=[],
        ))
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass

    async with serve(handler, HOST, PORT):
        ready.set()
        await asyncio.Future()


def main() -> int:
    print("== 模块二集成链路验证（模拟 NapCat + 本地站点）==")

    # 本地站点：网页 + 图片
    site = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    site_base = f"http://127.0.0.1:{site.server_port}"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = AppConfig(
            napcat=NapCatConfig(ws_url=f"ws://{HOST}:{PORT}", reconnect_interval=1),
            monitor_group_ids=[111],
            multimodal=MultimodalConfig(
                enable_ocr=True, enable_web_fetch=True,
                web_timeout=5, web_max_bytes=1_048_576, image_max_bytes=5_242_880,
            ),
            data=DataConfig(root_dir=str(root / "data")),
        )
        processor = MultimodalProcessor(cfg.multimodal, cfg.data, ocr_engine=FakeOCR())

        results: list[MultimodalResult] = []
        got = threading.Event()

        def on_message(msg) -> None:
            results.append(processor.process(msg))
            got.set()

        # 模拟 NapCat 服务端
        server_ready = threading.Event()
        server_thread = threading.Thread(
            target=lambda: asyncio.run(fake_napcat_server(server_ready, site_base)),
            daemon=True,
        )
        server_thread.start()
        if not server_ready.wait(timeout=5):
            print("  [FAIL] 模拟 NapCat 启动超时")
            site.shutdown()
            return 1

        listener = NapCatListener(cfg, on_message=on_message)
        listener.start()
        passed = got.wait(timeout=8)
        listener.stop()
        site.shutdown()

        if not passed or not results:
            print("  [FAIL] 未在超时时间内收到处理结果")
            return 1

        merged = results[0].merged_text
        ok_text = "【校招】公司A后端工程师" in merged
        ok_ocr = "OCR识别文本" in merged
        ok_web = "Java 后端 20k" in merged
        ok_files = (
            (root / "data" / "raw_msg").is_dir()
            and any((root / "data" / "image_raw").glob("*.png"))
            and any((root / "data" / "ocr_result").glob("*.txt"))
            and any((root / "data" / "web_content").glob("*.txt"))
        )
        print(f"  [{'PASS' if ok_text else 'FAIL'}] 文本直传合并")
        print(f"  [{'PASS' if ok_ocr else 'FAIL'}] 图片 OCR 合并")
        print(f"  [{'PASS' if ok_web else 'FAIL'}] 网页正文合并")
        print(f"  [{'PASS' if ok_files else 'FAIL'}] 数据分目录落盘")
        return 0 if (ok_text and ok_ocr and ok_web and ok_files) else 1


if __name__ == "__main__":
    sys.exit(main())
