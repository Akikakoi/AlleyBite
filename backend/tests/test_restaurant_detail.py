from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Mention as MentionRow, RawContent, Restaurant
from app.services.alignment import align_mentions
from app.services.extract_pipeline import extract_raw_content
from app.services.ingest import compute_content_hash, ingest_raw_content
from app.services.pipeline import list_cities
from app.services.rank_service import (
    build_rank_snapshot,
    build_restaurant_detail,
    build_restaurant_sources,
)
from app.services.scoring_service import score_one_restaurant

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)

TEXT = (
    "昨天去吃了明婷饭店，藏在青羊区同心路的巷子里，环境确实一般，"
    "但脑花豆腐和霸王兔太香了，锅气足，人均才 65。"
)


def make_settings(**overrides) -> Settings:
    base = dict(llm_mock=True, llm_api_key="")
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


_counter = {"n": 0}


def _seed_mention(session, shop, *, city="成都", text="x", sentiment="positive",
                  address=None, source_url=None, content_id=None):
    """落一条 raw_content + mention（可选复用已有内容，用于验证来源去重）。"""
    if content_id is None:
        _counter["n"] += 1
        rc = RawContent(
            source="forum",
            content_hash=compute_content_hash(f"detail-{_counter['n']}-{shop}"),
            raw_text=text,
            city_hint=city,
            source_url=source_url,
            status="extracted",
        )
        session.add(rc)
        session.flush()
        content_id = rc.id
    mention = MentionRow(
        raw_content_id=content_id,
        shop_name_raw=shop,
        address_text=address,
        sentiment=sentiment,
        is_recommendation=True,
    )
    session.add(mention)
    session.flush()
    return mention


def _restaurant(session, name) -> Restaurant:
    return session.scalar(select(Restaurant).where(Restaurant.name == name))


# --- score_one_restaurant ---------------------------------------------------

def test_score_one_restaurant_counts_mentions(session):
    settings = make_settings()
    _seed_mention(session, "明婷饭店")
    _seed_mention(session, "老明婷饭店（总店）")  # 归一后命中同一实体
    align_mentions(session, settings=settings)
    session.commit()

    restaurant = _restaurant(session, "明婷饭店")
    score = score_one_restaurant(session, restaurant.id, settings=settings, now=NOW)

    assert score is not None
    assert score.restaurant_id == restaurant.id
    assert score.mention_count == 2
    assert score.shop_key == f"restaurant:{restaurant.id}"


def test_score_one_restaurant_missing_returns_none(session):
    assert score_one_restaurant(session, 999999, settings=make_settings()) is None


# --- build_restaurant_detail ------------------------------------------------

def test_build_restaurant_detail_fields(session):
    settings = make_settings()
    row = ingest_raw_content(
        session,
        source="xiaohongshu",
        raw_text=TEXT,
        city_hint="成都",
        source_url="https://example.com/mingting",
        settings=settings,
    )
    session.commit()
    extract_raw_content(session, row.id, settings=settings)
    session.commit()
    align_mentions(session, settings=settings)
    session.commit()

    restaurant = _restaurant(session, "明婷饭店")
    detail = build_restaurant_detail(session, restaurant.id, settings=settings, now=NOW)

    assert detail["restaurant_id"] == restaurant.id
    assert detail["name"] == "明婷饭店"
    assert detail["area"] == "青羊区"
    assert detail["avg_price"] == 65
    assert detail["cuisine"] == "川菜"
    assert detail["status"] == "active"
    assert detail["score"] > 0
    assert "脑花豆腐" in detail["recommended_dishes"]
    assert detail["praise_keywords"]

    src = detail["sources"][0]
    assert src["source"] == "xiaohongshu"
    assert src["source_url"] == "https://example.com/mingting"
    assert set(src) == {"source", "source_url", "title", "excerpt", "published_at"}


def test_build_restaurant_detail_missing_returns_none(session):
    assert build_restaurant_detail(session, 999999, settings=make_settings()) is None


# --- build_restaurant_sources ----------------------------------------------

def test_build_restaurant_sources_dedup_and_limit(session):
    settings = make_settings()
    shared = _seed_mention(
        session, "明婷饭店", source_url="https://example.com/a"
    ).raw_content_id
    _seed_mention(session, "明婷饭店", content_id=shared)  # 同内容第二条 mention
    _seed_mention(session, "明婷饭店", source_url="https://example.com/b")
    align_mentions(session, settings=settings)
    session.commit()

    restaurant = _restaurant(session, "明婷饭店")

    sources = build_restaurant_sources(session, restaurant.id)
    assert len(sources) == 2  # 同 raw_content 的两条 mention 合并为一条来源
    assert {s["source_url"] for s in sources} == {
        "https://example.com/a",
        "https://example.com/b",
    }

    assert len(build_restaurant_sources(session, restaurant.id, limit=1)) == 1
    assert build_restaurant_sources(session, 999999) is None


# --- list_cities ------------------------------------------------------------

def test_list_cities_union_hint_and_city(session):
    settings = make_settings()
    _seed_mention(session, "明婷饭店")  # 仅内容线索，尚无 City 行

    collecting = list_cities(session)
    assert [c["name"] for c in collecting] == ["成都"]
    assert collecting[0]["status"] == "collecting"
    assert collecting[0]["has_rank"] is False

    align_mentions(session, settings=settings)
    session.commit()
    build_rank_snapshot(session, "成都", settings=settings, now=NOW)
    session.commit()

    active = list_cities(session)
    assert active[0]["status"] == "active"
    assert active[0]["has_rank"] is True