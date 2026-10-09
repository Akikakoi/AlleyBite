from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Mention as MentionRow
from app.db.models import RawContent, Restaurant
from app.services.alignment import align_mentions
from app.services.extract_pipeline import extract_raw_content
from app.services.ingest import compute_content_hash, ingest_raw_content
from app.services.scoring import (
    apply_bayesian_smooth,
    build_rank_reasons,
    FEATURE_WEIGHTS,
    MentionFact,
    ShopSignals,
    aggregate_shop,
    apply_hard_rules,
    compute_confidence_factor,
    compute_time_decay,
    normalize_shop_name,
    score_shop,
)
from app.services.scoring_service import collect_shop_scores

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


# --- 店名归一化 -------------------------------------------------------------

def test_normalize_shop_name_strips_modifiers_and_traditional():
    assert normalize_shop_name("老明婷饭店（总店）") == "明婷饭店"
    assert normalize_shop_name("沸騰裡火鍋") == "沸腾里火锅"
    assert normalize_shop_name("正宗王妈手撕烤兔") == "王妈手撕烤兔"


def test_feature_weights_sum_to_one():
    assert round(sum(FEATURE_WEIGHTS.values()), 6) == 1.0


# --- 公式与因子 -------------------------------------------------------------

def test_time_decay_half_life():
    assert compute_time_decay(NOW - timedelta(days=180), NOW, 180) == 0.5
    assert compute_time_decay(NOW - timedelta(days=360), NOW, 180) == 0.25
    assert compute_time_decay(NOW, NOW, 180) == 1.0
    assert compute_time_decay(None, NOW, 180) == 1.0


def test_confidence_factor_saturates():
    assert compute_confidence_factor(0, 3) == 0.0
    assert compute_confidence_factor(1, 3) == 0.3333
    assert compute_confidence_factor(3, 3) == 1.0
    assert compute_confidence_factor(10, 3) == 1.0


def test_score_shop_full_formula():
    signals = ShopSignals(
        shop_key="明婷饭店",
        display_name="明婷饭店",
        mention_count=4,
        independent_source_count=4,
        positive_count=4,
        intensity_sum=4.0,
        uniqueness_hits=2,
        paradox_good_count=1,
        earliest_at=NOW - timedelta(days=400),
        latest_at=NOW,
    )
    result = score_shop(signals, settings=make_settings(), now=NOW)

    # 逐项：0.5·0.20 + 1·0.18 + 0.5·0.15 + 0.5·0.12 + 1·0.10 + 1·0.10 + 1·0.08 + 1·0.07
    assert result.base_score == 0.765
    assert result.time_decay == 1.0
    assert result.confidence_factor == 1.0
    assert result.score == 3.8  # 5 分制，一位小数
    assert result.excluded is False


def test_score_shop_single_mention_smoothed_by_bayesian():
    """单条证据的店不再被线性压分（质量分如实），压分交给聚合层贝叶斯平均。"""
    signals = ShopSignals(
        shop_key="x", display_name="x", mention_count=1, independent_source_count=1,
        positive_count=1, intensity_sum=1.0, latest_at=NOW,
    )
    result = score_shop(signals, settings=make_settings(), now=NOW)
    assert result.confidence_factor == 0.3333  # 仍作为信息展示
    assert result.score == 2.2  # 质量分如实（5 分制一位小数）

    # 聚合层：单条店在城市批内被贝叶斯平滑收敛到先验附近
    good = ShopSignals(
        shop_key="y", display_name="y", mention_count=8, independent_source_count=8,
        positive_count=8, intensity_sum=8.0, uniqueness_hits=2, paradox_good_count=1,
        earliest_at=NOW - timedelta(days=400), latest_at=NOW,
    )
    scores = [
        score_shop(signals, settings=make_settings(), now=NOW),
        score_shop(good, settings=make_settings(), now=NOW),
    ]
    apply_bayesian_smooth(scores, prior_strength=2.0)
    single, solid = scores
    assert solid.score > single.score  # 好店仍显著高于单条店
    assert single.score > 2.2  # 但被先验抬升而非粗暴压低
    assert all(0.0 <= s.score <= 5.0 for s in scores)


# --- 上榜理由（方向一：可溯源证据链） ---------------------------------------

def test_build_rank_reasons_covers_signals_and_risks():
    signals = ShopSignals(
        shop_key="x", display_name="x", mention_count=6, independent_source_count=3,
        positive_count=5, local_known_count=4, local_count=3,
        avg_price=45.0, earliest_at=NOW - timedelta(days=800), latest_at=NOW,
        uniqueness_hits=2, paradox_good_count=1, influencer_hits=1,
    )
    reasons = build_rank_reasons(signals, now=NOW)
    joined = "；".join(reasons)
    assert "3 家独立来源" in joined
    assert "正面口碑提及 5 次" in joined
    assert "本地人认可（3/4" in joined
    assert "人均约 ¥45" in joined
    assert "约 2 年" in joined
    assert "独特语境" in joined
    assert "味道惊艳" in joined
    assert "网红探店话术，已降权" in joined
    assert len(reasons) <= 8


def test_build_rank_reasons_minimal_and_empty():
    # 仅一条提及：中性表述
    minimal = build_rank_reasons(
        ShopSignals(shop_key="x", display_name="x", mention_count=1,
                    independent_source_count=1, positive_count=1),
        now=NOW,
    )
    assert minimal[0] == "1 条公开提及记录"
    # 零信号：不生成任何理由
    assert build_rank_reasons(ShopSignals(shop_key="y", display_name="y"), now=NOW) == []


# --- 硬规则（6.3） ----------------------------------------------------------

def test_hard_rule_ad_ratio_excludes():
    signals = ShopSignals(
        shop_key="x", display_name="x", mention_count=5, independent_source_count=5,
        positive_count=1, intensity_sum=1.0, ad_mention_count=2, latest_at=NOW,
    )
    rules = apply_hard_rules(signals, make_settings())
    assert rules.excluded is True
    assert "广告" in rules.reason
    assert score_shop(signals, settings=make_settings(), now=NOW).score == 0.0


def test_hard_rule_chain_blacklist_excludes():
    signals = ShopSignals(
        shop_key="海底捞", display_name="海底捞火锅", mention_count=3,
        independent_source_count=3, latest_at=NOW,
    )
    rules = apply_hard_rules(signals, make_settings(chain_brand_blacklist="海底捞,肯德基"))
    assert rules.excluded is True
    assert "黑名单" in rules.reason


def test_hard_rule_mall_address_excludes():
    signals = ShopSignals(
        shop_key="r:1", display_name="龙森园火锅", mention_count=3,
        independent_source_count=3, latest_at=NOW,
        address="交子大道33号中国华商金融中心T1座3楼",
    )
    rules = apply_hard_rules(signals, make_settings())
    assert rules.excluded is True
    assert "商场" in rules.reason
    assert score_shop(signals, settings=make_settings(), now=NOW).score == 0.0


def test_hard_rule_street_address_not_excluded():
    signals = ShopSignals(
        shop_key="r:2", display_name="小灶恰鱼·浏阳土菜", mention_count=3,
        independent_source_count=3, latest_at=NOW,
        address="望岳街道府后路社区道坡小区3栋5单元1层",
    )
    assert apply_hard_rules(signals, make_settings()).excluded is False


def test_hard_rule_mall_center_variants_exclude():
    """具名商业体变体（金融国际中心/华商中心）应剔除：只写"金融中心"匹配不到前者。"""
    for addr in (
        "天府大道北段966号天府金融国际中心北塔11号楼4层",
        "交子大道33号华商中心3层",
    ):
        signals = ShopSignals(
            shop_key="r:m", display_name="某店", mention_count=3,
            independent_source_count=3, latest_at=NOW, address=addr,
        )
        rules = apply_hard_rules(signals, make_settings())
        assert rules.excluded is True, addr
        assert "商场" in rules.reason


def test_hard_rule_street_shop_near_center_not_excluded():
    """含"中心"但属街边店的地址不得误杀（地铁站名 / 隔邻单位 / 底商）。"""
    for addr in (
        "体育东路39号(体育中心地铁站D3口步行450米)",
        "麓景路151号九阳阳光服务中心隔壁",
        "凤城七路旭辉中心底商",
    ):
        signals = ShopSignals(
            shop_key="r:s", display_name="某店", mention_count=3,
            independent_source_count=3, latest_at=NOW, address=addr,
        )
        assert apply_hard_rules(signals, make_settings()).excluded is False, addr


def test_default_chain_blacklist_hits_national_chain():
    signals = ShopSignals(
        shop_key="r:3", display_name="点都德(北京路贰店)", mention_count=3,
        independent_source_count=3, latest_at=NOW,
    )
    rules = apply_hard_rules(signals, make_settings())
    assert rules.excluded is True
    assert "黑名单" in rules.reason


def test_hard_rule_burst_halves_score():
    facts = [
        MentionFact(
            content_id=i, platform="dianping", source_url=None, city_hint="成都",
            author_city=None, at=NOW - timedelta(days=1), low_trust=False,
            shop_name_raw="某店", sentiment="positive", is_recommendation=True,
        )
        for i in range(20)
    ]
    signals = aggregate_shop("某店", facts, make_settings())
    assert signals.burst is True

    rules = apply_hard_rules(signals, make_settings())
    assert rules.multiplier == 0.5
    assert score_shop(signals, settings=make_settings(), now=NOW).penalty_multiplier == 0.5


# --- 聚合 + 落库端到端 ------------------------------------------------------

SAMPLES = [
    ("xiaohongshu", "成都", "昨天去吃了明婷饭店，藏在青羊区同心路的巷子里，环境确实一般，但脑花豆腐和霸王兔太香了，锅气足，人均才 65。", False),
    ("dianping", "成都", "被安利去了春熙路的耍酒馆·冒菜，一份冒菜 128，量还少，味道很一般，不会再去了。", False),
    ("dianping", "成都", "【探店合作】成都新开的沸腾里火锅，团购链接见评论区，商务合作请私信。", True),
]


def test_collect_shop_scores_from_db(session):
    settings = make_settings()
    for source, city, text, _ in SAMPLES:
        row = ingest_raw_content(
            session, source=source, raw_text=text, city_hint=city, settings=settings
        )
        session.commit()
        extract_raw_content(session, row.id, settings=settings)
        session.commit()

    align_mentions(session, settings=settings)
    session.commit()

    scores = collect_shop_scores(session, settings=settings, now=NOW)

    names = {s.shop_name for s in scores}
    assert {"明婷饭店", "耍酒馆·冒菜", "沸腾里火锅"} <= names
    assert all(s.shop_key.startswith("restaurant:") for s in scores)

    excluded = [s for s in scores if s.excluded]
    assert len(excluded) == 1
    assert excluded[0].shop_name == "沸腾里火锅"
    assert scores[-1].excluded is True  # 被剔除的排最后
    assert scores[0].excluded is False

    mingting = next(s for s in scores if s.shop_name == "明婷饭店")
    assert mingting.mention_count == 1
    assert mingting.confidence_factor == 0.3333
    assert mingting.rank == 1


def test_collect_shop_scores_groups_by_restaurant(session):
    """同一 restaurant 的多个名称变体（含繁简/修饰词）合并为一条得分。"""
    settings = make_settings()
    for i, shop in enumerate(["明婷饭店", "老明婷饭店（总店）"]):
        rc = RawContent(
            source="forum",
            content_hash=compute_content_hash(f"variant-{i}"),
            raw_text="x",
            city_hint="成都",
            status="extracted",
        )
        session.add(rc)
        session.flush()
        session.add(
            MentionRow(
                raw_content_id=rc.id,
                shop_name_raw=shop,
                sentiment="positive",
                is_recommendation=True,
            )
        )
        session.flush()

    align_mentions(session, settings=settings)
    session.commit()

    scores = collect_shop_scores(session, settings=settings, now=NOW)
    assert len(scores) == 1
    assert scores[0].shop_key.startswith("restaurant:")
    assert scores[0].shop_name == "明婷饭店"
    assert scores[0].mention_count == 2


def test_collect_shop_scores_city_filter(session):
    settings = make_settings()
    row = ingest_raw_content(
        session, source="forum", raw_text=SAMPLES[0][2], city_hint="成都", settings=settings
    )
    session.commit()
    extract_raw_content(session, row.id, settings=settings)
    session.commit()
    align_mentions(session, settings=settings)
    session.commit()

    assert collect_shop_scores(session, city_hint="成都", settings=settings, now=NOW)
    assert collect_shop_scores(session, city_hint="北京", settings=settings, now=NOW) == []


def test_collect_shop_scores_skips_non_active_restaurant(session):
    """仅纳入 status=active 的 restaurant（文档 6.4 第 1 步）。"""
    settings = make_settings()
    row = ingest_raw_content(
        session, source="forum", raw_text=SAMPLES[0][2], city_hint="成都", settings=settings
    )
    session.commit()
    extract_raw_content(session, row.id, settings=settings)
    session.commit()
    align_mentions(session, settings=settings)
    session.commit()

    assert collect_shop_scores(session, settings=settings, now=NOW)

    restaurant = session.scalars(select(Restaurant)).one()
    restaurant.status = "merged"
    session.flush()

    assert collect_shop_scores(session, settings=settings, now=NOW) == []