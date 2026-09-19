# job_assistant/research/company_research.py

"""公司外部信息联网检索（问题修复 5）+ 残缺字段自动搜索补全（问题修复 4）。

目标（问题修复 5）：为岗位匹配打分补充【公司外部检索资料】——公司业务、规模、
      员工风评、WLB、经营状况等招聘原文不包含的信息（产品方案：招聘原文本身
      不会自带 WLB 信息，该部分必须外部联网获取）。

目标（问题修复 4）：抽取后缺公司名 / 岗位名等核心字段时，不再直接判定残缺跳过，
      而是先依据已有信息联网搜索补全缺失字段，同时带回公司风评资料；
      搜索失败 / 无法确认时才保留【基础信息残缺】标记（原兜底逻辑）。

检索策略（多来源，失败自动降级不阻断打分主流程）：
    1. 搜索引擎（Bing 网页搜索）：检索「公司名 岗位名 怎么样 加班 工作体验 评价」，
       解析前 _SEARCH_TOP_N 条结果（标题 + 摘要 + 来源链接）——员工风评、
       WLB、舆情等外部信息主要来自这里；
    2. 百度百科词条：公司业务/规模/经营的权威简介（结构化程度高，作补充）；
    3. 全部失败 / 无结果：返回空串，由打分侧标注"公司外部信息未自动联网
       核验，WLB 需人工自查"，日报同时生成可一键复制的搜索关键词
       （公司名 + 岗位名 + 风评 WLB）。

实现：
    - 检索源为公开网页，复用模块二 web_fetcher 的抓取能力
      （UA/超时/大小上限/反爬识别/重定向限制），不依赖第三方搜索 API Key
    - 抓取 → 正文/结果清洗 → 截断（max_chars 控制 token 消耗）→ 返回摘要文本
"""

from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import quote

from ..multimodal.web_fetcher import _decode_bytes, fetch_url_bytes, fetch_web_page
from .browser_fetch import browser_fetch_links, browser_fetch_texts
from ..parser.extractor import extract_json_object
from ..parser.schema import MISSING

logger = logging.getLogger(__name__)

# 搜索引擎：Bing 网页搜索（公开、无 API Key、实测可抓取；结果含员工风评/WLB 相关内容）
_SEARCH_URL = "https://www.bing.com/search?q={query}&setlang=zh-hans&count=20"
# 搜狗网页搜索（备用源：中文分词准确，但高频请求会间歇性触发验证码拦截）
_SOGOU_URL = "https://www.sogou.com/web?query={query}&num=10"
# 神马搜索（残缺补全主源：阿里系分词准确、移动端反爬弱、结果以稳定 JSON 内嵌返回，
# 实测命中"大疆-机器人算法工程师(SLAM方向)-校园招聘"等具体公司与岗位）
_SMCN_URL = "https://m.sm.cn/s?q={query}"
# 360 搜索（备用源：中文分词准确，但高频/无 cookie 会间歇性触发验证码）
_360_URL = "https://www.so.com/s?q={query}"
# 百度搜索（残缺补全兜底：结果量大，含高校就业网/公司招聘页，标题信息充分）
_BAIDU_URL = "https://www.baidu.com/s?wd={query}&rn=20"
# 风评检索词模板（多路定向）：通用口碑 + 牛客网员工评价 + 小红书真实体验
# 公司名加引号精确匹配，避免"厦门松霖机器人"被分词成"厦门"搜出无关结果；
# 牛客/小红书内容被搜索引擎索引，命中其页面的标题/摘要即含员工评价片段
_SEARCH_QUERIES = (
    '"{company}" {job} 怎么样 加班 工作体验 评价',
    '"{company}" {job} 牛客 评价 怎么样',
    '"{company}" site:nowcoder.com',
    '"{company}" {job} 小红书 工作 体验 加班',
    '"{company}" site:xiaohongshu.com',
)

# 员工评价平台特征（牛客网点评 / 小红书分享），用于来源标记与优先级
_PLATFORM_RULES = (
    ("牛客", "nowcoder.com"),
    ("小红书", ("xiaohongshu.com", "xhslink.com")),
)
# 搜索结果最多取前 N 条（控制上下文长度）
_SEARCH_TOP_N = 6

# 回退源：百度百科词条（公司业务/规模/经营简介，反爬相对宽松）
_BAIKE_URL = "https://baike.baidu.com/item/{name}"

# LLM 补全字段归一化：模型可能输出"信息未提及"（无【】括号）、"未知"、"无"等变体，
# 一律归一为 MISSING，防止把无效值当成有效岗位信息进入打分（问题修复 4 边界）
_UNKNOWN_WORDS = ("信息未提及", "未提及", "未知", "无", "none", "null", "n/a", "na", "-")

# 官方站点特征：公司官网/商城页不含员工风评，检索结果中跳过
_OFFICIAL_HINTS = ("官网", "官方商城", "消费者业务", "VMALL", "产品中心")

# 响应体上限（字节，2MB；搜索页与百科词条页面均较大）
_MAX_BYTES = 2_097_152

# Bing 结果块与标题/链接/摘要提取
_BING_ALGO_RE = re.compile(r'<li class="b_algo".*?</li>', re.DOTALL)
_BING_TITLE_RE = re.compile(r"<h2[^>]*>\s*<a[^>]*>(.*?)</a>", re.DOTALL)
_BING_HREF_RE = re.compile(r'<a[^>]*href="(https?://[^"]+)"', re.I)
_BING_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# 360 结果块：<li class="res-list"> 起，到 </li> 止；标题 h3.res-title>a，摘要 p.res-desc
_360_BLOCK_RE = re.compile(r'<li class="res-list".*?</li>', re.DOTALL)
_360_TITLE_RE = re.compile(
    r'<h3[^>]*class="res-title[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', re.DOTALL,
)
_360_HREF_RE = re.compile(
    r'<h3[^>]*class="res-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"', re.DOTALL,
)
_360_SNIPPET_RE = re.compile(r'<p class="res-desc"[^>]*>(.*?)</p>', re.DOTALL)

# 百度结果标题：<h3 class="c-title ...">，摘要 class="c-abstract"（旧版）或 content-right（新版，尽力而为）
_BAIDU_TITLE_RE = re.compile(
    r'<h3[^>]*class="[^"]*c-title[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', re.DOTALL,
)
_BAIDU_HREF_RE = re.compile(
    r'<h3[^>]*class="[^"]*c-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"', re.DOTALL,
)
_BAIDU_SNIPPET_RE = re.compile(
    r'<span class="(?:content-right_8Zs40|c-abstract)[^"]*"[^>]*>(.*?)</span>', re.DOTALL,
)
# 搜狗结果块：<div class="vrwrap" ...> 起，到下一个 vrwrap 止
_SOGOU_BLOCK_SPLIT = re.compile(r'<div class="vrwrap"')
_SOGOU_TITLE_RE = re.compile(
    r'<h3[^>]*class="vr-title"[^>]*>\s*<a[^>]*>(.*?)</a>', re.DOTALL,
)
_SOGOU_HREF_RE = re.compile(
    r'<h3[^>]*class="vr-title"[^>]*>\s*<a[^>]*href="([^"]+)"', re.DOTALL,
)
_SOGOU_SUMMARY_RE = re.compile(
    r'id="cacheresult_summary_\d+"[^>]*>(.*?)</div>', re.DOTALL,
)

# 神马搜索结果：<script type="application/json" id="s-data-nature_result_..."> 内嵌 JSON，
# initialData 含 title / desc / url / source 结构化字段
_SMCN_JSON_RE = re.compile(
    r'<script type="application/json" id="s-data-nature_result_[^"]*"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _clean(text: str) -> str:
    """去除 HTML 标签并压缩空白（Bing 标题/摘要常含 <strong> 高亮标签）。"""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text)).strip()


def _parse_bing(html: str) -> list[dict]:
    """解析 Bing 搜索结果：返回 [{title, url, snippet}] 列表。

    解析失败/无结果返回空列表（调用方降级，不抛异常）。
    """
    items: list[dict] = []
    for block in _BING_ALGO_RE.findall(html):
        title = _clean(_BING_TITLE_RE.search(block).group(1)) if _BING_TITLE_RE.search(block) else ""
        snippet = _clean(_BING_SNIPPET_RE.search(block).group(1)) if _BING_SNIPPET_RE.search(block) else ""
        href = _BING_HREF_RE.search(block)
        if not title or not href:
            continue
        # 跳过公司官网/官方商城等不含员工风评的官方页面
        if any(kw.lower() in (title + snippet).lower() for kw in _OFFICIAL_HINTS):
            continue
        items.append({"title": title, "url": href.group(1), "snippet": snippet})
        if len(items) >= _SEARCH_TOP_N:
            break
    return items


def _search(query: str, timeout: float) -> list[dict]:
    """执行 Bing 网页搜索（自定义 query），返回解析后的结果列表；失败返回空列表。"""
    url = _SEARCH_URL.format(query=quote(query))
    try:
        raw = fetch_url_bytes(
            url, timeout=timeout, max_bytes=_MAX_BYTES,
            error_type=Exception, too_large_type=Exception,
        )
    except Exception as e:  # noqa: BLE001 - 搜索失败仅降级，绝不阻断主流程
        logger.info("搜索引擎抓取失败: query=%s 错误=%s", query[:40], e)
        return []
    return _parse_bing(_decode_bytes(raw))


def _parse_sogou(html: str) -> list[dict]:
    """解析搜狗搜索结果：返回 [{title, url, snippet}]；失败/无结果返回空列表。"""
    items: list[dict] = []
    matches = list(_SOGOU_BLOCK_SPLIT.finditer(html))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        block = html[match.start():end]
        title_match = _SOGOU_TITLE_RE.search(block)
        if not title_match:
            continue
        href_match = _SOGOU_HREF_RE.search(block)
        if not href_match:
            continue
        url = href_match.group(1)
        if url.startswith("/link?url="):
            url = "https://www.sogou.com" + url
        snippet_match = _SOGOU_SUMMARY_RE.search(block)
        items.append({
            "title": _clean(title_match.group(1)),
            "url": url,
            "snippet": _clean(snippet_match.group(1)) if snippet_match else "",
        })
        if len(items) >= _SEARCH_TOP_N:
            break
    return items


# 搜索源节流：搜狗/360/百度等公开搜索引擎对高频或无 cookie 请求会间歇性触发
# 验证码拦截（返回 5~7KB 验证码页）。两次搜索请求至少间隔数秒可大幅降低触发。
# 生产场景每条残缺消息仅 1 次补全检索、每条岗位仅 1 次风评检索，天然低频；
# 此处仅防测试/异常重试连发触发限流。
_SEARCH_MIN_INTERVAL = 5.0
_LAST_SEARCH_TS = 0.0


def _throttle() -> None:
    """进程级节流：距上次搜索请求不足间隔时 sleep 补齐。"""
    global _LAST_SEARCH_TS
    now = time.monotonic()
    wait = _SEARCH_MIN_INTERVAL - (now - _LAST_SEARCH_TS)
    if wait > 0:
        time.sleep(wait)
    _LAST_SEARCH_TS = time.monotonic()


def _search_sogou(query: str, timeout: float) -> list[dict]:
    """执行搜狗网页搜索（备用源：中文分词准确，但高频会触发验证码）；失败/无结果返回空列表。"""
    _throttle()
    url = _SOGOU_URL.format(query=quote(query))
    try:
        raw = fetch_url_bytes(
            url, timeout=timeout, max_bytes=_MAX_BYTES,
            error_type=Exception, too_large_type=Exception,
        )
    except Exception as e:  # noqa: BLE001 - 搜索失败仅降级
        logger.info("搜狗检索失败: query=%s 错误=%s", query[:40], e)
        return []
    return _parse_sogou(_decode_bytes(raw))


def _parse_smcn(html: str) -> list[dict]:
    """解析神马搜索结果（JSON 内嵌）：返回 [{title, url, snippet}]；失败/无结果返回空列表。"""
    items: list[dict] = []
    for match in _SMCN_JSON_RE.findall(html):
        try:
            data = json.loads(match)
            init = data.get("data", {}).get("initialData", {}) or {}
            title = _clean(init.get("title") or "")
            url = init.get("url") or ""
            if not title or not url:
                continue
            items.append({
                "title": title,
                "url": url,
                "snippet": _clean(init.get("desc") or ""),
            })
        except Exception:  # noqa: BLE001 - 单条解析失败跳过，不影响整体
            continue
        if len(items) >= _SEARCH_TOP_N:
            break
    return items


def _search_smcn(query: str, timeout: float) -> list[dict]:
    """执行神马网页搜索（残缺补全主源，中文分词准确、反爬弱）；失败/无结果返回空列表。"""
    _throttle()
    url = _SMCN_URL.format(query=quote(query))
    try:
        raw = fetch_url_bytes(
            url, timeout=timeout, max_bytes=_MAX_BYTES,
            error_type=Exception, too_large_type=Exception,
        )
    except Exception as e:  # noqa: BLE001 - 搜索失败仅降级
        logger.info("神马检索失败: query=%s 错误=%s", query[:40], e)
        return []
    return _parse_smcn(_decode_bytes(raw))


def _parse_360(html: str) -> list[dict]:
    """解析 360 搜索结果：返回 [{title, url, snippet}]；失败/无结果返回空列表。"""
    items: list[dict] = []
    for block in _360_BLOCK_RE.findall(html):
        title_match = _360_TITLE_RE.search(block)
        href_match = _360_HREF_RE.search(block)
        if not title_match or not href_match:
            continue
        snippet_match = _360_SNIPPET_RE.search(block)
        items.append({
            "title": _clean(title_match.group(1)),
            "url": href_match.group(1),
            "snippet": _clean(snippet_match.group(1)) if snippet_match else "",
        })
        if len(items) >= _SEARCH_TOP_N:
            break
    return items


def _search_360(query: str, timeout: float) -> list[dict]:
    """执行 360 网页搜索（残缺补全主源，中文分词准确）；失败/无结果返回空列表。"""
    _throttle()
    url = _360_URL.format(query=quote(query))
    try:
        raw = fetch_url_bytes(
            url, timeout=timeout, max_bytes=_MAX_BYTES,
            error_type=Exception, too_large_type=Exception,
        )
    except Exception as e:  # noqa: BLE001 - 搜索失败仅降级
        logger.info("360 检索失败: query=%s 错误=%s", query[:40], e)
        return []
    return _parse_360(_decode_bytes(raw))


def _parse_baidu(html: str) -> list[dict]:
    """解析百度搜索结果：返回 [{title, url, snippet}]；失败/无结果返回空列表。"""
    items: list[dict] = []
    for title_match in _BAIDU_TITLE_RE.finditer(html):
        href_match = _BAIDU_HREF_RE.search(title_match.group(0))
        if not href_match:
            continue
        # 摘要需在标题之后、同结果块内截取（百度无稳定块边界，按标题位置向后找首个摘要）
        tail = html[title_match.end():title_match.end() + 1500]
        snippet_match = _BAIDU_SNIPPET_RE.search(tail)
        items.append({
            "title": _clean(title_match.group(1)),
            "url": href_match.group(1),
            "snippet": _clean(snippet_match.group(1)) if snippet_match else "",
        })
        if len(items) >= _SEARCH_TOP_N:
            break
    return items


def _search_baidu(query: str, timeout: float) -> list[dict]:
    """执行百度网页搜索（残缺补全兜底）；失败/无结果返回空列表。"""
    _throttle()
    url = _BAIDU_URL.format(query=quote(query))
    try:
        raw = fetch_url_bytes(
            url, timeout=timeout, max_bytes=_MAX_BYTES,
            error_type=Exception, too_large_type=Exception,
        )
    except Exception as e:  # noqa: BLE001 - 搜索失败仅降级
        logger.info("百度检索失败: query=%s 错误=%s", query[:40], e)
        return []
    return _parse_baidu(_decode_bytes(raw))


def _platform_of(url: str) -> str | None:
    """按 URL 识别员工评价平台（牛客/小红书），其他返回 None。"""
    for label, hints in _PLATFORM_RULES:
        if isinstance(hints, str):
            hints = (hints,)
        if any(h in (url or "").lower() for h in hints):
            return label
    return None


# 牛客浏览器检索缓存：同公司多条岗位只渲染一次搜索页（避免每条岗位都开浏览器）
_NOWCODER_CACHE: dict[str, str] = {}
_NOWCODER_CACHE_MAX = 200


def _browser_nowcoder(company: str, timeout: float, max_chars: int) -> str:
    """无头浏览器渲染牛客搜索页（面经/员工评价/offer 投票），失败降级为空。

    牛客内容不被主流搜索引擎索引（实测 Bing/百度/神马均无结果），
    直接渲染牛客站内搜索是拿到员工评价的可靠路径；同公司结果缓存复用。
    """
    cached = _NOWCODER_CACHE.get(company)
    if cached is not None:
        return cached
    try:
        from .browser_fetch import browser_fetch_texts
        url = "https://www.nowcoder.com/search/all?query=" + quote(company)
        text = browser_fetch_texts(
            [url], timeout_ms=25_000, wait_ms=4_500, max_chars=max_chars,
        )
    except Exception as e:  # noqa: BLE001 - 牛客浏览器失败仅降级
        logger.info("牛客浏览器检索失败: company=%s 错误=%s", company, e)
        return ""
    if len(text) > 300:
        result = f"【牛客网员工评价/面经】{url}\n{text[:max_chars]}"
        _NOWCODER_CACHE[company] = result
        if len(_NOWCODER_CACHE) > _NOWCODER_CACHE_MAX:
            _NOWCODER_CACHE.clear()
        logger.info("牛客浏览器检索成功: company=%s 长度=%d", company, len(result))
        return result
    logger.info("牛客浏览器检索无有效内容: company=%s", company)
    return ""


def _search_web(company: str, job_title: str, timeout: float) -> list[dict]:
    """风评检索（多路）：通用口碑 + 牛客网 + 小红书，合并去重并标记平台。

    牛客/小红书内容被搜索引擎索引，命中其页面的标题/摘要即含员工评价片段；
    平台来源（牛客/小红书）结果优先排序，供打分模型识别权威员工评价。
    """
    queries = [
        t.format(company=company, job=job_title or "").strip()
        for t in _SEARCH_QUERIES
    ]
    merged: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        for it in _search(query, timeout):
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            it["platform"] = _platform_of(it["url"])
            merged.append(it)
    # 平台员工评价优先（牛客/小红书）→ 其他来源
    merged.sort(key=lambda it: 0 if it["platform"] else 1)
    return merged[:_SEARCH_TOP_N * 2]


def _fetch_nowcoder(items: list[dict], timeout: float, max_chars: int) -> str:
    """牛客公司点评页正文抓取（员工评价/WLB 的直接来源）。

    牛客点评页部分服务端渲染可 HTTP 抓取；失败降级为空（不影响主流程）。
    """
    for it in items:
        url = it.get("url", "")
        if "nowcoder.com" not in url:
            continue
        try:
            page = fetch_web_page(url, timeout=timeout, max_bytes=_MAX_BYTES)
            text = (page.text or "").strip()
            if len(text) > 200 and not page.skip_reason:
                logger.info("牛客点评页抓取成功: %s 长度=%d", url[:60], len(text))
                return f"【牛客员工评价】{url}\n{text[:max_chars]}"
        except Exception as e:  # noqa: BLE001 - 牛客抓取失败仅降级
            logger.info("牛客点评页抓取失败: %s 错误=%s", url[:60], e)
    return ""


def _format_items(items: list[dict]) -> str:
    """把搜索结果格式化为文本（含来源链接/平台标记），供提交 LLM / 打分使用。"""
    lines = ["【搜索引擎检索结果】"]
    for item in items:
        line = f"{item['title']}。{item['snippet']}" if item["snippet"] else item["title"]
        source = f"{item.get('platform')}｜{item['url']}" if item.get("platform") else item["url"]
        lines.append(f"- {line}（来源：{source}）")
    return "\n".join(lines)


def research_company(
    company_name: str,
    *,
    job_title: str = "",
    timeout: float = 8.0,
    max_chars: int = 1500,
) -> str:
    """检索公司外部资料（风评/WLB/经营），返回截断后的文本摘要。

    Args:
        company_name: 公司名称（来自模块四抽取结果）。
        job_title: 岗位名称（可选，用于聚焦检索员工风评/WLB）。
        timeout: 单次检索超时（秒）。
        max_chars: 检索资料截断长度（字符）。

    Returns:
        公司外部资料摘要文本；检索失败 / 无结果 / 公司名为空时返回空串
        （调用方据此标注"未自动联网核验"，绝不中断打分链路）。
    """
    name = (company_name or "").strip()
    if not name or name == MISSING:
        return ""

    parts: list[str] = []
    # 1) 搜索引擎（员工风评 / WLB / 舆情，主来源；多路覆盖牛客/小红书）
    search_items = _search_web(name, job_title, timeout)
    if search_items:
        parts.append(_format_items(search_items))
        logger.info(
            "搜索引擎检索完成: company=%s 结果=%d 条 平台=%s",
            name, len(search_items),
            [it.get("platform") for it in search_items if it.get("platform")],
        )
        # 1b) 牛客员工评价：先抓搜索结果中的牛客页正文；
        #     搜索引擎不索引牛客内容时，浏览器渲染牛客搜索页兜底（缓存复用）
        nowcoder_text = _fetch_nowcoder(search_items, timeout, max_chars)
        if not nowcoder_text and not any(
            it.get("platform") == "牛客" for it in search_items
        ):
            nowcoder_text = _browser_nowcoder(name, timeout, max_chars)
        if nowcoder_text:
            parts.append(nowcoder_text)

    # 2) 百度百科（公司业务/规模/经营简介，补充来源）
    try:
        page = fetch_web_page(
            _BAIKE_URL.format(name=quote(name)),
            timeout=timeout, max_bytes=_MAX_BYTES,
        )
        text = (page.text or "").strip()
        if text and not page.skip_reason:
            parts.append(f"【百度百科】\n{text}")
            logger.info("百科检索完成: company=%s 摘要长度=%d", name, len(text))
        else:
            logger.info(
                "百科无有效正文: company=%s 原因=%s", name,
                page.skip_reason or "无有效正文",
            )
    except Exception as e:  # noqa: BLE001 - 百科失败仅降级
        logger.info("百科检索失败: company=%s 错误=%s", name, e)

    if not parts:
        logger.info(
            "公司检索无可用结果: company=%s（降级：未自动联网核验）", name,
        )
        return ""

    joined = "\n\n".join(parts)
    summary = joined[:max_chars]
    logger.info(
        "公司检索完成: company=%s 摘要长度=%d 截断=%s",
        name, len(summary), len(joined) > max_chars,
    )
    return summary


def _as_field(value) -> str:
    """LLM 补全字段归一化：空值 / '信息未提及' 等变体一律归一为 MISSING。"""
    text = str(value or "").strip()
    if not text or text.lower() in _UNKNOWN_WORDS:
        return MISSING
    return text


# 详情页优先特征：高校就业网/官方招聘平台含完整岗位信息（公司简介+岗位列表+薪资地点）
_DETAIL_PRIORITY = {
    "edu.cn": 3, "xidian": 3, "zhiye.com": 3, "mokahr": 3, "zhaopin": 2,
    "nowcoder": 2, "campus": 2, "career": 2, "recruit": 2, "jobs": 1,
    "job": 1, "51job": 1, "iguopin": 1,
}


def _detail_score(url: str) -> int:
    """详情页价值评分：高校就业网/官方校招平台 > 招聘平台 > 其他。"""
    return max((score for kw, score in _DETAIL_PRIORITY.items() if kw in url),
               default=0)


def _search_multi(query: str, timeout: float) -> list[dict]:
    """多源搜索合并（Bing 主源 → 神马 → 百度兜底），URL 去重，取前 _SEARCH_TOP_N 条。"""
    seen: set[str] = set()
    merged: list[dict] = []
    for search_fn, label in ((_search, "Bing"), (_search_smcn, "神马"),
                             (_search_baidu, "百度")):
        try:
            items = search_fn(query, timeout)
        except Exception:  # noqa: BLE001 - 单源失败降级下一源
            continue
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            merged.append(it)
        if len(merged) >= _SEARCH_TOP_N:
            break
    return merged[:_SEARCH_TOP_N]


def _pick_detail_urls(items: list[dict], llm=None, timeout: float = 8.0, limit: int = 2) -> list[str]:
    """挑选含完整岗位信息的详情页 URL（高校就业网/官方校招平台优先）。

    两层策略：先按 URL 关键词打分；候选不足时交给 DeepSeek 从全部搜索结果
    中挑选（利用其世界知识识别 zhiye.com/mokahr/edu.cn 等校招平台 URL，
    弥补搜索引擎常只返回官网首页/百科的局限）。
    """
    chosen = [it["url"] for it in sorted(
        (it for it in items if _detail_score(it["url"]) > 0),
        key=lambda it: _detail_score(it["url"]), reverse=True,
    )[:limit]]
    if len(chosen) < limit and llm is not None:
        remaining = [it for it in items if it["url"] not in chosen]
        if remaining:
            lines = "\n".join(
                f"- {it['title'][:40]} | {it['url']} | {it.get('snippet', '')[:80]}"
                for it in remaining
            )
            try:
                content = llm.complete(
                    f"以下是搜索某公司校园招聘信息得到的结果：\n{lines}\n\n"
                    "请挑选 1-2 个最可能包含该公司校园招聘完整岗位信息（岗位列表/工作地点/"
                    "薪资/投递方式）的网页 URL。优先官方校招平台（zhiye.com、mokahr.com）、"
                    "高校就业网（edu.cn）、牛客（nowcoder.com）；若没有直接的校招页面，"
                    "请选择公司官网首页（用于后续提取校招入口链接）。只输出选中的 URL，每行一个。",
                    system="你是招聘信息检索助手：只输出 URL，每行一个。",
                    temperature=0.1,
                )
            except Exception as e:  # noqa: BLE001
                logger.info("DeepSeek 挑选详情页失败: %s", e)
            else:
                for line in (content or "").splitlines():
                    line = line.strip()
                    if line.startswith("http"):
                        chosen.append(line.split(" ")[0])
                seen: set[str] = set()
                chosen = [u for u in chosen if not (u in seen or seen.add(u))]
    return chosen[:limit]


def _crawl_career_links(home_url: str, timeout: float) -> list[str]:
    """抓官网首页原始 HTML，提取校招/招聘子页链接（campus/career/job/recruit/zhiye 等）。

    搜索引擎常只返回官网首页，校招子页需从首页导航链接挖掘后 follow 抓取。
    """
    try:
        import urllib.request
        req = urllib.request.Request(
            home_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0 Safari/537.36"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(_MAX_BYTES).decode("utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001
        logger.info("官网首页抓取失败: %s 错误=%s", home_url[:60], e)
        return []
    links: set[str] = set()
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html):
        url = m.group(1)
        low = url.lower()
        if any(kw in low for kw in ("campus", "career", "recruit", "zhiye",
                                    "/job", "/jobs", "join-us", "campus-recruit")):
            links.add(urllib.parse.urljoin(home_url, url))
    return list(links)[:5]


def _pick_home_url(items: list[dict]) -> str | None:
    """从搜索结果中挑选公司官网首页（排除百科/新闻/社交平台，用于挖掘校招入口）。"""
    exclude = ("baike.", "zhihu", "weibo", "sogou.com", "zhuanlan",
               "36kr", "juejin", "csdn", "bytedance", "qq.com")
    for it in items:
        url = it["url"]
        if any(kw in url for kw in exclude):
            continue
        if any(kw in url for kw in (".com", ".cn", ".net", ".org")):
            return url
    return None


def _looks_like_job_page(text: str) -> bool:
    """判断抓取正文是否含完整岗位信息（岗位列表/薪资/地点/投递方式）。

    强特征优先：岗位/职位/薪资/网申/校招对象等只在招聘页出现；
    公司介绍页的"人才招聘"导航字样不算（避免"关于我们"页误判为岗位页）。
    """
    text = (text or "").strip()
    if len(text) < 300:
        return False
    strong = ("岗位", "职位", "薪资", "校招对象", "工作地点", "投递",
              "简历投递", "网申", "招聘职位")
    if any(kw in text for kw in strong):
        return True
    return any(kw in text for kw in ("工程师", "开发", "算法")) and "招聘" in text


def _ask_llm_career_url(llm, known: str) -> list[str]:
    """DeepSeek 世界知识给出公司官方校招网申页 URL（zhiye/mokahr/官网/jobs 路径）。

    注意：LLM 可能编造 URL，调用方抓取后须校验正文含岗位特征才采用。
    """
    try:
        content = llm.complete(
            f"公司：{known}。请给出该公司校园招聘的官方网申页面 URL"
            "（通常在 zhiye.com / mokahr.com / 公司官网 /jobs 路径下）。"
            "若不确定输出【不知道】。只输出 URL，最多 2 个，每行一个。",
            system="你是招聘信息检索助手：只输出 URL。",
            temperature=0.1,
        )
    except Exception as e:  # noqa: BLE001
        logger.info("DeepSeek 给出校招 URL 失败: %s", e)
        return []
    urls = [line.strip() for line in (content or "").splitlines()
            if line.strip().startswith("http")]
    return urls[:2]


def _fetch_details(urls: list[str], timeout: float, max_chars: int) -> str:
    """抓取详情页正文（校招公告/岗位列表），拼接返回；失败跳过不阻断。"""
    parts: list[str] = []
    for url in urls:
        try:
            page = fetch_web_page(url, timeout=timeout, max_bytes=_MAX_BYTES)
            text = (page.text or "").strip()
            if text and not page.skip_reason:
                parts.append(f"【详情页】{url}\n{text[:max_chars]}")
                logger.info("残缺补全详情页抓取成功: %s 长度=%d", url[:60], len(text))
            else:
                logger.info("残缺补全详情页无正文: %s 原因=%s",
                            url[:60], page.skip_reason or "无正文")
        except Exception as e:  # noqa: BLE001 - 详情页失败不影响搜索摘要补全
            logger.info("残缺补全详情页抓取失败: %s 错误=%s", url[:60], e)
    return "\n\n".join(parts)[: max_chars * 2]


def _build_search_queries(llm, known: str, location: str) -> list[str]:
    """DeepSeek 基于已知线索生成精准检索词（1-3 个）；失败时回退规则构造。"""
    fallback = [
        " ".join(filter(None, [known, location, "2027", "校招", "招聘"])).strip(),
        " ".join(filter(None, [known, "校园招聘", "岗位"])).strip(),
    ]
    try:
        content = llm.complete(
            f"已知招聘信息线索：{known}（可能只有公司名或岗位名）。"
            "请生成 2 个精准的中文搜索引擎检索词，用于查找该公司的校园招聘岗位信息"
            "（可含 2027/校招/岗位/软件/后端等词），每行一个，每个不超过 30 字。",
            system="你是招聘信息检索助手：只输出检索词，每行一个，不要多余内容。",
            temperature=0.3,
        )
    except Exception as e:  # noqa: BLE001 - LLM 失败回退规则检索词
        logger.info("残缺补全检索词生成失败，回退规则词: %s", e)
        return fallback
    queries = [q.strip() for q in (content or "").splitlines() if q.strip()]
    queries = [q for q in queries if 2 <= len(q) <= 30]
    # 追加高校就业网专项查询：就业网公告为服务端渲染，含完整岗位列表/薪资/投递方式
    queries = queries[:3] + [
        f"{known} 校园招聘 site:edu.cn",
        " ".join(filter(None, [known, "2027", "校园招聘", "公告"])).strip(),
    ]
    # 去重并限长
    seen: set[str] = set()
    queries = [q for q in queries if q and not (q in seen or seen.add(q))]
    return queries[:5] or fallback


def enrich_missing_fields(
    text: str,
    posting,
    llm,
    *,
    timeout: float = 8.0,
    max_chars: int = 1500,
) -> dict:
    """残缺字段自动补全（用户诉求：小助手自动调用 DeepSeek 补全残缺岗位）。

    流程（全程 DeepSeek 驱动，失败逐级降级不阻断）：
        1. DeepSeek 基于已知线索生成精准检索词
        2. 多源搜索引擎检索（Bing → 神马 → 百度，URL 去重）
        3. 自动挑选高校就业网/官方校招平台详情页并抓取正文（含完整岗位列表/薪资/地点）
        4. DeepSeek 从搜索资料抽取完整字段：公司/岗位/地点/学历/技术栈/投递方式/截止
        5. 就地更新缺失字段并清空/重算残缺标记，带回 external_info 供打分使用

    Args:
        text: 模块二合并的完整原始文本（缺字段时构造检索词）。
        posting: JobPosting（就地更新补全字段与 incomplete_reason）。
        llm: LLMClient（生成检索词 + 抽取缺失字段，复用模块四/五同一客户端）。
        timeout / max_chars: 检索超时与资料截断长度。

    Returns:
        dict：成功补全时返回 {"company_name": str, "job_title": str, ...,
        "external_info": str}（external_info 为截断后的搜索资料，含来源链接）；
        搜索失败 / 结果为空 / LLM 无法确认 / LLM 调用失败时返回 {}，
        调用方保持残缺标记并跳过打分（不阻断）。
    """
    missing = []
    if not posting.company_name or posting.company_name == MISSING:
        missing.append("company_name")
    if not posting.job_title or posting.job_title == MISSING:
        missing.append("job_title")
    if not missing:
        return {}

    # 1) 构造检索线索：优先用已有字段（缺哪个补哪个）；都缺则取原文关键片段
    known = (posting.company_name if posting.company_name != MISSING else "") or (
        posting.job_title if posting.job_title != MISSING else ""
    )
    if not known:
        flat = re.sub(r"\s+", " ", text or "").strip()
        known = flat[:60] if flat else ""
    if not known:
        logger.info("残缺补全：无可用的检索词（原文为空），保持残缺标记")
        return {}
    location = (posting.location or "").strip()
    if location == MISSING:
        location = ""

    # 2) DeepSeek 生成检索词 → 全部检索词执行多源检索（合并去重）
    #    结果按详情页价值排序：高校就业网简章（edu.cn 服务端渲染、含完整岗位）
    #    > 校招平台（zhiye/mokahr）> 其他，保证高质量详情页优先进入抓取
    queries = _build_search_queries(llm, known, location)
    logger.info("残缺补全检索词: %s", queries)
    items: list[dict] = []
    for query in queries:
        items.extend(_search_multi(query, timeout))
    unique: list[dict] = []
    seen: set[str] = set()
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        unique.append(it)
    items = sorted(unique, key=lambda it: _detail_score(it["url"]),
                   reverse=True)[:_SEARCH_TOP_N]
    if not items:
        logger.info("残缺补全：多源搜索无结果，保持残缺标记 query=%s", queries[0][:40])
        return {}

    # 3) 详情页抓取：多层尝试直到拿到含岗位信息的正文
    #    a. 搜索结果中的详情页（关键词打分 + DeepSeek 挑选）
    #    b. 官网首页挖掘校招子页链接（搜索引擎常只返回官网首页）
    #    c. DeepSeek 世界知识直接给出官方校招网申页 URL（zhiye/mokahr）
    #    d. 浏览器渲染兜底：HTTP 抓不到的 JS 渲染平台（zhiye/mokahr/BOSS）
    #       用无头浏览器执行 JS 后读取完整岗位列表
    detail_urls = _pick_detail_urls(items, llm=llm, timeout=timeout, limit=5)
    home = _pick_home_url(items)
    career_links = _crawl_career_links(home, timeout) if home else []
    if career_links:
        logger.info("官网校招链接挖掘: %s -> %d 条", home[:50], len(career_links))
    llm_urls = _ask_llm_career_url(llm, known)
    if llm_urls:
        logger.info("DeepSeek 校招URL: %s", llm_urls)
    details = ""
    for urls in (detail_urls, career_links, llm_urls):
        if not urls:
            continue
        fetched = _fetch_details(urls, timeout, max_chars)
        if _looks_like_job_page(fetched):
            details = fetched
            break
    if not details:
        browser_urls = list(dict.fromkeys(
            detail_urls + career_links + (llm_urls or []) + ([home] if home else [])
        ))[:4]
        if browser_urls:
            # 渲染后从页内导航提取校招入口链接（官网"人才招聘"入口
            # 常指向 zhiye/mokahr 校招平台），校招平台页优先采用
            extra_links = browser_fetch_links(browser_urls)
            if extra_links:
                # 过滤无关页（隐私政策/条款/关于页），职位列表/详情页优先
                exclude = ("privacy", "terms", "legal", "faq", "news", "media")
                ranked_links = [
                    u for u in extra_links
                    if not any(k in u.lower() for k in exclude)
                ]
                ranked_links.sort(key=lambda u: (
                    0 if any(k in u.lower() for k in ("campus/jobs", "position", "/job"))
                    else 1 if "campus" in u.lower() or "jobs" in u.lower()
                    else 2,
                ))
                logger.info("浏览器挖掘校招链接: %d 条(过滤后 %d 条)",
                            len(extra_links), len(ranked_links))
                browser_text2 = browser_fetch_texts(
                    ranked_links[:4], max_chars=max_chars,
                )
                if _looks_like_job_page(browser_text2):
                    details = browser_text2
                    logger.info("残缺补全: 浏览器挖掘校招页生效")
            if not details:
                browser_text = browser_fetch_texts(
                    browser_urls, max_chars=max_chars,
                )
                if _looks_like_job_page(browser_text):
                    details = browser_text
                    logger.info("残缺补全: 浏览器渲染兜底生效")
    search_text = _format_items(items)[:max_chars]
    material = search_text + ("\n\n" + details if details else "")

    # 4) DeepSeek 从搜索资料抽取完整缺失字段
    try:
        content = llm.complete(
            f"原始消息：\n{text}\n\n搜索资料（标题/摘要/详情页正文/来源）：\n"
            f"{material[: max_chars * 2]}\n\n"
            "请判断能否确认以下字段：公司名称、岗位名称、工作地点、学历要求、"
            "技术栈要求、投递方式、截止时间。依据包括原始消息与搜索资料："
            "原始消息明确写出的公司名/岗位名可直接确认（不必在搜索结果中逐字出现）；"
            "其他字段（地点/学历/技术栈/投递/截止）仅当搜索资料可确认时填写。"
            "输出 JSON：{\"company_name\": \"...\", \"job_title\": \"...\", "
            "\"location\": \"...\", \"education\": \"...\", \"tech_stack\": \"...\", "
            "\"apply_method\": \"...\", \"deadline\": \"...\"}。"
            "无法确认的字段填【信息未提及】。",
            system="你是岗位信息补全助手：只能依据给定搜索资料下结论，不得编造。",
            temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001 - LLM 失败保持残缺，不阻断
        logger.warning("残缺补全 LLM 抽取失败: 错误=%s", e)
        return {}
    data = extract_json_object(content)
    if not data:
        logger.warning("残缺补全 LLM 输出无法解析: 保持残缺标记")
        return {}

    # 5) 就地更新缺失字段（仅补 MISSING 字段，不覆盖已有值；未确认字段保持 MISSING）
    fields = (
        ("company_name", "company_name"), ("job_title", "job_title"),
        ("location", "location"), ("education", "education"),
        ("tech_stack", "tech_stack"), ("apply_method", "apply_method"),
        ("deadline", "deadline"),
    )
    result: dict = {}
    for attr, key in fields:
        current = getattr(posting, attr)
        new = _as_field(data.get(key))
        if current in (None, "", MISSING) and new != MISSING:
            setattr(posting, attr, new)
            result[key] = new

    # 6) 重算残缺标记：核心字段（公司/岗位）都齐 → 清空残缺，可进入打分
    still_missing = [
        label for label, value in (
            ("公司名称", posting.company_name), ("岗位名称", posting.job_title),
        ) if value == MISSING
    ]
    if still_missing:
        posting.incomplete_reason = "缺少" + "、".join(still_missing)
    else:
        posting.incomplete_reason = ""

    if not result:
        logger.info("残缺补全：LLM 未能确认任何缺失字段，保持残缺标记")
        return {}
    result["external_info"] = material
    filled = "、".join(result.keys()) or "无"
    logger.info(
        "残缺补全完成: 补全=%s 剩余残缺=%s 来源=%d 条/详情%d 页",
        filled, posting.incomplete_reason or "无", len(items), len(detail_urls),
    )
    return result
