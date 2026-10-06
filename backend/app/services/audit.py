"""管理后台审计日志（文档 9.5）：所有写操作记录操作人/时间/目标/前后值。

合规追溯要求；ip_hash 复用纠错模块的加盐哈希，不落原始 IP。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AdminAuditLog


def write_audit(
    session: Session,
    *,
    operator: str,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip_hash: str | None = None,
    now: datetime | None = None,
) -> AdminAuditLog:
    """落一条审计记录（调用方负责 commit，与业务写操作同事务）。"""
    row = AdminAuditLog(
        operator=operator,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        before=before,
        after=after,
        ip_hash=ip_hash,
        created_at=now or datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def list_audit(
    session: Session, *, limit: int = 50, action: str | None = None
) -> list[AdminAuditLog]:
    """最近审计记录（倒序），可按 action 过滤。"""
    stmt = select(AdminAuditLog).order_by(AdminAuditLog.id.desc())
    if action:
        stmt = stmt.where(AdminAuditLog.action == action)
    return list(session.scalars(stmt.limit(min(200, max(1, limit)))).all())