import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import AlignmentReview, Mention, RawContent, Restaurant, ShopAlias
from app.services.alignment import align_mentions, confirm_review
from app.services.ingest import compute_content_hash
from app.services.normalize import (
    address_similarity,
    name_similarity,
    normalize_shop_name,
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


def _add_mention(session, *, shop, city="成都", address=None, source="forum"):
    _counter["n"] += 1
    rc = RawContent(
        source=source,
        content_hash=compute_content_hash(f"{source}-{_counter['n']}-{shop}"),
        raw_text="x",
        city_hint=city,
        status="extracted",
    )
    session.add(rc)
    session.flush()
    mention = Mention(
        raw_content_id=rc.id,
        shop_name_raw=shop,
        address_text=address,
        sentiment="positive",
        is_recommendation=True,
    )
    session.add(mention)
    session.flush()
    return mention


# --- 归一化 / 相似度 --------------------------------------------------------

def test_normalize_and_similarity():
    assert normalize_shop_name("老明婷饭店（总店）") == "明婷饭店"
    assert name_similarity("明婷", "明婷饭店") == 0.9  # 包含关系
    assert name_similarity("明婷饭店", "明婷饭店") == 1.0
    assert 0.0 <= name_similarity("张三烤鱼", "李四火锅") < 0.5

    sim = address_similarity("青羊区同心路的巷子里", "青羊区同心路")
    assert sim is not None and sim > 0
    assert address_similarity(None, "青羊区") is None


# --- 归并 -------------------------------------------------------------------

def test_same_name_merges_to_one_restaurant(session):
    _add_mention(session, shop="明婷饭店")
    _add_mention(session, shop="明婷饭店", source="dianping")

    run = align_mentions(session, settings=make_settings())
    session.commit()

    assert run.restaurants_created == 1
    assert run.mentions_aligned == 2
    assert session.scalar(select(func.count()).select_from(Restaurant)) == 1


def test_name_variant_creates_alias(session):
    _add_mention(session, shop="明婷饭店")
    _add_mention(session, shop="明婷", source="dianping")

    run = align_mentions(session, settings=make_settings())
    session.commit()

    assert run.restaurants_created == 1
    assert run.aliases_added == 1
    aliases = session.scalars(select(ShopAlias)).all()
    assert [a.alias_norm for a in aliases] == ["明婷"]


def test_traditional_and_modifier_variant_merges(session):
    _add_mention(session, shop="明婷饭店")
    _add_mention(session, shop="老明婷饭店（总店）", source="dianping")

    run = align_mentions(session, settings=make_settings())
    session.commit()

    assert run.restaurants_created == 1
    assert run.mentions_aligned == 2


# --- 灰区 / 新建 ------------------------------------------------------------

def test_grey_zone_goes_to_review(session):
    _add_mention(session, shop="成都老妈火锅")
    _add_mention(session, shop="成都老妈串串", source="dianping")

    run = align_mentions(session, settings=make_settings())
    session.commit()

    assert run.restaurants_created == 1
    assert run.mentions_review == 1
    review = session.scalars(select(AlignmentReview)).one()
    assert review.status == "pending"
    assert 0.65 <= review.score < 0.85
    # 未归并的 mention 仍是 None
    reviewed = session.get(Mention, review.mention_id)
    assert reviewed.restaurant_id is None


def test_below_review_threshold_creates_new(session):
    _add_mention(session, shop="蜀香源火锅")
    _add_mention(session, shop="蜀香源串串", source="dianping")

    run = align_mentions(session, settings=make_settings())
    session.commit()

    assert run.restaurants_created == 2
    assert run.mentions_review == 0
    assert session.scalar(select(func.count()).select_from(Restaurant)) == 2


# --- 幂等 / 审核确认 / 无城市 -----------------------------------------------

def test_review_is_idempotent_and_confirm(session):
    _add_mention(session, shop="成都老妈火锅")
    _add_mention(session, shop="成都老妈串串", source="dianping")
    align_mentions(session, settings=make_settings())
    session.commit()

    # 重跑：已在审核队列的 mention 跳过，不重复建审核
    run2 = align_mentions(session, settings=make_settings())
    session.commit()
    assert run2.mentions_review == 0
    assert session.scalar(select(func.count()).select_from(AlignmentReview)) == 1

    review = session.scalars(select(AlignmentReview)).one()
    confirm_review(session, review.id)
    session.commit()

    assert review.status == "confirmed"
    mention = session.get(Mention, review.mention_id)
    assert mention.restaurant_id == review.candidate_restaurant_id


def test_mentions_without_city_are_skipped(session):
    _add_mention(session, shop="无城市小店", city=None)

    run = align_mentions(session, settings=make_settings())
    session.commit()

    assert run.skipped == 1
    assert run.mentions_aligned == 0
    assert session.scalar(select(func.count()).select_from(Restaurant)) == 0


def test_restaurant_backfilled_from_mention(session):
    """新建 restaurant 时用 mention 的区域/菜系/人均回填；后续提及不覆盖已有值。"""
    first = _add_mention(session, shop="明婷饭店", address="青羊区同心路")
    first.area = "青羊区"
    first.cuisine = "川菜"
    first.avg_price = 65
    session.flush()

    align_mentions(session, settings=make_settings())
    session.commit()

    restaurant = session.scalars(select(Restaurant)).one()
    assert restaurant.area == "青羊区"
    assert restaurant.cuisine == "川菜"
    assert restaurant.avg_price == 65
    assert restaurant.address == "青羊区同心路"

    # 第二条同名提及（不同菜系）不覆盖已有值
    later = _add_mention(session, shop="明婷饭店", source="dianping")
    later.cuisine = "火锅"
    later.avg_price = 200
    session.flush()
    align_mentions(session, settings=make_settings())
    session.commit()

    session.refresh(restaurant)
    assert restaurant.cuisine == "川菜"
    assert restaurant.avg_price == 65