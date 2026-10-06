"""人工种子：解析三种文件形态、增量游标、上限、入库幂等。"""

import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.collectors.runner import run_crawl, run_source
from app.collectors.seed import SeedCollector, parse_seed_records
from app.db.models import RawContent
from collectors_fakes import make_client, make_deps, make_settings, memory_session

LONG = "成都这家苍蝇馆子锅气足，价钱实惠，老板热情。" * 8


@pytest.fixture
def session():
    with memory_session() as s:
        yield s


def _deps(handler=None):
    return make_deps(make_client(handler or (lambda r: httpx.Response(404, text=""))))


# --- 解析 -------------------------------------------------------------------

def test_parse_seed_jsonl_skips_blank_text():
    text = "\n".join(
        [
            json.dumps({"raw_text": "成都苍蝇馆子真香", "source": "xiaohongshu", "city_hint": "成都"}),
            json.dumps({"raw_title": "只有标题，无正文"}),
            json.dumps({"text": "备用字段生效", "url": "http://x", "published_at": "2026-01-01T00:00:00"}),
        ]
    )
    items = parse_seed_records(text, default_city="重庆", default_source="seed")

    assert len(items) == 2
    assert items[0].source == "xiaohongshu"
    assert items[0].city_hint == "成都"
    assert items[1].source == "seed"
    assert items[1].city_hint == "重庆"
    assert items[1].source_url == "http://x"
    assert items[1].published_at == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_parse_seed_supports_json_array_and_items_object():
    array_form = json.dumps([{"raw_text": "A"}, {"raw_text": "B"}])
    items_form = json.dumps({"items": [{"raw_text": "C"}]})
    assert len(parse_seed_records(array_form)) == 2
    assert len(parse_seed_records(items_form)) == 1


# --- 采集器 -----------------------------------------------------------------

def test_seed_collector_not_ready_without_file(tmp_path):
    collector = SeedCollector(
        settings=make_settings(), deps=_deps(), path=tmp_path / "missing.jsonl"
    )
    assert collector.is_ready is False
    assert collector.fetch() == []


def test_seed_fetch_respects_cursor(tmp_path):
    path = tmp_path / "s.jsonl"
    rows = [
        {"raw_text": "旧内容", "published_at": "2025-01-01T00:00:00Z"},
        {"raw_text": "新内容", "published_at": "2026-01-02T00:00:00Z"},
        {"raw_text": "无时间"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    collector = SeedCollector(settings=make_settings(), deps=_deps(), path=path)
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert [i.raw_text for i in collector.fetch(since=since)] == ["新内容", "无时间"]


def test_seed_fetch_capped_by_max_items(tmp_path):
    path = tmp_path / "s.jsonl"
    rows = [{"raw_text": f"内容{i}"} for i in range(5)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    collector = SeedCollector(
        settings=make_settings(crawl_max_items_per_source=2), deps=_deps(), path=path
    )
    assert len(collector.fetch()) == 2


# --- 入库幂等 ---------------------------------------------------------------

def test_seed_ingest_is_idempotent(session, tmp_path):
    path = tmp_path / "seeds.jsonl"
    path.write_text(json.dumps({"raw_text": LONG, "city_hint": "成都"}), encoding="utf-8")

    settings = make_settings()
    collector = SeedCollector(
        settings=settings, deps=_deps(), path=path, city_hint="成都"
    )

    first = run_source(session, collector, settings=settings)
    session.commit()
    assert (first.fetched, first.new, first.duplicated) == (1, 1, 0)

    second = run_source(session, collector, settings=settings)
    session.commit()
    assert (second.new, second.duplicated) == (0, 1)
    assert session.scalar(select(func.count()).select_from(RawContent)) == 1


def test_incremental_cursor_normalizes_timezone(session, tmp_path):
    """+08:00 的发布时间需按 UTC 落库，否则增量游标会偏移 8h 而漏抓。"""
    path = tmp_path / "s.jsonl"
    path.write_text(
        json.dumps(
            {"raw_text": LONG, "city_hint": "成都", "published_at": "2026-10-02T08:00:00+08:00"}
        ),
        encoding="utf-8",
    )
    settings = make_settings()
    deps = _deps()

    first = run_crawl(
        session, settings, sources=["seed"], deps=deps,
        city_hint="成都", seed_path=str(path), force=True,
    )
    assert first.stats["sources"]["seed"]["new"] == 1

    second = run_crawl(
        session, settings, sources=["seed"], deps=deps,
        city_hint="成都", seed_path=str(path), force=True,
    )
    stats = second.stats["sources"]["seed"]
    assert (stats["fetched"], stats["new"], stats["duplicated"]) == (1, 0, 1)