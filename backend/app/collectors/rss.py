"""公开 RSS/Atom 采集（文档 4.2）：stdlib 解析，增量拉取，低频合规。

仅静态 feed，不渲染 JS；正文取 description/summary 的纯文本。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from .base import BaseCollector
from .core import CollectorDeps, CrawlItem
from .html_list import strip_html


@dataclass
class FeedEntry:
    title: str
    link: str | None
    summary: str
    published_at: datetime | None


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(node: ET.Element, name: str) -> ET.Element | None:
    for child in node:
        if _localname(child.tag) == name:
            return child
    return None


def _child_text(node: ET.Element, name: str) -> str | None:
    child = _child(node, name)
    if child is None:
        return None
    return (child.text or "").strip() or None


def _child_link(node: ET.Element, base_url: str | None) -> str | None:
    """兼容 RSS 的 <link>text</link> 与 Atom 的 <link href=.../>。"""
    candidates = [c for c in node if _localname(c.tag) == "link"]
    if not candidates:
        return None
    for child in candidates:
        href = child.get("href")
        if href and child.get("rel") in (None, "alternate"):
            return urljoin(base_url, href) if base_url else href
    for child in candidates:
        if child.text and child.text.strip():
            text = child.text.strip()
            return urljoin(base_url, text) if base_url else text
    return None


def _parse_pub(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed is None:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def parse_rss(data: bytes, base_url: str | None = None) -> list[FeedEntry]:
    """解析 RSS 2.0 / Atom feed 字节串，返回条目列表。"""
    root = ET.fromstring(data)
    entries: list[FeedEntry] = []
    for node in root.iter():
        if _localname(node.tag) not in ("item", "entry"):
            continue
        summary_raw = (
            _child_text(node, "description")
            or _child_text(node, "summary")
            or _child_text(node, "content")
            or ""
        )
        entries.append(
            FeedEntry(
                title=_child_text(node, "title") or "",
                link=_child_link(node, base_url),
                summary=strip_html(summary_raw),
                published_at=_parse_pub(
                    _child_text(node, "pubdate")
                    or _child_text(node, "published")
                    or _child_text(node, "updated")
                ),
            )
        )
    return entries


class RssCollector(BaseCollector):
    source = "rss"

    def __init__(
        self,
        *,
        settings,
        deps: CollectorDeps,
        feed_urls: list[str] | None = None,
        city_hint: str | None = None,
    ):
        super().__init__(settings=settings, deps=deps, city_hint=city_hint)
        self.feed_urls = [url for url in (feed_urls or []) if url]

    @property
    def is_ready(self) -> bool:
        return bool(self.feed_urls)

    def fetch(self, since: datetime | None = None) -> list[CrawlItem]:
        items: list[CrawlItem] = []
        for feed_url in self.feed_urls:
            result = self.fetch_url(feed_url, ext="xml")
            if not result.ok:
                continue
            try:
                entries = parse_rss(result.text.encode("utf-8"), base_url=feed_url)
            except ET.ParseError:
                self.deps.alert("rss_parse_error", {"source": self.source, "url": feed_url})
                continue
            for entry in entries:
                if since and entry.published_at and entry.published_at < since:
                    continue
                text = entry.summary or entry.title
                if not text:
                    continue
                items.append(
                    CrawlItem(
                        source=self.source,
                        raw_text=text,
                        source_url=entry.link or feed_url,
                        raw_title=entry.title or None,
                        city_hint=self.city_hint,
                        published_at=entry.published_at,
                        raw_ref=result.raw_ref,
                    )
                )
                if len(items) >= self.max_items:
                    return items
        return items