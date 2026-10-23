"""方向四：老店档案 + 存活监测（第 1/2 批）单测。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from collectors_fakes import memory_session

from app.collectors.amap import PoiRecord, upsert_pois
from app.db.models import Mention, RawContent, Restaurant, UgcPost, User
from app.services.alignment import get_or_create_city
from app.services.liveness import (
    apply_user_evidence,
    backfill_first_last_mentioned,
    backfill_last_checkin,
    evaluate_liveness,
    mark_city_misses,
    mark_seen,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _make_restaurant(session, city_name="成都", **kwargs) -> Restaurant:
    city = get_or_create_city(session, city_name)
    name = kwargs.pop("name", "测试店")
    row = Restaurant(
        city_id=city.id,
        name=name,
        name_norm=kwargs.pop("name_norm", name),
        **kwargs,
    )
    session.add(row)
    session.flush()
    return row


def _add_mention(session, restaurant: Restaurant, *, pub, crawl) -> None:
    raw = RawContent(
        source="seed",
        raw_text="提到" + restaurant.name,
        content_hash=f"hash-{restaurant.name}-{pub}-{crawl}",
        published_at=pub,
        crawled_at=crawl,
        status="extracted",
    )
    session.add(raw)
    session.flush()
    session.add(
        Mention(
            raw_content_id=raw.id,
            restaurant_id=restaurant.id,
            shop_name_raw=restaurant.name,
        )
    )
    session.flush()


# --- 第 1 批：老店档案回填 ---------------------------------------------------

def test_backfill_confident_uses_published_at():
    with memory_session() as s:
        shop = _make_restaurant(s, name="明婷饭店")
        _add_mention(s, shop, pub=_utc(2005, 3, 1), crawl=_utc(2026, 10, 1))
        _add_mention(s, shop, pub=_utc(2019, 5, 17), crawl=_utc(2026, 10, 7))

        stats = backfill_first_last_mentioned(s)

        assert stats["restaurants_with_mention"] == 1
        assert stats["confident"] == 1
        assert shop.first_mentioned_at == _utc(2005, 3, 1)
        assert shop.first_evidence_confident is True
        # last 取每条 coalesce(published_at, crawled_at) 的最大值：
        # 有发布时间的提及以发布时间为准（2019），不用采集时间顶替
        assert shop.last_mentioned_at == _utc(2019, 5, 17)


def test_backfill_crawl_only_is_not_confident():
    """仅 crawled_at 支撑时：落库但打 False 标记——既不展示年限也不参与打分。"""
    with memory_session() as s:
        shop = _make_restaurant(s, name="曾家烧烤")
        _add_mention(s, shop, pub=None, crawl=_utc(2026, 10, 14))

        stats = backfill_first_last_mentioned(s)

        assert stats["confident"] == 0
        assert stats["crawl_only"] == 1
        assert shop.first_mentioned_at == _utc(2026, 10, 14)
        assert shop.first_evidence_confident is False


def test_backfill_mixed_pub_and_null_prefers_published():
    """部分提及有发布时间：取有值的 MIN，可靠标记不受空值行影响。"""
    with memory_session() as s:
        shop = _make_restaurant(s, name="甘记肥肠粉")
        _add_mention(s, shop, pub=_utc(2019, 5, 17), crawl=_utc(2026, 10, 7))
        _add_mention(s, shop, pub=None, crawl=_utc(2026, 10, 10))

        backfill_first_last_mentioned(s)

        assert shop.first_mentioned_at == _utc(2019, 5, 17)
        assert shop.first_evidence_confident is True
        # last 取 coalesce 后的最大值
        assert shop.last_mentioned_at == _utc(2026, 10, 10)


def test_backfill_idempotent():
    with memory_session() as s:
        shop = _make_restaurant(s, name="吴氏蹄花")
        _add_mention(s, shop, pub=_utc(2017, 9, 20), crawl=_utc(2026, 10, 7))

        first = backfill_first_last_mentioned(s)
        shop.first_mentioned_at = None  # 模拟被破坏
        second = backfill_first_last_mentioned(s)

        assert first == second
        assert shop.first_mentioned_at == _utc(2017, 9, 20)


def test_backfill_without_mentions_leaves_restaurants_untouched():
    with memory_session() as s:
        shop = _make_restaurant(s, name="纯POI店")

        stats = backfill_first_last_mentioned(s)

        assert stats["restaurants_with_mention"] == 0
        assert shop.first_mentioned_at is None
        assert shop.first_evidence_confident is False


# --- 第 2 批：存活监测 -------------------------------------------------------

def test_mark_seen_resets_miss_and_revives_suspect():
    with memory_session() as s:
        seen_at = _utc(2026, 10, 14, 3, 0)
        shop = _make_restaurant(
            s, name="回活店", liveness="suspect", miss_count=2, last_seen_at=seen_at - timedelta(days=3)
        )

        mark_seen(shop, seen_at)

        assert shop.miss_count == 0
        assert shop.liveness == "active"
        assert shop.last_seen_at == seen_at


def test_mark_seen_does_not_revive_closed():
    """closed 是终端态：命中只刷字段，reopen 由第 3 批状态机处理。"""
    with memory_session() as s:
        seen_at = _utc(2026, 10, 14, 3, 0)
        shop = _make_restaurant(s, name="closed店", liveness="closed", miss_count=6)

        mark_seen(shop, seen_at)

        assert shop.liveness == "closed"
        assert shop.miss_count == 0
        assert shop.last_seen_at == seen_at


def test_upsert_pois_marks_seen_on_match():
    with memory_session() as s:
        seen_at = _utc(2026, 10, 14, 3, 0)
        records = [PoiRecord(poi_id="B001", name="陈麻婆豆腐")]

        upsert_pois(s, "成都", records, seen_at=seen_at)

        row = s.query(Restaurant).one()
        # SQLite 读回 naive（值本身已是 UTC），Postgres 读回 aware；统一按 UTC 对比
        assert row.last_seen_at.replace(tzinfo=timezone.utc) == seen_at
        assert row.miss_count == 0
        assert row.liveness == "active"


def test_mark_city_misses_counts_unmatched_poi_shops_only():
    with memory_session() as s:
        seen_at = _utc(2026, 10, 14, 3, 0)
        # 命中店：upsert 后 last_seen_at == seen_at，不应被计 miss
        hit = _make_restaurant(s, name="命中店", poi_id="B001")
        mark_seen(hit, seen_at)
        # 未命中店：有 POI 底座、曾经见过但 last_seen_at 落后 → miss +1
        miss = _make_restaurant(
            s, name="失联店", poi_id="B002", miss_count=1,
            last_seen_at=seen_at - timedelta(days=2),
        )
        # 纯 mention 店：无 POI 底座，不参与计数
        _make_restaurant(s, name="纯提及店")
        # closed 店：终端态不再累计
        closed = _make_restaurant(s, name="已闭店", poi_id="B003", liveness="closed")
        # 其他城市店：不跨界
        other = _make_restaurant(s, city_name="上海", name="外地店", poi_id="B004")

        marked = mark_city_misses(s, "成都", before=seen_at)

        assert marked == 1
        assert miss.miss_count == 2
        assert hit.miss_count == 0
        assert closed.miss_count == 0
        assert other.miss_count == 0
        assert s.query(Restaurant).filter_by(name="纯提及店").one().miss_count == 0


def test_mark_city_misses_never_seen_shops_do_not_count():
    """检索盲区店（从未命中，last_seen_at IS NULL）不累计 miss：
    固定关键词召回有天然盲区（实测全局约 35%），盲区店没有「失联」概念，
    若累计会在数日内整批误杀降权。只有先见过、后丢失才是真失联。"""
    with memory_session() as s:
        _make_restaurant(s, name="盲区店", poi_id="B100")

        marked = mark_city_misses(s, "成都", before=_utc(2026, 10, 14, 3, 0))

        assert marked == 0
        row = s.query(Restaurant).filter_by(name="盲区店").one()
        assert row.miss_count == 0


def test_mark_city_misses_unknown_city_is_noop():
    with memory_session() as s:
        assert mark_city_misses(s, "不存在的城市", before=_utc(2026, 10, 14)) == 0


# --- 打卡补证（2026-10-23 数据产品路线）--------------------------------------

def test_apply_user_evidence_resets_miss_and_revives_suspect():
    with memory_session() as s:
        shop = _make_restaurant(s, name="回活店", liveness="suspect", miss_count=3)

        apply_user_evidence(shop, _utc(2026, 10, 23, 12, 0))

        assert shop.liveness == "active"
        assert shop.miss_count == 0
        assert shop.last_checkin_at == _utc(2026, 10, 23, 12, 0)


def test_apply_user_evidence_reopens_closed():
    """approved 打卡是审核过的第一手在店证据，强于 POI 召回（可能撞名），
    故 closed 也可回活；closed_at 同时清掉，避免陈旧闭店时间残留。"""
    with memory_session() as s:
        shop = _make_restaurant(
            s, name="误闭店", liveness="closed", miss_count=12,
            closed_at=_utc(2026, 10, 1),
        )

        apply_user_evidence(shop, _utc(2026, 10, 23, 12, 0))

        assert shop.liveness == "active"
        assert shop.closed_at is None
        assert shop.miss_count == 0
        assert shop.last_checkin_at == _utc(2026, 10, 23, 12, 0)


def test_apply_user_evidence_keeps_relocated():
    """relocated 是人工处置态：证据字段照常落地，liveness 不自动改。"""
    with memory_session() as s:
        shop = _make_restaurant(s, name="已搬迁", liveness="relocated", miss_count=7)

        apply_user_evidence(shop, _utc(2026, 10, 23, 12, 0))

        assert shop.liveness == "relocated"
        assert shop.miss_count == 0
        assert shop.last_checkin_at is not None


def test_backfill_last_checkin_uses_approved_only():
    """先审后显是信任边界：pending/rejected 不构成存活证据。"""
    with memory_session() as s:
        user = User(username="u1", password_hash="x")
        s.add(user)
        s.flush()
        shop = _make_restaurant(s, name="打卡店")
        other = _make_restaurant(s, name="无打卡店")
        for status, created in (
            ("approved", _utc(2026, 10, 20)),
            ("pending", _utc(2026, 10, 22)),
            ("rejected", _utc(2026, 10, 23)),
        ):
            s.add(
                UgcPost(
                    user_id=user.id,
                    restaurant_id=shop.id,
                    content="刚去过，还开着",
                    status=status,
                    created_at=created,
                )
            )
        s.flush()

        stats = backfill_last_checkin(s)

        assert stats["restaurants_with_checkin"] == 1
        assert shop.last_checkin_at == _utc(2026, 10, 20)
        assert other.last_checkin_at is None


# --- 第 3 批：状态机 ---------------------------------------------------------

def _settings(**overrides):
    from collectors_fakes import make_settings

    defaults = dict(liveness_suspect_misses=5, liveness_closed_misses=10)
    defaults.update(overrides)
    return make_settings(**defaults)


def _make_with_miss(session, name, miss_count, **kwargs) -> Restaurant:
    return _make_restaurant(session, name=name, poi_id=f"B-{name}", miss_count=miss_count, **kwargs)


def test_evaluate_liveness_boundaries():
    """阈值边界（保守默认 5/10）：4 不动、5 进 suspect、10 判 closed。"""
    with memory_session() as s:
        safe = _make_with_miss(s, "安全店", 4)
        edge_suspect = _make_with_miss(s, "临界店", 5)
        closed_edge = _make_with_miss(s, "闭店临界", 10)

        stats = evaluate_liveness(s, settings=_settings())

        # 闭店临界（miss=10 ≥ closed 阈值）直接 active→closed，不经过 suspect
        assert stats["to_suspect"] == 1
        assert stats["to_closed"] == 1
        assert safe.liveness == "active"
        assert edge_suspect.liveness == "suspect"
        assert closed_edge.liveness == "closed"
        assert closed_edge.closed_at is not None


def test_evaluate_liveness_skips_terminal_states():
    """closed / relocated 不参与自动迁移；closed 的 closed_at 不被改写。"""
    with memory_session() as s:
        closed_at = _utc(2026, 10, 1)
        closed = _make_with_miss(s, "已闭店", 15, liveness="closed", closed_at=closed_at)
        relocated = _make_with_miss(s, "已搬迁", 15, liveness="relocated")

        stats = evaluate_liveness(s, settings=_settings())

        assert stats == {"to_suspect": 0, "to_closed": 0}
        # SQLite 读回 naive，统一按 UTC 对比；closed_at 未被改写
        assert closed.closed_at.replace(tzinfo=timezone.utc) == closed_at
        assert relocated.liveness == "relocated"


def test_evaluate_liveness_idempotent():
    with memory_session() as s:
        _make_with_miss(s, "临界店", 5)

        first = evaluate_liveness(s, settings=_settings())
        second = evaluate_liveness(s, settings=_settings())

        assert first["to_suspect"] == 1
        assert second == {"to_suspect": 0, "to_closed": 0}


def test_evaluate_liveness_thresholds_configurable():
    """阈值走配置：保守值可通过环境变量调宽（上线首月观察期用）。"""
    with memory_session() as s:
        shop = _make_with_miss(s, "宽限店", 5)

        evaluate_liveness(s, settings=_settings(liveness_suspect_misses=8))

        assert shop.liveness == "active"


# --- 第 3 批：打分接入 -------------------------------------------------------

def test_score_shop_tenure_bonus_only_from_confident_evidence():
    """老店加成：仅 first_confident_at（可靠证据）参与；退化数据为 0。"""
    from app.services.scoring import compute_tenure_bonus

    settings = _settings()
    now = _utc(2026, 10, 14)

    assert compute_tenure_bonus(_utc(2010, 1, 1), now, settings) == 0.15   # ≥10 年
    assert compute_tenure_bonus(_utc(2020, 1, 1), now, settings) == 0.08   # ≥5 年
    assert compute_tenure_bonus(_utc(2024, 1, 1), now, settings) == 0.0    # <5 年
    assert compute_tenure_bonus(None, now, settings) == 0.0                # 无可靠证据


def test_apply_liveness_and_tenure_suspect_penalty_and_bonus():
    from app.services.scoring import ShopScore, apply_liveness_and_tenure

    settings = _settings()

    def _score(**kw):
        return ShopScore(shop_key="x", shop_name="x", score=2.0, **kw)

    scores = [
        _score(tenure_bonus=0.15),                       # 老店：2.0 + 0.15
        _score(liveness="suspect"),                      # 失联：2.0 × 0.9
        _score(tenure_bonus=0.15, liveness="suspect"),   # 先加成后降权
        _score(),                                        # 普通店不动
        _score(excluded=True, tenure_bonus=0.15),        # 已剔除店不动
    ]
    apply_liveness_and_tenure(scores, settings=settings)

    # 注：round(2.15, 1)=2.1（2.15 的浮点表示是 2.1499…），断言按 Python 实际舍入
    assert scores[0].score == 2.1
    assert scores[1].score == 1.8
    assert scores[2].score == round((2.0 + 0.15) * 0.9, 1)
    assert scores[3].score == 2.0
    assert scores[4].score == 2.0


def test_apply_liveness_and_tenure_clamped_to_five():
    from app.services.scoring import ShopScore, apply_liveness_and_tenure

    settings = _settings()
    top = ShopScore(shop_key="x", shop_name="x", score=4.95, tenure_bonus=0.15)
    apply_liveness_and_tenure([top], settings=settings)
    assert top.score == 5.0


def test_collect_shop_scores_filters_closed_restaurants():
    """closed 店在聚合入口被剔除：不进榜（不靠分数压到 0）。"""
    from app.services.scoring_service import collect_shop_scores

    with memory_session() as s:
        open_shop = _make_restaurant(s, name="开门店")
        closed_shop = _make_restaurant(s, name="闭门店", liveness="closed")
        for shop in (open_shop, closed_shop):
            _add_mention(s, shop, pub=_utc(2024, 1, 1), crawl=_utc(2026, 10, 1))

        scores = collect_shop_scores(s, settings=_settings())

        ids = {sc.restaurant_id for sc in scores}
        assert open_shop.id in ids
        assert closed_shop.id not in ids
