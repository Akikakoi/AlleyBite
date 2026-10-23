"""按城收藏包（方向六批 2）：订阅制，服务端只存 unique(user_id, city) 关系。

- 打开包由客户端实时拉 GET /rank（快照制天然稳定、随重建更新），离线副本是
  客户端缓存行为，不是服务端的数据复制（设计方案 2.3）
- 订阅校验城市已收录（City 表存在），未收录抛 LookupError → 路由层转 404
- 重复订阅幂等返回 (已有条目, False)；列表附最新快照元信息供摘要展示
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import City, CityPack, User
from .rank_service import city_maturity, get_latest_snapshot
from .scoring import to_utc


def subscribe_city_pack(
    session: Session, user_id: int, city: str
) -> tuple[CityPack, bool]:
    """收藏整城；返回 (pack, 是否新建)。城市未收录抛 LookupError。"""
    name = (city or "").strip()
    if not name or session.scalar(select(City.id).where(City.name == name)) is None:
        raise LookupError("城市未收录")
    existing = session.scalar(
        select(CityPack).where(CityPack.user_id == user_id, CityPack.city == name)
    )
    if existing is not None:
        return existing, False
    pack = CityPack(user_id=user_id, city=name)
    session.add(pack)
    session.commit()
    return pack, True


def unsubscribe_city_pack(session: Session, user_id: int, city: str) -> bool:
    """退订收藏包；未订阅时幂等返回 False。"""
    pack = session.scalar(
        select(CityPack).where(
            CityPack.user_id == user_id, CityPack.city == (city or "").strip()
        )
    )
    if pack is None:
        return False
    session.delete(pack)
    session.commit()
    return True


def is_city_packed(session: Session, user_id: int, city: str) -> bool:
    """该用户是否已收藏该城（榜单页按钮态）。"""
    return (
        session.scalar(
            select(CityPack.id).where(
                CityPack.user_id == user_id, CityPack.city == (city or "").strip()
            )
        )
        is not None
    )


def list_city_packs(session: Session, user: User) -> list[dict]:
    """收藏包列表（订阅时间倒序），附最新快照摘要；打开包时客户端再实时拉全量。

    无快照城市（collecting）如实给 rank_count=0 / maturity=collecting。
    """
    rows = session.scalars(
        select(CityPack)
        .where(CityPack.user_id == user.id)
        .order_by(CityPack.created_at.desc(), CityPack.id.desc())
    ).all()
    items: list[dict] = []
    for row in rows:
        snapshot = get_latest_snapshot(session, row.city)
        generated = to_utc(snapshot.generated_at) if snapshot else None
        rank_count = len(snapshot.items or []) if snapshot else 0
        items.append(
            {
                "city": row.city,
                "rank_count": rank_count,
                "maturity": city_maturity(rank_count),
                "generated_at": generated.isoformat() if generated else None,
                "packed_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return items
