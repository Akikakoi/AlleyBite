"""公开列表页采集（文档 4.2）：仅静态 HTML，用 stdlib 解析，不渲染 JS。

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


class HtmlListCollector(BaseCollector):
    source = "html_list"

    def __init__(
        self,
        *,
        settings,
        deps: CollectorDeps,
        list_urls: list[str] | None = None,
        city_hint: str | None = None,
    ):
        super().__init__(settings=settings, deps=deps, city_hint=city_hint)
        self.list_urls = [url for url in (list_urls or []) if url]

    @property
    def is_ready(self) -> bool:
        return bool(self.list_urls)

    def fetch(self, since=None) -> list[CrawlItem]:
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
                if len(items) >= self.max_items:
                    return items
        return items