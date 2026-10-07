"""公开列表页采集（文档 4.2）：仅静态 HTML，用 stdlib 解析，不渲染 JS。

两种模式（共存，互不影响）：
- 列表页模式：``list_urls`` 页面内同域外链 → 逐条跟进正文（原行为）
- 单页即内容模式：``page_urls`` 页面正文整体作为一条内容，不跟进外链；
  适用于官方「必吃榜/美食名单」这类正文即数据的静态页，可按 ``城市=URL`` 绑定城市。

仅提取正文摘要文本用于抽取，原始页面留存快照供溯源；对外展示只做摘要引用 + 跳转。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .base import BaseCollector
from .core import CollectorDeps, CrawlItem

_MAX_TEXT_CHARS = 6000
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_HTTP_PREFIXES = ("http://", "https://")


class _LinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self._base = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(urljoin(self._base, href))


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def extract_links(html: str, base_url: str) -> list[str]:
    """抽取页面内 http(s) 链接，去重且保持出现顺序。"""
    parser = _LinkExtractor(base_url)
    parser.feed(html)
    seen: list[str] = []
    for link in parser.links:
        if link.startswith(("http://", "https://")) and link not in seen:
            seen.append(link)
    return seen


def strip_html(html: str) -> str:
    """去标签取纯文本，空白折叠。"""
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(parser.parts)


def extract_text(html: str) -> str:
    return strip_html(html)[:_MAX_TEXT_CHARS]


def extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if not match:
        return None
    title = strip_html(match.group(1)).strip()
    return title or None


def parse_page_specs(
    specs: list[str], default_city: str | None = None
) -> list[tuple[str | None, str]]:
    """解析「单页即内容」来源配置为 ``(city, url)`` 列表。

    每项支持 ``城市=URL`` 或裸 ``URL``（此时 city 取 ``default_city``）；
    非 http(s) 项与空项跳过。
    """
    parsed: list[tuple[str | None, str]] = []
    for spec in specs:
        spec = (spec or "").strip()
        if not spec:
            continue
        if spec.startswith(_HTTP_PREFIXES):
            parsed.append((default_city, spec))
            continue
        city, sep, url = spec.partition("=")
        url = url.strip()
        if sep and url.startswith(_HTTP_PREFIXES):
            parsed.append((city.strip() or default_city, url))
    return parsed


class HtmlListCollector(BaseCollector):
    source = "html_list"

    def __init__(
        self,
        *,
        settings,
        deps: CollectorDeps,
        list_urls: list[str] | None = None,
        page_urls: list[str] | None = None,
        city_hint: str | None = None,
    ):
        super().__init__(settings=settings, deps=deps, city_hint=city_hint)
        self.list_urls = [url for url in (list_urls or []) if url]
        self.page_specs = [spec for spec in (page_urls or []) if spec]

    @property
    def is_ready(self) -> bool:
        return bool(self.list_urls or self.page_specs)

    def fetch(self, since=None) -> list[CrawlItem]:
        items = self._fetch_pages()
        if len(items) < self.max_items:
            items.extend(self._fetch_lists(limit=self.max_items - len(items)))
        return items

    def _fetch_pages(self) -> list[CrawlItem]:
        """单页即内容：页面正文整体作为一条 CrawlItem，不跟进外链。"""
        items: list[CrawlItem] = []
        for city, url in parse_page_specs(self.page_specs, self.city_hint):
            # 指定 --city 时只取该城市的页面；未带城市前缀的页面归属 default_city。
            if self.city_hint and city and city != self.city_hint:
                continue
            page = self.fetch_url(url, ext="html")
            if not page.ok:
                continue
            text = extract_text(page.text)
            if not text:
                continue
            items.append(
                CrawlItem(
                    source=self.source,
                    raw_text=text,
                    source_url=url,
                    raw_title=extract_title(page.text),
                    city_hint=city,
                    raw_ref=page.raw_ref,
                )
            )
            if len(items) >= self.max_items:
                break
        return items

    def _fetch_lists(self, *, limit: int) -> list[CrawlItem]:
        """列表页模式：跟进同域外链正文（原行为）。"""
        items: list[CrawlItem] = []
        for list_url in self.list_urls:
            page = self.fetch_url(list_url, ext="html")
            if not page.ok:
                continue
            list_host = urlparse(list_url).netloc
            for link in extract_links(page.text, list_url):
                if link == list_url or urlparse(link).netloc != list_host:
                    continue
                detail = self.fetch_url(link, ext="html")
                if not detail.ok:
                    continue
                text = extract_text(detail.text)
                if not text:
                    continue
                items.append(
                    CrawlItem(
                        source=self.source,
                        raw_text=text,
                        source_url=link,
                        raw_title=extract_title(detail.text),
                        city_hint=self.city_hint,
                        raw_ref=detail.raw_ref,
                    )
                )
                if len(items) >= limit:
                    return items
        return items