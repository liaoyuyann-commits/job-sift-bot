# job_assistant/research/browser_fetch.py

"""无头浏览器渲染抓取（Playwright）：为 JS 渲染平台（zhiye/mokahr/BOSS 等）兜底。

背景：搜索引擎/HTTP 抓取对 SPA 网站只能拿到占位文本（如 zhiye.com 90 字），
完整岗位列表（职位/地点/薪资/投递方式）需要浏览器执行 JS 渲染后才能读取。
本模块作为残缺补全的兜底通道：HTTP 多层尝试失败后用无头浏览器逐个渲染候选 URL。

设计：
    - 同步 API（主程序线程模型，避免与既有 asyncio 冲突）
    - 每次调用 launch/close，避免浏览器进程常驻泄漏资源
    - 任一 URL 失败降级为空串，不阻断整体补全
    - Playwright 未安装时静默降级（不影响 HTTP 快路径）
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def browser_fetch_texts(
    urls: list[str],
    *,
    timeout_ms: int = 25_000,
    wait_ms: int = 3_500,
    max_chars: int = 2500,
) -> str:
    """无头浏览器渲染抓取页面正文，拼接返回（顺序与输入一致）。

    Args:
        urls: 候选 URL（去重后传入，最多建议 4 个）。
        timeout_ms: 单页加载超时。
        wait_ms: 加载完成后等待 JS 渲染的时间。
        max_chars: 每个页面正文截断长度。

    Returns:
        拼接后的渲染正文；空串表示全部失败/Playwright 不可用。
    """
    if not urls:
        return ""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright 未安装，跳过浏览器渲染抓取")
        return ""

    parts: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=_UA, locale="zh-CN")
                page = context.new_page()
                for url in urls:
                    try:
                        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                        page.wait_for_timeout(wait_ms)
                        text = page.evaluate(
                            "document.body ? document.body.innerText : ''"
                        )
                        text = (text or "").strip()
                        if len(text) > 30:
                            parts.append(f"【浏览器渲染页】{url}\n{text[:max_chars]}")
                            logger.info(
                                "浏览器渲染抓取成功: %s 长度=%d", url[:60], len(text),
                            )
                        else:
                            logger.info("浏览器渲染页无正文: %s", url[:60])
                    except Exception as e:  # noqa: BLE001 - 单页失败继续下一页
                        logger.info(
                            "浏览器渲染抓取失败: %s 错误=%s",
                            url[:60], str(e)[:80],
                        )
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 - 浏览器启动失败降级
        logger.warning("浏览器渲染启动失败: %s", str(e)[:120])
        return ""
    return "\n\n".join(parts)[: max_chars * 3]


# 校招入口链接特征（官网导航"人才招聘/加入我们/校园招聘"常指向校招平台子页）
_LINK_HINTS = ("campus", "career", "recruit", "zhiye", "/job", "jobs",
               "join", "talent")


def browser_fetch_links(
    urls: list[str],
    *,
    timeout_ms: int = 25_000,
    wait_ms: int = 3_000,
    limit: int = 6,
) -> list[str]:
    """无头浏览器渲染页面后提取校招入口链接（官网导航 → zhiye/mokahr 校招平台）。

    搜索引擎常只返回官网首页/关于页，校招子页需渲染后从页内导航挖掘；
    返回绝对 URL 列表（去重、限长），供下一步渲染抓取正文。
    """
    if not urls:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright 未安装，跳过浏览器链接挖掘")
        return []

    links: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=_UA, locale="zh-CN")
                page = context.new_page()
                for url in urls:
                    try:
                        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                        page.wait_for_timeout(wait_ms)
                        hrefs = page.evaluate(
                            """() => {
                                const out = [];
                                document.querySelectorAll('a[href]').forEach(
                                    a => out.push(a.href));
                                return out;
                            }"""
                        )
                        for href in hrefs or []:
                            low = (href or "").lower()
                            if any(kw in low for kw in _LINK_HINTS):
                                links.append(href)
                    except Exception as e:  # noqa: BLE001 - 单页失败继续
                        logger.info(
                            "浏览器链接挖掘失败: %s 错误=%s", url[:60], str(e)[:80],
                        )
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("浏览器链接挖掘启动失败: %s", str(e)[:120])
        return []
    # 去重并限长
    seen: set[str] = set()
    unique = [u for u in links if u and not (u in seen or seen.add(u))]
    return unique[:limit]
