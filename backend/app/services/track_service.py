"""行为埋点（方向六「榜单可带走」2026-10-23 设计方案批 1）。

北极星「榜单带走率」数据源：榜单页 UV 中发生 ≥1 次带走事件的会话占比。
免登录可写，限流按 IP 哈希（与 feedback 同口径），不落原始 IP；
动作白名单由本模块定义，客户端只上报已知动作（防脏数据膨胀）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..db.models import TrackEvent

# 浏览类动作（UV 分母）：榜单页曝光
VIEW_ACTIONS = ("rank_view",)
# 带走类动作（UV 分子）：把榜单内容带离站内的行为。
# pack_save 批 2 追加（服务端订阅成功时落，见 city_pack 路由）；
# 批 3 将追加 trip_save / trip_check，口径见设计方案 2.1。
TAKEAWAY_ACTIONS = (
    "share_poster_shop",
    "share_poster_rank",
    "copy_link_rank",
    "copy_link_shop",
    "pack_save",
)
TRACK_ACTIONS = VIEW_ACTIONS + TAKEAWAY_ACTIONS

_SESSION_KEY_MAX = 64
_CITY_MAX = 64


class TrackRateLimited(Exception):
    """同一 IP 在限流窗口内上报次数超限。"""


def _clamp(value: str | None, limit: int) -> str | None:
    """客户端自由文本字段统一截断（session_key/city），不留异常长脏数据。"""
    if not value:
        return None
    return value.strip()[:limit] or None


def record_track_event(
    session: Session,
    *,
    action: str,
    settings: Settings,
    session_key: str | None = None,
    city: str | None = None,
    restaurant_id: int | None = None,
    user_id: int | None = None,
    ip_hash: str | None = None,
    now: datetime | None = None,
) -> TrackEvent:
    """落一条埋点事件（调用方负责 commit）。

    action 不在白名单抛 ValueError；同 IP 超限抛 TrackRateLimited。
    """
    if action not in TRACK_ACTIONS:
        raise ValueError(f"未知埋点动作：{action}")
    now = now or datetime.now(timezone.utc)
    if ip_hash:
        since = now - timedelta(minutes=settings.track_rate_limit_window_minutes)
        recent = (
            session.scalar(
                select(func.count(TrackEvent.id)).where(
                    TrackEvent.ip_hash == ip_hash,
                    TrackEvent.created_at >= since,
                )
            )
            or 0
        )
        if recent >= settings.track_rate_limit_max:
            raise TrackRateLimited()
    row = TrackEvent(
        user_id=user_id,
        session_key=_clamp(session_key, _SESSION_KEY_MAX),
        action=action,
        city=_clamp(city, _CITY_MAX),
        restaurant_id=restaurant_id,
        ip_hash=ip_hash,
        created_at=now,
    )
    session.add(row)
    session.flush()
    return row


def track_daily_summary(
    session: Session, *, now: datetime | None = None, days: int = 14
) -> dict:
    """带走事件日汇总（admin 侧）。

    分桶口径与 build_admin_stats 一致：UTC 日期。UV 按 session_key 去重；
    session_key 缺失的事件各自独立计数（无法归并会话，宁高勿丢）。
    带走率 = 带走 UV / 榜单页 UV（当日，4 位小数；分母 0 时为 None）。
    """
    now = now or datetime.now(timezone.utc)
    days = max(1, min(90, days))
    cutoff = now - timedelta(days=days)
    rows = session.execute(
        select(TrackEvent.created_at, TrackEvent.action, TrackEvent.session_key).where(
            TrackEvent.created_at >= cutoff
        )
    ).all()

    base = now.date()
    day_keys = [
        (base - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)
    ]
    view_sessions: dict[str, set[str]] = {d: set() for d in day_keys}
    takeaway_sessions: dict[str, set[str]] = {d: set() for d in day_keys}
    action_counts: dict[str, int] = {}
    for created_at, action, session_key in rows:
        if created_at is None:
            continue
        key = created_at.date().isoformat()
        if key not in day_keys:
            continue
        # session_key 为空时按事件独立计数，避免整段会话被静默丢出 UV
        sid = session_key or f"e{action_counts.get(action, 0)}-{created_at.isoformat()}"
        action_counts[action] = action_counts.get(action, 0) + 1
        if action in VIEW_ACTIONS:
            view_sessions[key].add(sid)
        elif action in TAKEAWAY_ACTIONS:
            takeaway_sessions[key].add(sid)

    days_out = []
    total_view: set[str] = set()
    total_takeaway: set[str] = set()
    for d in day_keys:
        v, t = view_sessions[d], takeaway_sessions[d]
        total_view |= v
        total_takeaway |= t
        days_out.append(
            {
                "date": d,
                "rank_view_uv": len(v),
                "takeaway_uv": len(t),
                "takeaway_rate": round(len(t) / len(v), 4) if v else None,
            }
        )
    return {
        "window_days": days,
        "days": days_out,
        "actions": [
            {"action": a, "count": c}
            for a, c in sorted(action_counts.items(), key=lambda kv: -kv[1])
        ],
        "totals": {
            "rank_view_uv": len(total_view),
            "takeaway_uv": len(total_takeaway),
        },
    }
