"""实体对齐与去重（文档 5.5）。

流程：
    名称归一化 →（别名 / 名称命中 → 直接归并）→ 相似度综合分 → 判定
      ≥ match 阈值     ：挂靠到现有 restaurant_id
      [review, match)：进 alignment_review 人工审核队列
      < review 阈值    ：新建 restaurant

说明：地理约束（以地图 POI 为锚点）待地图开放 API 接入后补充；mention 未落 area，
      故综合分暂只用「名称相似度 + 地址 token 重合度」。
"""

from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..core.config import Settings, get_settings
from ..db.models import (
    AlignmentReview,
    City,
    Mention as MentionRow,
    RawContent,
    Restaurant,
    ShopAlias,
)
from .normalize import address_similarity, name_similarity, normalize_shop_name


class AlignmentRunResult(BaseModel):
    cities: int = 0
    restaurants_created: int = 0
    mentions_aligned: int = 0
    mentions_review: int = 0
    aliases_added: int = 0
    skipped: int = 0  # 无城市线索或已在审核队列


def get_or_create_city(session: Session, name: str) -> City:
    city = session.scalar(select(City).where(City.name == name))
    if city is None:
        city = City(name=name)
        session.add(city)
        session.flush()
    return city


def composite_similarity(
    name_norm: str, address: str | None, restaurant: Restaurant, settings: Settings
) -> float:
    """名称 + 地址的加权综合相似度（缺失项按权重归一，逐项得分见函数返回）。"""
    components: list[tuple[float, float]] = [
        (settings.align_name_weight, name_similarity(name_norm, restaurant.name_norm))
    ]
    addr_sim = address_similarity(address, restaurant.address)
    if addr_sim is not None:
        components.append((settings.align_address_weight, addr_sim))

    total_weight = sum(weight for weight, _ in components) or 1.0
    return sum(weight * value for weight, value in components) / total_weight


def _add_alias(
    restaurant: Restaurant,
    raw_name: str,
    norm: str,
    alias_map: dict[str, int],
) -> bool:
    if not norm or norm == restaurant.name_norm or norm in alias_map:
        return False
    # 同一店铺已有相同规范化别名时跳过（人工确认同店多条 mention 时会重复触发）
    if any(a.alias_norm == norm for a in restaurant.aliases):
        return False
    # 走关系集合追加：既落库（cascade）又保持内存中的 aliases 集合同步
    restaurant.aliases.append(
        ShopAlias(restaurant_id=restaurant.id, alias=raw_name, alias_norm=norm)
    )
    alias_map[norm] = restaurant.id
    return True


def _backfill_restaurant(restaurant: Restaurant, mention: MentionRow) -> None:
    """用 mention 填充 restaurant 缺失的地址/区域/菜系/人均（已有值不覆盖）。"""
    if restaurant.address is None and mention.address_text:
        restaurant.address = mention.address_text
    if restaurant.area is None and mention.area:
        restaurant.area = mention.area
    if restaurant.cuisine is None and mention.cuisine:
        restaurant.cuisine = mention.cuisine
    if restaurant.avg_price is None and mention.avg_price is not None:
        restaurant.avg_price = mention.avg_price


def align_mentions(
    session: Session,
    *,
    city_hint: str | None = None,
    settings: Settings | None = None,
) -> AlignmentRunResult:
    """对尚未归并（restaurant_id 为空）的 mention 执行实体对齐。调用方负责 commit。"""
    settings = settings or get_settings()

    stmt = (
        select(MentionRow)
        .options(joinedload(MentionRow.raw_content))
        .where(MentionRow.restaurant_id.is_(None))
        .order_by(MentionRow.id)
    )
    if city_hint:
        stmt = stmt.where(MentionRow.raw_content.has(RawContent.city_hint == city_hint))

    # 一个 mention 只允许一条审核记录（uq_alignment_review_mention）：
    # pending 已在队列、rejected 人工已否决、confirmed 已归并，均不再重复入队
    reviewed_mention_ids = set(
        session.scalars(select(AlignmentReview.mention_id)).all()
    )

    result = AlignmentRunResult()
    by_city: dict[str, list[MentionRow]] = {}
    for row in session.scalars(stmt).all():
        city_name = row.raw_content.city_hint if row.raw_content else None
        if not city_name:
            result.skipped += 1
            continue
        by_city.setdefault(city_name, []).append(row)

    result.cities = len(by_city)

    for city_name, mentions in by_city.items():
        city = get_or_create_city(session, city_name)
        restaurants = list(
            session.scalars(
                select(Restaurant)
                .options(selectinload(Restaurant.aliases))
                .where(Restaurant.city_id == city.id, Restaurant.status == "active")
            ).all()
        )
        alias_map = {a.alias_norm: a.restaurant_id for r in restaurants for a in r.aliases}
        name_map = {r.name_norm: r.id for r in restaurants}
        by_id = {r.id: r for r in restaurants}

        for mention in mentions:
            if mention.id in reviewed_mention_ids:
                result.skipped += 1
                continue

            raw_name = mention.shop_name_raw
            norm = normalize_shop_name(raw_name)
            address = mention.address_text

            if norm in alias_map:
                mention.restaurant_id = alias_map[norm]
                _backfill_restaurant(by_id[alias_map[norm]], mention)
                result.mentions_aligned += 1
                continue
            if norm in name_map:
                mention.restaurant_id = name_map[norm]
                _backfill_restaurant(by_id[name_map[norm]], mention)
                result.mentions_aligned += 1
                continue

            best_restaurant: Restaurant | None = None
            best_score = 0.0
            for restaurant in restaurants:
                score = composite_similarity(norm, address, restaurant, settings)
                if score > best_score:
                    best_score, best_restaurant = score, restaurant

            if best_restaurant is not None and best_score >= settings.align_match_threshold:
                mention.restaurant_id = best_restaurant.id
                _backfill_restaurant(best_restaurant, mention)
                result.mentions_aligned += 1
                if _add_alias(best_restaurant, raw_name, norm, alias_map):
                    result.aliases_added += 1
            elif best_restaurant is not None and best_score >= settings.align_review_threshold:
                session.add(
                    AlignmentReview(
                        mention_id=mention.id,
                        candidate_restaurant_id=best_restaurant.id,
                        score=round(best_score, 4),
                    )
                )
                result.mentions_review += 1
            else:
                restaurant = Restaurant(
                    city_id=city.id,
                    name=raw_name,
                    name_norm=norm,
                    address=address,
                    area=mention.area,
                    cuisine=mention.cuisine,
                    avg_price=mention.avg_price,
                )
                session.add(restaurant)
                session.flush()
                mention.restaurant_id = restaurant.id
                restaurants.append(restaurant)
                by_id[restaurant.id] = restaurant
                name_map[norm] = restaurant.id
                result.restaurants_created += 1
                result.mentions_aligned += 1

    session.flush()
    return result


def list_pending_reviews(session: Session) -> list[AlignmentReview]:
    return list(
        session.scalars(
            select(AlignmentReview)
            .where(AlignmentReview.status == "pending")
            .order_by(AlignmentReview.id)
        ).all()
    )


def confirm_review(
    session: Session, review_id: int, restaurant_id: int | None = None
) -> AlignmentReview:
    """人工确认灰区 mention 的归并；可指定目标 restaurant，否则用候选。"""
    review = session.get(AlignmentReview, review_id)
    if review is None:
        raise LookupError(f"alignment_review#{review_id} 不存在")

    target_id = restaurant_id or review.candidate_restaurant_id
    if target_id is None:
        raise ValueError("未指定目标 restaurant，且该审核无候选")

    mention = session.get(MentionRow, review.mention_id)
    restaurant = session.get(Restaurant, target_id)
    if mention is None or restaurant is None:
        raise LookupError("mention 或 restaurant 不存在")

    mention.restaurant_id = target_id
    review.status = "confirmed"
    _add_alias(
        restaurant,
        mention.shop_name_raw,
        normalize_shop_name(mention.shop_name_raw),
        {},
    )
    session.flush()
    return review


def reject_review(session: Session, review_id: int) -> AlignmentReview:
    """人工驳回灰区归并：保持 mention 未归并，避免后续重复进队列。"""
    review = session.get(AlignmentReview, review_id)
    if review is None:
        raise LookupError(f"alignment_review#{review_id} 不存在")
    review.status = "rejected"
    session.flush()
    return review


def merge_restaurants(
    session: Session, source_id: int, target_id: int
) -> tuple[Restaurant, Restaurant, int]:
    """把 source 店铺合并进 target（文档 9.5 店铺合并）。

    处理：mention 改挂、别名迁移、缺失字段回填；source 置 merged 并保留其名称为
    target 别名，使后续同名 mention 直接归并。返回 (source, target, 迁移的 mention 数)。
    """
    if source_id == target_id:
        raise ValueError("不能合并到自身")
    source = session.get(Restaurant, source_id)
    target = session.get(Restaurant, target_id)
    if source is None or target is None:
        raise LookupError("店铺不存在")
    if source.status == "merged":
        raise ValueError("该店铺已被合并")

    moved = 0
    for mention in session.scalars(
        select(MentionRow).where(MentionRow.restaurant_id == source.id)
    ).all():
        mention.restaurant_id = target.id
        moved += 1

    target_aliases = {a.alias_norm for a in target.aliases}
    for alias in list(source.aliases):
        if alias.alias_norm in target_aliases:
            session.delete(alias)
            continue
        # 走关系迁移：自动从 source.aliases 摘除并加入 target.aliases，
        # 避免 delete-orphan 误删与内存集合不一致
        alias.restaurant = target
        target_aliases.add(alias.alias_norm)
    session.flush()

    # source 原名称保留为 target 别名
    if _add_alias(target, source.name, source.name_norm, {}):
        target_aliases.add(source.name_norm)

    # 缺失字段回填（已有值不覆盖）
    if target.address is None and source.address:
        target.address = source.address
    if target.area is None and source.area:
        target.area = source.area
    if target.cuisine is None and source.cuisine:
        target.cuisine = source.cuisine
    if target.avg_price is None and source.avg_price is not None:
        target.avg_price = source.avg_price

    source.status = "merged"
    source.merged_into = target.id
    session.flush()
    return source, target, moved


def add_manual_alias(
    session: Session, restaurant_id: int, alias: str
) -> ShopAlias | None:
    """人工确认别名（文档 9.5）；已存在（同名/同 alias_norm）返回 None。"""
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise LookupError("店铺不存在")
    norm = normalize_shop_name(alias)
    if not norm:
        raise ValueError("别名不能为空")
    if norm == restaurant.name_norm:
        return None
    existing = session.scalar(
        select(ShopAlias).where(
            ShopAlias.restaurant_id == restaurant_id, ShopAlias.alias_norm == norm
        )
    )
    if existing is not None:
        return None
    row = ShopAlias(restaurant_id=restaurant_id, alias=alias.strip(), alias_norm=norm)
    session.add(row)
    session.flush()
    return row


def set_restaurant_status(
    session: Session, restaurant_id: int, status: str
) -> tuple[Restaurant, str]:
    """屏蔽/恢复店铺（文档 9.5）；仅允许 active ↔ blocked。返回 (店铺, 原状态)。"""
    if status not in ("active", "blocked"):
        raise ValueError("status 仅支持 active | blocked")
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise LookupError("店铺不存在")
    if restaurant.status == "merged":
        raise ValueError("已合并的店铺不可改状态")
    before = restaurant.status
    restaurant.status = status
    session.flush()
    return restaurant, before