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


# --- 方向五第 2 批：多候选守卫 / 疑似分店标注 / is_chain 回填 -----------------

def _add_restaurant(session, *, name, city="成都", is_chain=False):
    from app.db.models import City

    city_row = session.query(City).filter_by(name=city).one_or_none()
    if city_row is None:
        city_row = City(name=city)
        session.add(city_row)
        session.flush()
    row = Restaurant(
        city_id=city_row.id,
        name=name,
        name_norm=normalize_shop_name(name),
        is_chain=is_chain,
    )
    session.add(row)
    session.flush()
    return row


def test_pure_branch_suffix_matches_at_full_similarity():
    # 纯分店后缀 = 同一实体强信号；含定位词的总店不算（"全球"不是纯后缀）
    assert name_similarity("小毛肚老火锅", "小毛肚老火锅总店") == 0.9  # 包含规则仍在
    assert normalize_shop_name("小毛肚老火锅总店") == "小毛肚老火锅"  # norm 层已剥
    assert normalize_shop_name("小龙坎火锅全球总店") == "小龙坎火锅全球"


def test_brand_stem_groups_numbered_branches():
    from app.services.normalize import brand_stem

    assert (
        brand_stem("乐三姐飘香馆一店脆鱼") == brand_stem("乐三姐飘香馆二店脆鱼")
    )
    assert brand_stem("明婷饭店") == "明婷饭店"  # 无编号词原样返回


def test_ambiguous_branch_mention_goes_to_review(session):
    """裸品牌名对同城一店/二店同分：不可随机归并，进审核（多候选同分）。"""
    _add_restaurant(session, name="张记面馆一店")
    _add_restaurant(session, name="张记面馆二店")
    _add_mention(session, shop="张记面馆")

    run = align_mentions(session, settings=make_settings())
    session.commit()

    assert run.mentions_review == 1
    assert run.restaurants_created == 0
    review = session.scalars(select(AlignmentReview)).one()
    assert review.reason == "多候选同分，疑似分店"
    assert session.get(Mention, review.mention_id).restaurant_id is None


def test_branch_context_review_is_annotated(session):
    """灰区候选若处于同城同品牌多店语境，审核单标注疑似分店。"""
    _add_mention(session, shop="成都老妈火锅")
    align_mentions(session, settings=make_settings())
    session.commit()

    restaurant = session.scalars(select(Restaurant)).one()
    restaurant.is_chain = True
    session.flush()

    _add_mention(session, shop="成都老妈串串", source="dianping")
    align_mentions(session, settings=make_settings())
    session.commit()

    review = session.scalars(select(AlignmentReview)).one()
    assert review.status == "pending"
    assert review.reason == "疑似分店（同城同品牌多店）"


def test_non_branch_review_keeps_default_reason(session):
    _add_mention(session, shop="成都老妈火锅")
    align_mentions(session, settings=make_settings())
    session.commit()

    _add_mention(session, shop="成都老妈串串", source="dianping")
    align_mentions(session, settings=make_settings())
    session.commit()

    review = session.scalars(select(AlignmentReview)).one()
    assert review.reason == "灰区相似度"


def test_backfill_chain_flags_groups_by_brand_stem(session):
    from app.services.alignment import backfill_chain_flags

    first = _add_restaurant(session, name="张记面馆一店")
    second = _add_restaurant(session, name="张记面馆二店")
    third = _add_restaurant(session, name="王记烧烤")

    stats = backfill_chain_flags(session)
    session.commit()

    assert stats["brand_groups"] == 1
    session.refresh(first)
    session.refresh(second)
    session.refresh(third)
    assert first.is_chain and second.is_chain
    assert not third.is_chain

    # 幂等：重跑不再新增
    stats2 = backfill_chain_flags(session)
    assert stats2["flagged"] == 0