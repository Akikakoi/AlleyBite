"""个性化推荐（文档 2.2 V2.0：基于用户位置与口味标签）。

V1 口径（无需向量化，纯库内聚合）：
- 登录用户：从「收藏 + 近期浏览」提取偏好城市与菜系，召回同城市/同菜系的
  高口碑店铺（mention 数达标），排除已收藏与最近浏览过的
- 未登录：回退全局热门（多城最新快照的前列店铺）
结果按 mention_count 与最近提及时间粗排，客户端展示为首页「猜你想吃」。
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    City,
    Favorite,
    RankSnapshot,
    Restaurant,
    ViewEvent,
)


def record_view(session: Session, restaurant_id: int, user_id: int | None) -> None:
    """记录详情页浏览事件（匿名可记，user_id 可空）。"""
    session.add(ViewEvent(user_id=user_id, restaurant_id=restaurant_id))
    session.commit()


def _snapshot_top_items(session: Session, city_name: str, limit: int) -> list[dict]:
    """取城市最新榜单快照前 N 条（榜单条目即已富化的店铺信息）。"""
    snapshot = session.scalar(
        select(RankSnapshot)
        .join(City, City.id == RankSnapshot.city_id)
        .where(City.name == city_name)
        .order_by(RankSnapshot.id.desc())
        .limit(1)
    )
    if snapshot is None or not snapshot.items:
        return []
    out = []
    for item in snapshot.items[:limit]:
        if item.get("restaurant_id") and item.get("mention_count", 0) > 0:
            out.append(item)
    return out


def recommend_for_user(
    session: Session,
    user_id: int | None,
    *,
    limit: int = 6,
) -> dict:
    """推荐结果：{'strategy': ..., 'items': [{restaurant_id, name, city, reason}]}。

    策略优先级：口味召回（同城同菜系）> 城市召回（偏好城市热门）> 全局热门。
    """
    limit = max(1, min(10, limit))
    fav_ids: list[int] = []
    viewed: list[tuple[int, str]] = []  # (restaurant_id, viewed_at_iso)
    if user_id is not None:
        fav_ids = list(
            session.scalars(
                select(Favorite.restaurant_id).where(Favorite.user_id == user_id)
            ).all()
        )
        rows = session.scalars(
            select(ViewEvent)
            .where(ViewEvent.user_id == user_id)
            .order_by(ViewEvent.id.desc())
            .limit(20)
        ).all()
        viewed = [(r.restaurant_id, r.created_at.isoformat() if r.created_at else "") for r in rows]
    exclude_ids = set(fav_ids) | {rid for rid, _ in viewed}

    def _pack(items: list[dict], strategy: str) -> dict:
        packed = []
        for item in items:
            rid = item.get("restaurant_id")
            if rid in exclude_ids:
                continue
            packed.append(
                {
                    "restaurant_id": rid,
                    "name": item.get("name"),
                    "city": item.get("city"),
                    "cuisine": item.get("cuisine"),
                    "score": item.get("score"),
                    "mention_count": item.get("mention_count", 0),
                }
            )
            if len(packed) >= limit:
                break
        return {"strategy": strategy, "items": packed}

    if user_id is not None and (fav_ids or viewed):
        # 偏好城市与菜系：收藏权重高，浏览次之
        seed_ids = fav_ids + [rid for rid, _ in viewed]
        restaurants = {
            r.id: r
            for r in session.scalars(
                select(Restaurant).where(Restaurant.id.in_(seed_ids))
            ).all()
        }
        city_counter: Counter[str] = Counter()
        cuisine_counter: Counter[str] = Counter()
        for rid in fav_ids:
            r = restaurants.get(rid)
            if r and r.city:
                city_counter[r.city.name] += 2
            if r and r.cuisine:
                cuisine_counter[r.cuisine] += 2
        for rid, _ in viewed:
            r = restaurants.get(rid)
            if r and r.city:
                city_counter[r.city.name] += 1
            if r and r.cuisine:
                cuisine_counter[r.cuisine] += 1

        items: list[dict] = []
        seen: set[int] = set()
        # 1) 口味召回：偏好城市 × 偏好菜系的榜单店铺
        for city_name, _ in city_counter.most_common(3):
            for cuisine, _ in cuisine_counter.most_common(3):
                for item in _snapshot_top_items(session, city_name, 12):
                    rid = item.get("restaurant_id")
                    if rid in seen or item.get("cuisine") != cuisine:
                        continue
                    seen.add(rid)
                    items.append(item)
            for item in _snapshot_top_items(session, city_name, 12):
                rid = item.get("restaurant_id")
                if rid in seen:
                    continue
                seen.add(rid)
                items.append(item)
            if len(items) >= limit * 2:
                break
        packed = _pack(items, "taste")
        if packed["items"]:
            return packed

    # 2) 全局热门：多城最新快照轮转取前列
    city_names = list(
        session.scalars(select(City.name)).all()
    )
    hot: list[dict] = []
    seen: set[int] = set()
    for city_name in city_names:
        for item in _snapshot_top_items(session, city_name, 3):
            rid = item.get("restaurant_id")
            if rid in seen:
                continue
            seen.add(rid)
            hot.append(item)
    return _pack(hot, "hot")
