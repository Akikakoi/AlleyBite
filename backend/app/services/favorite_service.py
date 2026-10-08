"""收藏（文档 8.1 favorites / 12 章 V2.0）：用户态写操作，浏览与搜索仍免登录。

- 幂等收藏：重复收藏同一店铺返回 (已有条目, False)，不报错
- 店铺不存在抛 LookupError → 路由层转 404
- 列表按收藏时间倒序，附带榜单同源的店铺摘要字段
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..db.models import Favorite, Restaurant, User


def add_favorite(
    session: Session, user_id: int, restaurant_id: int
) -> tuple[Favorite, bool]:
    """收藏店铺；返回 (favorite, 是否新建)。店铺不存在抛 LookupError。"""
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise LookupError("店铺不存在")
    existing = session.scalar(
        select(Favorite).where(
            Favorite.user_id == user_id, Favorite.restaurant_id == restaurant_id
        )
    )
    if existing is not None:
        return existing, False
    favorite = Favorite(user_id=user_id, restaurant_id=restaurant_id)
    session.add(favorite)
    session.commit()
    return favorite, True


def remove_favorite(session: Session, user_id: int, restaurant_id: int) -> bool:
    """取消收藏；返回是否确实移除了条目。"""
    favorite = session.scalar(
        select(Favorite).where(
            Favorite.user_id == user_id, Favorite.restaurant_id == restaurant_id
        )
    )
    if favorite is None:
        return False
    session.delete(favorite)
    session.commit()
    return True


def is_favorite(session: Session, user_id: int, restaurant_id: int) -> bool:
    """该用户是否已收藏该店铺（详情页收藏态）。"""
    return (
        session.scalar(
            select(Favorite.id).where(
                Favorite.user_id == user_id, Favorite.restaurant_id == restaurant_id
            )
        )
        is not None
    )


def _last_mentioned_at(restaurant: Restaurant) -> str | None:
    """最近一次被提及的日期（YYYY-MM-DD），与榜单口径一致。"""
    stamps = [m.created_at for m in restaurant.mentions if m.created_at]
    return max(stamps).date().isoformat() if stamps else None


def list_favorites(session: Session, user: User) -> list[dict]:
    """我的收藏列表（按收藏时间倒序），附店铺摘要。"""
    rows = (
        session.scalars(
            select(Favorite)
            .options(joinedload(Favorite.restaurant).joinedload(Restaurant.city))
            .where(Favorite.user_id == user.id)
            .order_by(Favorite.created_at.desc(), Favorite.id.desc())
        )
        .unique()
        .all()
    )
    items: list[dict] = []
    for row in rows:
        r = row.restaurant
        items.append(
            {
                "restaurant_id": r.id,
                "name": r.name,
                "city": r.city.name if r.city else None,
                "area": r.area,
                "cuisine": r.cuisine,
                "avg_price": r.avg_price,
                "address": r.address,
                "mention_count": len(r.mentions),
                "last_mentioned_at": _last_mentioned_at(r),
                "favorited_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return items
