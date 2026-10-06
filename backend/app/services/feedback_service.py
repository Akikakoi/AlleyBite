"""店铺纠错/举报（文档 9.3 纠错入口 / 9.5 反馈处理 / 14 章合规验收）。

免登录提交，改以 IP 哈希做限流与滥用追溯：不落原始 IP，符合"无个人信息入库"。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..db.models import Feedback, Restaurant

FEEDBACK_TYPES = ("info", "closed", "label", "other")
DEFAULT_FEEDBACK_TYPE = "info"


class FeedbackRateLimited(Exception):
    """同一 IP 在限流窗口内提交次数超限。"""


def hash_ip(ip: str | None, *, settings: Settings) -> str | None:
    """加盐 SHA-256；ip 为空（如测试客户端）时返回 None。"""
    if not ip:
        return None
    salted = f"{settings.feedback_ip_salt}:{ip}".encode("utf-8")
    return hashlib.sha256(salted).hexdigest()


def recent_feedback_count(
    session: Session,
    ip_hash: str | None,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """窗口内该 IP 已提交条数（ip_hash 为空视为无限流）。"""
    if not ip_hash:
        return 0
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(minutes=settings.feedback_rate_limit_window_minutes)
    return (
        session.scalar(
            select(func.count(Feedback.id)).where(
                Feedback.ip_hash == ip_hash,
                Feedback.created_at >= since,
            )
        )
        or 0
    )


def create_feedback(
    session: Session,
    *,
    content: str,
    settings: Settings,
    restaurant_id: int | None = None,
    type: str = DEFAULT_FEEDBACK_TYPE,
    contact: str | None = None,
    ip_hash: str | None = None,
    now: datetime | None = None,
) -> Feedback:
    """落一条工单（调用方负责 commit）。

    校验失败抛 ValueError；店铺不存在抛 LookupError；超限抛 FeedbackRateLimited。
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("纠错内容不能为空")
    if len(content) > settings.feedback_content_max_len:
        raise ValueError(f"纠错内容不能超过 {settings.feedback_content_max_len} 字")
    if type not in FEEDBACK_TYPES:
        type = "other"
    if restaurant_id is not None and session.get(Restaurant, restaurant_id) is None:
        raise LookupError(f"店铺不存在：{restaurant_id}")
    if (
        recent_feedback_count(session, ip_hash, settings=settings, now=now)
        >= settings.feedback_rate_limit_max
    ):
        raise FeedbackRateLimited()

    row = Feedback(
        restaurant_id=restaurant_id,
        type=type,
        content=content,
        contact=(contact or "").strip() or None,
        status="pending",
        ip_hash=ip_hash,
        created_at=now or datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def list_feedback(
    session: Session, *, limit: int = 50, status: str | None = None
) -> list[Feedback]:
    """运营侧工单列表（文档 9.5 反馈处理，后台就绪前由脚本/接口兜底）。"""
    stmt = select(Feedback).order_by(Feedback.id.desc())
    if status:
        stmt = stmt.where(Feedback.status == status)
    return list(session.scalars(stmt.limit(min(200, max(1, limit)))).all())