"""行前清单（方向六批 3）：勾店/核销一体，一城一清单按 city 聚合不建清单头。

- 勾店 = 写入 planned；重复勾店幂等返回 (已有条目, False)
- 核销 = planned→visited（visited_at 取核销时刻）；已是 visited 幂等返回 False
- 店铺不存在/清单中没有该店抛 LookupError → 路由层转 404
- 核销后客户端引导发打卡：approved 打卡是存活证据（见 ugc_service.review_ugc），
  服务端在勾店/核销时分别落 trip_save/trip_check 带走事件（路由层，见 main.py）
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..db.models import Restaurant, TripItem, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def add_trip_item(
    session: Session, user_id: int, restaurant_id: int
) -> tuple[TripItem, bool]:
    """勾店入清单；返回 (item, 是否新建)。店铺不存在抛 LookupError。"""
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise LookupError("店铺不存在")
    existing = session.scalar(
        select(TripItem).where(
            TripItem.user_id == user_id, TripItem.restaurant_id == restaurant_id
        )
    )
    if existing is not None:
        return existing, False
    item = TripItem(user_id=user_id, restaurant_id=restaurant_id)
    session.add(item)
    session.commit()
    return item, True


def visit_trip_item(
    session: Session, user_id: int, restaurant_id: int
) -> tuple[TripItem, bool]:
    """旅途核销：planned→visited；已是 visited 幂等返回 (item, False)。"""
    item = session.scalar(
        select(TripItem).where(
            TripItem.user_id == user_id, TripItem.restaurant_id == restaurant_id
        )
    )
    if item is None:
        raise LookupError("清单中没有这家店")
    if item.status == "visited":
        return item, False
    item.status = "visited"
    item.visited_at = _utcnow()
    session.commit()
    return item, True


def remove_trip_item(session: Session, user_id: int, restaurant_id: int) -> bool:
    """把店移出清单；返回是否确实移除了条目。"""
    item = session.scalar(
        select(TripItem).where(
            TripItem.user_id == user_id, TripItem.restaurant_id == restaurant_id
        )
    )
    if item is None:
        return False
    session.delete(item)
    session.commit()
    return True


def get_trip_item(
    session: Session, user_id: int, restaurant_id: int
) -> TripItem | None:
    """详情页勾店按钮态查询。"""
    return session.scalar(
        select(TripItem).where(
            TripItem.user_id == user_id, TripItem.restaurant_id == restaurant_id
        )
    )


def list_trip_items(session: Session, user: User) -> list[dict]:
    """行前清单（勾店时间倒序），附榜单同源店铺摘要与状态；按城分组由客户端做。"""
    rows = (
        session.scalars(
            select(TripItem)
            .options(joinedload(TripItem.restaurant).joinedload(Restaurant.city))
            .where(TripItem.user_id == user.id)
            .order_by(TripItem.created_at.desc(), TripItem.id.desc())
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
                "status": row.status,
                "saved_at": row.created_at.isoformat() if row.created_at else None,
                "visited_at": row.visited_at.isoformat() if row.visited_at else None,
            }
        )
    return items
