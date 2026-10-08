"""榜单生成与读取（文档 6.4 / 7.2 rank_snapshot / 7.3 条目结构）。

- build_rank_snapshot：取城市 active restaurant → 打分 → 硬规则过滤 → Top N → 落 rank_snapshot，
  并失效该城市的榜单缓存（文档 6.4 第 6 步）。
- get_rank：读该城市**最新**快照，按筛选与分页返回；Redis 可用时走 read-through
  缓存（RankCache，键含筛选与 days），不可用则直读库内快照。
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..core.config import Settings, get_settings
from ..db.models import City, Mention as MentionRow, RankSnapshot, Restaurant
from .cache import RankCache
from .scoring import apply_bayesian_smooth, to_utc
from .scoring_service import collect_shop_scores, score_one_restaurant

ALGORITHM_VER = "score-v1"
DEFAULT_TOP_N = 50
_MAX_KEYWORDS = 5
_MAX_DISHES = 5
_MAX_SOURCES = 3
MAX_DETAIL_SOURCES = 10
MAX_SOURCE_LIST = 20
_EXCERPT_LIMIT = 120


def _mention_time(row: MentionRow) -> datetime | None:
    content = row.raw_content
    at = None
    if content is not None:
        at = content.published_at or content.crawled_at
    return to_utc(at or row.created_at)


def _iter_mentions(session: Session, restaurant_id: int) -> list[MentionRow]:
    return list(
        session.scalars(
            select(MentionRow)
            .options(joinedload(MentionRow.raw_content))
            .where(MentionRow.restaurant_id == restaurant_id)
            .order_by(MentionRow.id)
        ).all()
    )


def _collect_sources(mentions: list[MentionRow], limit: int) -> list[dict]:
    """按 raw_content 去重取来源引用；excerpt 取 evidence_span 前 _EXCERPT_LIMIT 字。"""
    sources: list[dict] = []
    seen_contents: set[int] = set()
    for mention in mentions:
        content = mention.raw_content
        if content is None or content.id in seen_contents:
            continue
        seen_contents.add(content.id)
        excerpt = (mention.evidence_span or "").strip()[:_EXCERPT_LIMIT] or None
        published = to_utc(content.published_at)
        sources.append(
            {
                "source": content.source,
                "source_url": content.source_url,
                "title": content.raw_title,
                "excerpt": excerpt,
                "published_at": published.isoformat() if published else None,
            }
        )
        if len(sources) >= limit:
            break
    return sources


def _enrich_from_mentions(
    session: Session, restaurant_id: int, *, limit: int = _MAX_SOURCES
) -> dict:
    """按 restaurant_id 聚合 mention → 口碑关键词 / 推荐菜 / 来源 / 最近提及 / 地址兜底。"""
    mentions = _iter_mentions(session, restaurant_id)
    praise = Counter(kw for m in mentions for kw in (m.praise_keywords or []))
    complaints = Counter(kw for m in mentions for kw in (m.complaints or []))
    dishes = Counter(d for m in mentions for d in (m.dishes or []))

    times = [t for t in (_mention_time(m) for m in mentions) if t is not None]
    last_at = max(times) if times else None

    return {
        "praise_keywords": [k for k, _ in praise.most_common(_MAX_KEYWORDS)],
        "complaints": [k for k, _ in complaints.most_common(_MAX_KEYWORDS)],
        "recommended_dishes": [d for d, _ in dishes.most_common(_MAX_DISHES)],
        "mention_count": len(mentions),
        "last_mentioned_at": last_at.date().isoformat() if last_at else None,
        "address_text": mentions[0].address_text if mentions else None,
        "sources": _collect_sources(mentions, limit),
    }


def _build_item(session: Session, score, rank: int) -> dict:
    restaurant = session.get(Restaurant, score.restaurant_id)
    enriched = _enrich_from_mentions(session, score.restaurant_id)

    location = None
    if restaurant.latitude is not None and restaurant.longitude is not None:
        location = {"lat": restaurant.latitude, "lng": restaurant.longitude}

    return {
        "rank": rank,
        "restaurant_id": restaurant.id,
        "name": restaurant.name,
        "area": restaurant.area,
        "address": restaurant.address or enriched["address_text"],
        "location": location,
        "cuisine": restaurant.cuisine,
        "avg_price": restaurant.avg_price,
        "score": score.score,
        "praise_keywords": enriched["praise_keywords"],
        "complaints": enriched["complaints"],
        "recommended_dishes": enriched["recommended_dishes"],
        "mention_count": score.mention_count,
        "last_mentioned_at": enriched["last_mentioned_at"],
        "sources": enriched["sources"],
    }


def build_restaurant_detail(
    session: Session,
    restaurant_id: int,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict | None:
    """店铺详情（文档 9.2 detail / 8.1）。restaurant 不存在 → None。"""
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        return None

    score = score_one_restaurant(session, restaurant_id, settings=settings, now=now)
    enriched = _enrich_from_mentions(session, restaurant_id, limit=MAX_DETAIL_SOURCES)

    # 展示分优先取最新榜单快照的城市内分位分（与榜单页口径一致）；
    # 未上榜/无快照时回退实时质量分
    display_score = score.score if score else 0.0
    if restaurant.city is not None:
        snapshot = get_latest_snapshot(session, restaurant.city.name)
        if snapshot and snapshot.items:
            for item in snapshot.items:
                if item.get("restaurant_id") == restaurant.id:
                    display_score = item.get("display_score", item.get("score", display_score))
                    break

    location = None
    if restaurant.latitude is not None and restaurant.longitude is not None:
        location = {"lat": restaurant.latitude, "lng": restaurant.longitude}

    return {
        "restaurant_id": restaurant.id,
        "name": restaurant.name,
        "area": restaurant.area,
        "address": restaurant.address or enriched["address_text"],
        "location": location,
        "cuisine": restaurant.cuisine,
        "avg_price": restaurant.avg_price,
        "status": restaurant.status,
        "score": display_score,
        "exclude_reason": score.exclude_reason if score else None,
        "mention_count": enriched["mention_count"],
        "last_mentioned_at": enriched["last_mentioned_at"],
        "praise_keywords": enriched["praise_keywords"],
        "complaints": enriched["complaints"],
        "recommended_dishes": enriched["recommended_dishes"],
        "sources": enriched["sources"],
    }


def build_restaurant_sources(
    session: Session, restaurant_id: int, *, limit: int = MAX_SOURCE_LIST
) -> list[dict] | None:
    """店铺来源引用列表（文档 8.1 /restaurants/{id}/sources）。不存在 → None。"""
    if session.get(Restaurant, restaurant_id) is None:
        return None
    return _collect_sources(_iter_mentions(session, restaurant_id), limit)


def build_rank_snapshot(
    session: Session,
    city_hint: str,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    top_n: int = DEFAULT_TOP_N,
    cache: RankCache | None = None,
) -> RankSnapshot:
    """生成并落库一条榜单快照（调用方负责 commit）。城市不存在时抛 LookupError。"""
    settings = settings or get_settings()
    now = to_utc(now) or datetime.now(timezone.utc)

    city = session.scalar(select(City).where(City.name == city_hint))
    if city is None:
        raise LookupError(f"城市不存在：{city_hint}")

    scores = collect_shop_scores(session, city_hint=city_hint, settings=settings, now=now)
    # 贝叶斯平均（文档 6.2 演进）：小样本店收敛到城市先验，替代线性置信压分
    apply_bayesian_smooth(scores, settings.score_bayes_prior)
    ranked = [s for s in scores if s.restaurant_id is not None and not s.excluded][:top_n]

    total = len(ranked)
    items = []
    for i, s in enumerate(ranked, start=1):
        item = _build_item(session, s, rank=i)
        # 城市内分位显示分（1.0–9.9）：跨城观感公平，Top1≈9.9、末位 1.0；
        # score 字段保留贝叶斯绝对分（排序与数据用途）
        if total > 1:
            pct = (total - i) / (total - 1)
        else:
            pct = 1.0
        item["display_score"] = round(1 + 8.9 * pct, 1)
        items.append(item)

    snapshot = RankSnapshot(
        city_id=city.id, generated_at=now, algorithm_ver=ALGORITHM_VER, items=items
    )
    session.add(snapshot)
    if items and city.status == "collecting":
        city.status = "active"
    session.flush()
    if cache is not None:
        cache.invalidate_city(city_hint)
    return snapshot


def get_latest_snapshot(session: Session, city_hint: str) -> RankSnapshot | None:
    return session.scalar(
        select(RankSnapshot)
        .join(City, RankSnapshot.city_id == City.id)
        .where(City.name == city_hint)
        .order_by(RankSnapshot.generated_at.desc(), RankSnapshot.id.desc())
    )


def _parse_item_date(value) -> datetime | None:
    """快照条目的 last_mentioned_at（YYYY-MM-DD）→ UTC datetime；解析失败返回 None。"""
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _apply_filters(
    items: list[dict],
    *,
    cuisine: str | None,
    price_min: float | None,
    price_max: float | None,
    area: str | None,
    days: int | None = None,
    now: datetime | None = None,
) -> list[dict]:
    result = items
    if cuisine:
        result = [i for i in result if i.get("cuisine") and cuisine in i["cuisine"]]
    if area:
        result = [i for i in result if i.get("area") == area]
    if price_min is not None:
        result = [
            i for i in result if i.get("avg_price") is not None and i["avg_price"] >= price_min
        ]
    if price_max is not None:
        result = [
            i for i in result if i.get("avg_price") is not None and i["avg_price"] <= price_max
        ]
    if days:
        # 时间维度（文档 2.2 V1.1）：仅保留快照生成时点前 N 天内被提及过的店，
        # 次序不变、名次重排。以快照生成时间为基准，保证结果确定、缓存一致。
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        result = [
            i
            for i in result
            if (t := _parse_item_date(i.get("last_mentioned_at"))) is not None and t >= cutoff
        ]
        result = [{**i, "rank": rank} for rank, i in enumerate(result, start=1)]
    return result


def get_rank(
    session: Session,
    city_hint: str,
    *,
    page: int = 1,
    page_size: int = 20,
    cuisine: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    area: str | None = None,
    days: int | None = None,
    cache: RankCache | None = None,
) -> dict | None:
    """读最新快照并分页；该城市无快照时返回 None。

    注意：筛选在快照 Top N 之上进行，故筛选后条数可能少于 Top N。
    days 传入正整数时启用时间维度（如 90 = 近 90 天），按最近提及时间过滤并重排名次。
    """
    if cache is not None and cache.enabled:
        cache_key = RankCache.key(
            city_hint, page, page_size, cuisine, price_min, price_max, area, days
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    else:
        cache_key = None

    snapshot = get_latest_snapshot(session, city_hint)
    if snapshot is None:
        return None

    items = _apply_filters(
        list(snapshot.items or []),
        cuisine=cuisine,
        price_min=price_min,
        price_max=price_max,
        area=area,
        days=days,
        now=to_utc(snapshot.generated_at),
    )
    total = len(items)
    start = max(0, (page - 1) * page_size)
    generated_at = to_utc(snapshot.generated_at)

    data = {
        "city": city_hint,
        "generated_at": generated_at.isoformat() if generated_at else None,
        "algorithm_ver": snapshot.algorithm_ver,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items[start : start + page_size],
    }
    if cache_key is not None:
        cache.set(cache_key, data)
    return data