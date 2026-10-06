"""人工种子导入（文档 4.2 第 4 项）：运营提供的内容名单，用于冷启动与标注校准。

支持 JSON 数组、``{"items": [...]}`` 与 JSONL 三种文件形态；不走网络。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseCollector
from .core import CollectorDeps, CrawlItem

DEFAULT_SEED_PATH = "samples/seeds.sample.jsonl"


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _load_records(text: str) -> list[dict]:
    stripped = text.strip()
    if not stripped:
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        rows: list[dict] = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
        return rows

    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
        return [data]
    return []


def parse_seed_records(
    text: str,
    *,
    default_city: str | None = None,
    default_source: str = "seed",
) -> list[CrawlItem]:
    """把种子文件内容解析为 CrawlItem 列表；缺 raw_text 的记录跳过。"""
    items: list[CrawlItem] = []
    for record in _load_records(text):
        raw_text = str(record.get("raw_text") or record.get("text") or "").strip()
        if not raw_text:
            continue
        items.append(
            CrawlItem(
                source=str(record.get("source") or default_source),
                raw_text=raw_text,
                source_url=record.get("source_url") or record.get("url"),
                raw_title=record.get("raw_title") or record.get("title"),
                city_hint=record.get("city_hint") or default_city,
                published_at=_parse_dt(record.get("published_at")),
            )
        )
    return items


class SeedCollector(BaseCollector):
    source = "seed"

    def __init__(
        self,
        *,
        settings,
        deps: CollectorDeps,
        path: str | Path = DEFAULT_SEED_PATH,
        city_hint: str | None = None,
    ):
        super().__init__(settings=settings, deps=deps, city_hint=city_hint)
        self.path = Path(path)

    @property
    def is_ready(self) -> bool:
        return self.path.exists()

    def fetch(self, since: datetime | None = None) -> list[CrawlItem]:
        if not self.path.exists():
            return []
        items = parse_seed_records(
            self.path.read_text(encoding="utf-8"), default_city=self.city_hint
        )
        if since is not None:
            items = [i for i in items if i.published_at is None or i.published_at >= since]
        return items[: self.max_items]