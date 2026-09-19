# job_assistant/multimodal/web_fetcher.py

"""URL 分支：网页抓取与 HTML 正文提取（模块二分支 3）。

设计要点（对应 ARCHITECTURE.md 非功能需求"稳定性"）：
    - 全部使用标准库（urllib + html.parser），零第三方依赖
    - 设超时与响应体大小上限，防止资源失控；超限抛 PageTooLargeError
    - 显式识别并跳过需登录、JS 强渲染页面（产品方案 3.2 控制策略）
    - 正文清洗跳过 script/style/head 等标签，只保留可见文本与标题
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)

from ..errors import MultimodalError, PageTooLargeError, WebFetchError

logger = logging.getLogger(__name__)

# 默认请求头：模拟常规浏览器，避免部分站点直接拒绝（仅用于只读抓取）
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "identity",  # urllib 不解压 gzip/br，明确请求原始编码
}

# 备用 UA（重试时切换，部分站点按 UA 指纹拦截）
_FALLBACK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
)

# 重定向次数上限：302 无限循环（常见于外链跳转站）时快速失败，不拖垮请求
_MAX_REDIRECTS = 3


class _LimitedRedirectHandler(HTTPRedirectHandler):
    """限制重定向次数（默认 HTTPRedirectHandler 最多 10 次，循环时仍会耗尽并抛错）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not hasattr(req, "redirect_count"):
            req.redirect_count = {}
        req.redirect_count[newurl] = req.redirect_count.get(newurl, 0) + 1
        if req.redirect_count[newurl] > _MAX_REDIRECTS:
            raise HTTPError(
                newurl, code, "重定向次数超限（可能存在循环）", headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_LimitedRedirectHandler)

# 网页链接提取正则：仅匹配 http/https，避免 QQ 表情链接等干扰
# （与模块一 group_filter 的提取正则保持一致，模块二按产品方案自行提取）
_URL_PATTERN = re.compile(r"https?://[^\s，。；、）】\"']+", re.IGNORECASE)

# 正文清洗时跳过的标签：脚本/样式/内联框架等无有效文本内容
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "svg", "head", "template"}

# 需登录页面特征关键词（跳过识别用，启发式）
_LOGIN_KEYWORDS = ("登录", "登陆", "扫码登录", "请登录", "sign in", "log in", "login")

# JS 强渲染（SPA）页面特征：空壳根节点 + 前端框架挂载（跳过识别用，启发式）
_SPA_MARKERS = ('id="root"', 'id="app"', 'id="app-root"', "createApp")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


class _TextExtractor(HTMLParser):
    """轻量正文提取器：跳过 script/style 等标签，保留标题与可见文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title += text
            return
        if self._skip_depth == 0:
            self._parts.append(text)

    def get_text(self) -> str:
        """返回正文可见文本（按文档顺序拼接）。"""
        return "\n".join(self._parts).strip()


def url_stem(url: str) -> str:
    """从 URL 生成稳定文件名前缀（md5 前 16 位）。

    避免 URL 中的特殊字符与超长路径影响文件名安全。
    """
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def image_ext(url: str) -> str:
    """从 URL 路径推断图片扩展名；无法识别时回退 .png。"""
    ext = Path(urlsplit(url).path).suffix.lower()
    return ext if ext in _IMAGE_EXTS else ".png"


def extract_urls(text: str) -> list[str]:
    """从文本中正则提取 http/https 链接（去重保序）。

    对应产品方案 4.2 URL 分支第 1 步"正则提取链接"。
    """
    return list(dict.fromkeys(_URL_PATTERN.findall(text)))


def _read_limited(resp, max_bytes: int, too_large_type: type[Exception]) -> bytes:
    """限量读取响应体：最多读 max_bytes+1 字节，超限抛 too_large_type。"""
    chunks: list[bytes] = []
    total = 0
    limit = max_bytes + 1
    while total < limit:
        chunk = resp.read(limit - total)
        if not chunk:
            break
        total += len(chunk)
        chunks.append(chunk)
    if total > max_bytes:
        raise too_large_type(f"响应体超过大小上限 {max_bytes} 字节")
    return b"".join(chunks)


def _ascii_url(url: str) -> str:
    """把 URL 中的非 ASCII 字符百分号编码（RFC 3986 保留字符除外）。

    urllib 仅支持 ASCII URL：QQ 群消息里的链接常带中文路径/参数，
    直接请求会抛 UnicodeEncodeError。此函数在请求前完成规范化。
    """
    return quote(url, safe=":/?#[]@!$&'()*+,;=%")


def fetch_url_bytes(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    error_type: type[MultimodalError] = WebFetchError,
    too_large_type: type[MultimodalError] = PageTooLargeError,
    retries: int = 1,
) -> bytes:
    """抓取 URL 并返回响应体字节内容。

    Args:
        url: 目标 URL（http/https；含中文等非 ASCII 字符时自动百分号编码）。
        timeout: 请求超时（秒）。
        max_bytes: 响应体大小上限（字节）。
        error_type: 网络/HTTP 错误时抛出的异常类型。
        too_large_type: 响应超限时抛出的异常类型。
        retries: 网络错误/5xx/超时时的重试次数（重试时切换备用 UA）。

    Raises:
        error_type: 网络错误、HTTP 非 2xx（重试后仍失败）、超时。
        too_large_type: 响应体超过 max_bytes。
    """
    last_error: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        headers = dict(_DEFAULT_HEADERS)
        if attempt > 0:
            headers["User-Agent"] = _FALLBACK_UA  # 换 UA 重试，绕过按 UA 指纹的拦截
        req = Request(_ascii_url(url), headers=headers)
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                # urlopen 对 4xx/5xx 直接抛 HTTPError，能进入此处的均为 2xx
                return _read_limited(resp, max_bytes, too_large_type)
        except HTTPError as e:
            # 5xx 可重试（服务端瞬时故障）；4xx 不重试（请求/反爬拦截，重试无意义）
            if e.code >= 500 and attempt < retries:
                last_error = e
                logger.info("HTTP %d 触发重试: url=%s 第%d次", e.code, url, attempt + 1)
                continue
            raise error_type(f"HTTP {e.code}: {e.reason}") from e
        except URLError as e:
            if attempt < retries:
                last_error = e
                logger.info("网络错误触发重试: url=%s 第%d次 错误=%s", url, attempt + 1, e.reason)
                continue
            raise error_type(f"网络错误: {e.reason}") from e
        except TimeoutError:
            if attempt < retries:
                last_error = e
                logger.info("请求超时触发重试: url=%s 第%d次", url, attempt + 1)
                continue
            raise error_type(f"请求超时（>{timeout} 秒）") from None
        except OSError as e:
            raise error_type(f"网络/IO 错误: {e}") from e
    raise error_type(f"请求失败（已重试 {retries} 次）: {last_error}")


def _decode_bytes(raw: bytes) -> str:
    """解码响应体：优先 utf-8，失败回退 gbk（部分国内站点），兜底 utf-8 容错。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_web_text(html: str) -> str:
    """从 HTML 中提取正文文本（标题 + 可见正文），失败或空页面返回空串。

    Returns:
        "标题：<title>\n<正文>" 格式；无有效内容时返回 ""。
    """
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # HTML 结构异常不应阻断整体流程，记录后按无正文处理
        logger.warning("HTML 解析异常，按无正文处理")
        return ""

    body = parser.get_text()
    if not parser.title and not body:
        return ""
    lines: list[str] = []
    if parser.title:
        lines.append(f"标题：{parser.title}")
    if body:
        lines.append(body)
    return "\n".join(lines).strip()


def page_skip_reason(html: str) -> str | None:
    """正文为空时判断跳过原因：需登录 / JS 强渲染 / 无有效内容。

    启发式检测（产品方案 3.2"不处理需登录、JS 强渲染页面"）：
        - 页面含登录特征关键词 → 需登录页面
        - 页面为 SPA 空壳（根节点 + 前端框架挂载标记）→ JS 强渲染页面
    误判容忍：识别为跳过后，该链接不抓取正文，不影响整体流程。
    """
    if not html.strip():
        return None
    head = html[:8000].lower()
    if any(keyword in head for keyword in _LOGIN_KEYWORDS):
        return "需登录页面"
    if any(marker in head for marker in _SPA_MARKERS):
        return "JS 强渲染页面"
    return None


@dataclass
class WebPageResult:
    """网页抓取结果：正文文本 + 跳过原因（正文为空/策略跳过时）。"""

    text: str                       # 提取到的正文（可能为空串）
    skip_reason: str | None = None  # "需登录页面" / "JS 强渲染页面" / None


def fetch_web_page(url: str, *, timeout: float = 10.0, max_bytes: int = 1_048_576) -> WebPageResult:
    """抓取网页并提取正文；对需登录 / JS 强渲染页面显式识别并跳过。

    Raises:
        WebFetchError: 网络错误、HTTP 非 2xx、超时。
        PageTooLargeError: 响应体超过 max_bytes。
    """
    raw = fetch_url_bytes(
        url, timeout=timeout, max_bytes=max_bytes,
        error_type=WebFetchError, too_large_type=PageTooLargeError,
    )
    html = _decode_bytes(raw)
    reason = page_skip_reason(html)
    text = extract_web_text(html)

    # 需登录页：无论是否提取到少量文本（如"请登录"），一律策略跳过
    if reason == "需登录页面":
        return WebPageResult(text="", skip_reason=reason)
    # JS 强渲染页：服务端无正文，且命中 SPA 特征 → 策略跳过
    if reason == "JS 强渲染页面" and not text:
        return WebPageResult(text="", skip_reason=reason)
    if not text:
        return WebPageResult(text="")
    return WebPageResult(text=text)


def fetch_web_text(url: str, *, timeout: float = 10.0, max_bytes: int = 1_048_576) -> str:
    """抓取网页并提取正文文本（空串表示无正文或需登录/JS 渲染跳过）。

    Args:
        url: 目标 URL。
        timeout: 请求超时（秒）。
        max_bytes: 响应体大小上限（字节）。

    Raises:
        WebFetchError: 网络错误、HTTP 非 2xx、超时。
        PageTooLargeError: 响应体超过 max_bytes。
    """
    return fetch_web_page(url, timeout=timeout, max_bytes=max_bytes).text
