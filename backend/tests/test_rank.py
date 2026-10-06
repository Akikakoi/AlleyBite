from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import City, Mention as MentionRow, RawContent, Restaurant
from app.services.alignment import align_mentions
from app.services.extract_pipeline import extract_raw_content
from app.services.ingest import compute_content_hash, ingest_raw_content
from app.services.rank_service import build_rank_snapshot, get_latest_snapshot, get_rank

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


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


SAMPLES = [
    ("xiaohongshu", "昨天去吃了明婷饭店，藏在青羊区同心路的巷子里，环境确实一般，但脑花豆腐和霸王兔太香了，锅气足，人均才 65。"),
    ("dianping", "王妈手撕烤兔，开在玉林，兔头麻辣入味，人均 40。"),
    ("dianping", "被安利去了春熙路的耍酒馆·冒菜，一份冒菜 128，量还少，味道很一般，不会再去了。"),
    ("dianping", "【探店合作】成都新开的沸腾里火锅，团购链接见评论区，商务合作请私信。"),
]

_counter = {"n": 0}


def _seed_mention(session, shop, city="成都", sentiment="positive", address=None):
    _counter["n"] += 1
    rc = RawContent(
        source="forum",
        content_hash=compute_content_hash(f"rank-{_counter['n']}-{shop}"),
        raw_text="x",
        city_hint=city,
        status="extracted",
    )
    session.add(rc)
    session.flush()
    mention = MentionRow(
        raw_content_id=rc.id,
        shop_name_raw=shop,
        address_text=address,
        sentiment=sentiment,
        is_recommendation=True,
    )
    session.add(mention)
    session.flush()
    return mention


def test_build_rank_snapshot_orders_and_filters_excluded(session):
    settings = make_settings()
    for source, text in SAMPLES:
        row = ingest_raw_content(
            session, source=source, raw_text=text, city_hint="成都", settings=settings
        )
        session.commit()
        extract_raw_content(session, row.id, settings=settings)
        session.commit()

    align_mentions(session, settings=settings)
    session.commit()

    snapshot = build_rank_snapshot(session, "成都", settings=settings, now=NOW)
    session.commit()

    items = snapshot.items
    assert [i["name"] for i in items] == ["明婷饭店", "王妈手撕烤兔", "耍酒馆·冒菜"]
    assert [i["rank"] for i in items] == [1, 2, 3]
    assert all(i["score"] > 0 for i in items)
    assert "沸腾里火锅" not in {i["name"] for i in items}  # 硬规则剔除

    top = items[0]
    assert top["restaurant_id"] is not None
    assert top["mention_count"] == 1
    assert "脑花豆腐" in top["recommended_dishes"]
    assert top["praise_keywords"]  # 来自 mock 抽取
    assert top["sources"][0]["source"] == "xiaohongshu"
    # 抽取的 area/avg_price/cuisine 经 mention → restaurant 回填到条目
    assert top["avg_price"] == 65
    assert top["cuisine"] == "川菜"
    assert top["area"] == "青羊区"

    # 有数据后城市状态由 collecting → active
    assert session.scalar(select(City).where(City.name == "成都")).status == "active"

    # 筛选基于快照条目（菜系包含匹配 / 人均区间）
    assert {i["name"] for i in get_rank(session, "成都", cuisine="川菜")["items"]} == {
        "明婷饭店",
        "王妈手撕烤兔",
    }
    assert {i["name"] for i in get_rank(session, "成都", price_max=50)["items"]} == {
        "王妈手撕烤兔"
    }


def test_get_rank_pagination_and_filters(session):
    settings = make_settings()
    specs = [
        ("甲店", "川菜", 50, "青羊区", "positive"),
        ("乙店", "火锅", 120, "武侯区", "positive"),
        ("丙店", "川菜", 90, "锦江区", "positive"),
    ]
    for shop, _, _, _, sentiment in specs:
        _seed_mention(session, shop, sentiment=sentiment)
    align_mentions(session, settings=settings)
    session.commit()

    for shop, cuisine, price, area, _ in specs:
        restaurant = session.scalar(select(Restaurant).where(Restaurant.name == shop))
        restaurant.cuisine = cuisine
        restaurant.avg_price = price
        restaurant.area = area
    session.flush()

    build_rank_snapshot(session, "成都", settings=settings, now=NOW)
    session.commit()

    page1 = get_rank(session, "成都", page=1, page_size=2)
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["items"][0]["rank"] == 1
    page2 = get_rank(session, "成都", page=2, page_size=2)
    assert len(page2["items"]) == 1

    assert {i["name"] for i in get_rank(session, "成都", cuisine="川菜")["items"]} == {"甲店", "丙店"}
    assert {i["name"] for i in get_rank(session, "成都", price_max=60)["items"]} == {"甲店"}
    assert {i["name"] for i in get_rank(session, "成都", price_min=100)["items"]} == {"乙店"}
    assert {i["name"] for i in get_rank(session, "成都", area="青羊区")["items"]} == {"甲店"}


def test_get_rank_returns_none_without_snapshot(session):
    assert get_rank(session, "成都") is None
    assert get_latest_snapshot(session, "成都") is None


def test_latest_snapshot_is_returned(session):
    settings = make_settings()
    _seed_mention(session, "甲店")
    align_mentions(session, settings=settings)
    session.commit()

    build_rank_snapshot(session, "成都", settings=settings, now=NOW)
    session.commit()

    # 增加一家店后重排，读取应返回最新快照
    _seed_mention(session, "乙店")
    align_mentions(session, settings=settings)
    session.commit()
    build_rank_snapshot(
        session, "成都", settings=settings, now=NOW.replace(hour=6)
    )
    session.commit()

    data = get_rank(session, "成都")
    assert data["total"] == 2
    assert {i["name"] for i in data["items"]} == {"甲店", "乙店"}


def test_build_rank_snapshot_unknown_city_raises(session):
    with pytest.raises(LookupError):
        build_rank_snapshot(session, "不存在的城市", settings=make_settings())