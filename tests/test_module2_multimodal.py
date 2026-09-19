# tests/test_module2_multimodal.py

"""模块二（多模态消息解析模块）自测脚本。

覆盖范围：
    1. text_branch 文本直传
    2. extract_web_text HTML 正文清洗（标题 / 正文 / 跳过 script/style）
    3. extract_urls 链接正则提取（去重保序）
    4. fetch_web_page 需登录 / JS 强渲染页面显式识别跳过
    5. fetch_web_text 网页抓取（本地 HTTP：正常 / 404 / 大小上限）
    6. download_image 图片下载（本地 HTTP、404）
    7. OCREngine 识别超时 / 识别失败（不依赖 PaddleOCR 环境）
    8. _flatten_ocr_result OCR 结果兼容解析（2.x / 3.x 结构）
    9. MultimodalProcessor 编排（合并 / 开关跳过 / 失败标记 / 落盘）
    10. 新增配置字段校验（enable_ocr / ocr_lang / ocr_timeout / web_timeout）

运行方式：python tests/test_module2_multimodal.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 保证可直接从项目根目录运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_assistant.config.settings import DataConfig, MultimodalConfig, load_config  # noqa: E402
from job_assistant.errors import (  # noqa: E402
    ConfigError, ImageDownloadError, OcrError, PageTooLargeError, WebFetchError,
)
from job_assistant.listener.group_filter import GroupMessage  # noqa: E402
from job_assistant.multimodal.ocr_engine import OCREngine, _flatten_ocr_result, download_image  # noqa: E402
from job_assistant.multimodal.processor import MultimodalProcessor  # noqa: E402
from job_assistant.multimodal.text_branch import process_text  # noqa: E402
from job_assistant.multimodal.web_fetcher import (  # noqa: E402
    extract_urls, extract_web_text, fetch_web_page, fetch_web_text,
)

_PASS = 0
_FAIL = 0

# 最小 1x1 PNG（测试图片下载用，OCR 走替身不解析内容）
_MIN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f030005fe02fea72fc9a70000000049454e44ae426082"
)

# 测试用 HTML：包含标题、正文、script/style（应被清洗跳过）
_HTML = (
    "<html><head><title>后端工程师招聘</title><style>p{color:red}</style></head>"
    "<body><h1>公司A</h1><p>Java 后端 20k，坐标北京</p>"
    "<script>var track = 1;</script><div>投递邮箱: hr@a.com</div></body></html>"
)

# 需登录页面（正文仅登录提示，应被策略跳过）
_LOGIN_HTML = (
    "<html><head><title>登录</title></head><body>请登录后查看完整招聘信息</body></html>"
)

# JS 强渲染（SPA）页面：空壳根节点 + 脚本，无服务端正文
_SPA_HTML = (
    '<html><body><div id="root"></div>'
    "<script>window.__DATA__ = {};</script></body></html>"
)


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} {detail}")


class _Handler(BaseHTTPRequestHandler):
    """本地模拟站点：正常网页 / 登录页 / SPA 页 / 超限 / 图片，其余 404。"""

    def do_GET(self) -> None:
        if self.path == "/job.html":
            body = _HTML.encode("utf-8")
        elif self.path == "/login.html":
            body = _LOGIN_HTML.encode("utf-8")
        elif self.path == "/spa.html":
            body = _SPA_HTML.encode("utf-8")
        elif self.path == "/big.html":
            body = b"x" * 2048
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

    def log_message(self, *args) -> None:  # 屏蔽测试期访问日志
        pass


def start_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_text_branch() -> None:
    print("== text_branch 文本直传 ==")
    check("文本透传", process_text("hello 招聘") == "hello 招聘")
    check("空文本透传", process_text("") == "")


def test_extract_web_text() -> None:
    print("== extract_web_text HTML 正文清洗 ==")
    text = extract_web_text(_HTML)
    check("标题提取", "后端工程师招聘" in text)
    check("正文提取", "Java 后端 20k" in text and "投递邮箱" in text)
    check("跳过 script/style", "var track" not in text and "color:red" not in text)
    check("空 HTML 返回空", extract_web_text("<html></html>") == "")


def test_extract_urls() -> None:
    print("== extract_urls 链接正则提取 ==")
    text = "详情见 https://a.com/job/1 和 https://a.com/job/1 及 http://b.cn/x"
    check("提取去重保序", extract_urls(text) == ["https://a.com/job/1", "http://b.cn/x"])
    check("无链接返回空", extract_urls("没有链接的文本") == [])


def test_fetch_web_page() -> None:
    print("== fetch_web_page 登录/JS 渲染页识别 ==")
    server = start_server()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = fetch_web_page(f"{base}/login.html", timeout=5, max_bytes=1_048_576)
        check("登录页识别跳过", page.text == "" and page.skip_reason == "需登录页面")

        page = fetch_web_page(f"{base}/spa.html", timeout=5, max_bytes=1_048_576)
        check("JS 渲染页识别跳过", page.text == "" and page.skip_reason == "JS 强渲染页面")

        page = fetch_web_page(f"{base}/job.html", timeout=5, max_bytes=1_048_576)
        check("正常页不跳过", page.text != "" and page.skip_reason is None)
    finally:
        server.shutdown()


def test_fetch_web_text() -> None:
    print("== fetch_web_text 网页抓取 ==")
    server = start_server()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        text = fetch_web_text(f"{base}/job.html", timeout=5, max_bytes=1_048_576)
        check("正常抓取提取正文", "后端工程师招聘" in text and "Java 后端 20k" in text)

        try:
            fetch_web_text(f"{base}/missing", timeout=5, max_bytes=1_048_576)
            check("404 抛 WebFetchError", False)
        except WebFetchError:
            check("404 抛 WebFetchError", True)

        try:
            fetch_web_text(f"{base}/big.html", timeout=5, max_bytes=1024)
            check("超限抛 PageTooLargeError", False)
        except PageTooLargeError:
            check("超限抛 PageTooLargeError", True)
    finally:
        server.shutdown()


def test_download_image() -> None:
    print("== download_image 图片下载 ==")
    server = start_server()
    base = f"http://127.0.0.1:{server.server_port}"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            target = download_image(f"{base}/img.png", tmp, timeout=5, max_bytes=5_242_880)
            check("图片下载保存", target.is_file() and target.read_bytes() == _MIN_PNG)
            check("扩展名为 png", target.suffix == ".png")

            target = download_image(
                f"{base}/img.png", tmp, timeout=5, max_bytes=5_242_880, prefix="g1_m2_",
            )
            check("前缀防覆盖", target.name.startswith("g1_m2_"))

            try:
                download_image(f"{base}/missing", tmp, timeout=5, max_bytes=5_242_880)
                check("404 抛 ImageDownloadError", False)
            except ImageDownloadError:
                check("404 抛 ImageDownloadError", True)
        finally:
            server.shutdown()


def test_ocr_timeout() -> None:
    print("== OCREngine 超时 / 识别失败 ==")

    class _SlowOCR(OCREngine):
        """替身：_ensure_loaded 注入假引擎，识别 sleep 模拟卡死。"""

        def _ensure_loaded(self) -> None:
            class _Dummy:
                def ocr(self, path: str):
                    time.sleep(10)
                    return None

            self._ocr = _Dummy()

    engine = _SlowOCR(timeout=0.5)
    try:
        engine.recognize("fake.png")
        check("OCR 超时抛 OcrError", False)
    except OcrError as e:
        check("OCR 超时抛 OcrError", "超时" in str(e))

    class _FailOCR(OCREngine):
        """替身：注入无识别能力的对象，模拟识别失败。"""

        def _ensure_loaded(self) -> None:
            self._ocr = object()

    engine = _FailOCR(timeout=5)
    try:
        engine.recognize("fake.png")
        check("OCR 失败抛 OcrError", False)
    except OcrError:
        check("OCR 失败抛 OcrError", True)


def test_flatten_ocr() -> None:
    print("== _flatten_ocr_result 兼容解析 ==")
    # PaddleOCR 2.x：[[[box, (text, score)], ...]]
    r2 = [[[[0, 0, 10, 10], ("文本一", 0.99)], [[0, 0, 10, 20], ("文本二", 0.95)]]]
    check("2.x 结构解析", _flatten_ocr_result(r2) == "文本一\n文本二")
    # PaddleOCR 3.x：{'res': [{'rec_texts': [...]}]}
    r3 = {"res": [{"rec_texts": ["行一", "行二"]}]}
    check("3.x 结构解析", _flatten_ocr_result(r3) == "行一\n行二")
    check("空结果", _flatten_ocr_result(None) == "" and _flatten_ocr_result([]) == "")


class FakeOCR:
    """测试用 OCR 引擎替身：返回固定文本并记录调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def recognize(self, path: str) -> str:
        self.calls.append(str(path))
        return "OCR识别文本：Java 后端工程师"


def _make_cfg(enable_ocr: bool = True, enable_web_fetch: bool = True) -> MultimodalConfig:
    return MultimodalConfig(
        enable_ocr=enable_ocr, enable_web_fetch=enable_web_fetch,
        web_timeout=5, web_max_bytes=1_048_576, image_max_bytes=5_242_880,
    )


def test_processor() -> None:
    print("== MultimodalProcessor 编排（正常链路）==")
    server = start_server()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = MultimodalProcessor(_make_cfg(), DataConfig(root_dir=str(root / "data")), ocr_engine=FakeOCR())

            # 链接必须出现在文本中：模块二 URL 分支从 raw_text 正则提取
            msg = GroupMessage(
                message_id=101, group_id=111, user_id=10001, timestamp=1779000000,
                raw_text=f"校招宣讲：公司A 详情见 {base}/job.html",
                image_urls=[f"{base}/img.png"],
            )
            result = proc.process(msg)

            check("合并含原始文本", "校招宣讲" in result.merged_text)
            check("合并含 OCR 文本", "OCR识别文本" in result.merged_text)
            check("合并含网页正文", "Java 后端 20k" in result.merged_text)
            check("无失败分支", not result.has_errors)

            # 数据分目录落盘检查
            check("raw_msg 归档", (root / "data" / "raw_msg" / "g111_m101.txt").is_file())
            check("图片落盘（带消息前缀）",
                  any((root / "data" / "image_raw").glob("g111_m101_*.png")))
            check("OCR 结果归档", any((root / "data" / "ocr_result").glob("g111_m101_*.txt")))
            check("网页正文归档", any((root / "data" / "web_content").glob("g111_m101_*.txt")))
    finally:
        server.shutdown()


def test_processor_skip_switches() -> None:
    print("== MultimodalProcessor 开关跳过逻辑 ==")
    server = start_server()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # enable_ocr=False：图片分支整体跳过（不下载、不识别）
            # 注意：关闭 OCR 时不注入 OCR 引擎，模拟生产行为（注入优先于开关，仅测试替身用）
            proc = MultimodalProcessor(
                _make_cfg(enable_ocr=False), DataConfig(root_dir=str(root / "data")),
            )
            msg = GroupMessage(
                message_id=201, group_id=111, user_id=1, timestamp=1,
                raw_text=f"文本 {base}/job.html",
                image_urls=[f"{base}/img.png"],
            )
            result = proc.process(msg)
            check("关闭 OCR 不产出 OCR 文本", not result.ocr_texts and "OCR识别文本" not in result.merged_text)
            check("关闭 OCR 不下载图片", not any((root / "data" / "image_raw").glob("g111_m201_*.png")))

            # enable_web_fetch=False：网页分支整体跳过
            proc = MultimodalProcessor(
                _make_cfg(enable_web_fetch=False), DataConfig(root_dir=str(root / "data")), ocr_engine=FakeOCR(),
            )
            msg = GroupMessage(
                message_id=202, group_id=111, user_id=1, timestamp=1,
                raw_text=f"文本 {base}/job.html",
                image_urls=[f"{base}/img.png"],
            )
            result = proc.process(msg)
            check("关闭网页抓取不产出正文", not result.web_texts and "Java 后端 20k" not in result.merged_text)
            check("关闭网页抓取不归档", not any((root / "data" / "web_content").glob("g111_m202_*.txt")))
    finally:
        server.shutdown()


def test_processor_failure_paths() -> None:
    print("== MultimodalProcessor 失败分支标记（不阻断整体）==")
    server = start_server()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = DataConfig(root_dir=str(root / "data"))

            # 图片下载失败（404）→ MUL.DOWN.001；网页正常
            proc = MultimodalProcessor(_make_cfg(), data, ocr_engine=FakeOCR())
            msg = GroupMessage(
                message_id=301, group_id=111, user_id=1, timestamp=1,
                raw_text=f"文本 {base}/job.html",
                image_urls=[f"{base}/missing.png"],
            )
            result = proc.process(msg)
            check("图片下载失败标记", result.has_errors and any("MUL.DOWN.001" in e for e in result.errors))
            check("失败不阻断网页分支", "Java 后端 20k" in result.merged_text)

            # OCR 识别失败 → MUL.OCR.001
            class _FailOCR:
                def recognize(self, path: str) -> str:
                    raise OcrError("模拟识别失败")

            proc = MultimodalProcessor(_make_cfg(), data, ocr_engine=_FailOCR())
            msg = GroupMessage(
                message_id=302, group_id=111, user_id=1, timestamp=1,
                raw_text="文本",
                image_urls=[f"{base}/img.png"],
            )
            result = proc.process(msg)
            check("OCR 失败标记", result.has_errors and any("MUL.OCR.001" in e for e in result.errors))

            # 网页 404 → MUL.FETCH.001；图片正常
            proc = MultimodalProcessor(_make_cfg(), data, ocr_engine=FakeOCR())
            msg = GroupMessage(
                message_id=303, group_id=111, user_id=1, timestamp=1,
                raw_text=f"文本 {base}/missing",
                image_urls=[f"{base}/img.png"],
            )
            result = proc.process(msg)
            check("网页失败标记", result.has_errors and any("MUL.FETCH.001" in e for e in result.errors))
            check("失败不阻断图片分支", "OCR识别文本" in result.merged_text)
    finally:
        server.shutdown()


def test_config_validation() -> None:
    print("== 新增配置字段校验 ==")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        cases = [
            ("multimodal:\n  enable_ocr: \"yes\"\n", "enable_ocr 非布尔"),
            ("multimodal:\n  ocr_lang: 123\n", "ocr_lang 非字符串"),
            ("multimodal:\n  web_timeout: -1\n", "web_timeout 非正数"),
            ("multimodal:\n  ocr_timeout: 0\n", "ocr_timeout 非正数"),
        ]
        for i, (content, name) in enumerate(cases):
            path = root / f"bad{i}.yaml"
            path.write_text("napcat:\n  ws_url: \"ws://127.0.0.1:3001\"\n" + content, encoding="utf-8")
            try:
                load_config(path)
                check(f"{name} 抛 ConfigError", False)
            except ConfigError:
                check(f"{name} 抛 ConfigError", True)

        # 合法配置：新字段缺省使用默认值
        good = root / "good.yaml"
        good.write_text("napcat:\n  ws_url: \"ws://127.0.0.1:3001\"\n", encoding="utf-8")
        cfg = load_config(good)
        check("新字段缺省默认值",
              cfg.multimodal.enable_ocr is True and cfg.multimodal.ocr_timeout == 30.0)


def main() -> None:
    test_text_branch()
    test_extract_web_text()
    test_extract_urls()
    test_fetch_web_page()
    test_fetch_web_text()
    test_download_image()
    test_ocr_timeout()
    test_flatten_ocr()
    test_processor()
    test_processor_skip_switches()
    test_processor_failure_paths()
    test_config_validation()
    print(f"\n结果: {_PASS} 通过, {_FAIL} 失败")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
