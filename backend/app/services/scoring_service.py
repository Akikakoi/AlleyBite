"""从 mention 聚合店铺并打分（文档 6.4 的打分步骤）。

实体对齐（5.5）落地后，以 **restaurant_id** 作为店铺实体键，规范名 / 城市 / 人均
取自 restaurant 实体；尚未对齐（restaurant_id 为空）的 mention 按归一店名兜底聚合，
保证对齐前仍可出分。
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..core.config import Settings, get_settings
from ..db.models import Mention as MentionRow
from ..db.models import RawContent, Restaurant
from .scoring import (
    MentionFact,
    ShopScore,
    aggregate_shop,
    normalize_shop_name,
    score_shop,
    to_utc,
)


def _fact_from_row(row: MentionRow) -> MentionFact:
    content = row.raw_content
    at = None
    if content is not None:
        at = content.published_at or content.crawled_at
    at = at or row.created_at

    return MentionFact(
        content_id=row.raw_content_id,
        platform=content.source if content else "unknown",
        source_url=content.source_url if content else None,
        city_hint=content.city_hint if content else None,
        author_city=row.author_city,
        at=to_utc(at),
        low_trust=bool(content.low_trust) if content else False,
        shop_name_raw=row.shop_name_raw,
        sentiment=row.sentiment or "neutral",
        is_recommendation=bool(row.is_recommendation),
        praise_keywords=tuple(row.praise_keywords or []),
        complaints=tuple(row.complaints or []),
        evidence_span=row.evidence_span,
    )


def collect_shop_scores(
    session: Session,
    *,
    city_hint: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[ShopScore]:
    """聚合 mention → 计算得分 → 按 score 降序返回（被剔除的排最后）。

    已对齐的 mention 按 restaurant_id 聚合（shop_key=restaurant:{id}）；
    未对齐的按归一店名兜底（shop_key=name:{norm}）。
    """
    settings = settings or get_settings()
    now = to_utc(now) or datetime.now(timezone.utc)

    stmt = (
        select(MentionRow)
        .options(
            joinedload(MentionRow.raw_content),
            joinedload(MentionRow.restaurant).joinedload(Restaurant.city),
        )
        .order_by(MentionRow.id)
    )
    if city_hint:
        stmt = stmt.where(MentionRow.raw_content.has(RawContent.city_hint == city_hint))

    aligned: dict[int, list[MentionFact]] = {}
    aligned_restaurants: dict[int, Restaurant] = {}
    aligned_address: dict[int, str] = {}
    unaligned: dict[str, list[MentionFact]] = {}
    for row in session.scalars(stmt).all():
        fact = _fact_from_row(row)
        if row.restaurant is not None:
            if row.restaurant.status != "active":  # 已合并/屏蔽的店不计入（文档 6.4 第 1 步）
                continue
            aligned.setdefault(row.restaurant_id, []).append(fact)
            aligned_restaurants[row.restaurant_id] = row.restaurant
            # restaurant.address 多为空，地址实际落在 mention 上；取其作商场店判定依据
            if not aligned_address.get(row.restaurant_id):
                addr = (row.address_text or row.area or "").strip()
                if addr:
                    aligned_address[row.restaurant_id] = addr
        else:
            unaligned.setdefault(normalize_shop_name(row.shop_name_raw), []).append(fact)

    scores: list[ShopScore] = []
    for restaurant_id, facts in aligned.items():
        restaurant = aligned_restaurants[restaurant_id]
        signals = aggregate_shop(
            f"restaurant:{restaurant_id}",
            facts,
            settings,
            avg_price=restaurant.avg_price,
            display_name=restaurant.name,
            city_hint=restaurant.city.name if restaurant.city else None,
            address=restaurant.address or aligned_address.get(restaurant_id) or None,
        )
        result = score_shop(signals, settings=settings, now=now)
        result.restaurant_id = restaurant_id
        scores.append(result)

    for norm, facts in unaligned.items():
        scores.append(
            score_shop(aggregate_shop(f"name:{norm}", facts, settings), settings=settings, now=now)
        )

    scores.sort(key=lambda s: (s.excluded, -s.score, s.shop_name))
    for index, item in enumerate(scores, start=1):
        item.rank = index
    return scores


def score_one_restaurant(
    session: Session,
    restaurant_id: int,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ShopScore | None:
    """对单个 restaurant 聚合其 mention 并打分；restaurant 不存在 → None。

    与 collect_shop_scores 不同：不跳过非 active 店铺，是否对外可见由调用方决定。
    """
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        return None

    settings = settings or get_settings()
    now = to_utc(now) or datetime.now(timezone.utc)

    rows = session.scalars(
        select(MentionRow)
        .options(joinedload(MentionRow.raw_content))
        .where(MentionRow.restaurant_id == restaurant_id)
        .order_by(MentionRow.id)
    ).all()

    address = restaurant.address
    if not address:
        for row in rows:
            addr = (row.address_text or row.area or "").strip()
            if addr:
                address = addr
                break

    signals = aggregate_shop(
        f"restaurant:{restaurant_id}",
        [_fact_from_row(row) for row in rows],
        settings,
        avg_price=restaurant.avg_price,
        display_name=restaurant.name,
        city_hint=restaurant.city.name if restaurant.city else None,
        address=address,
    )
    result = score_shop(signals, settings=settings, now=now)
    result.restaurant_id = restaurant_id
    return result