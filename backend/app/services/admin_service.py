"""管理后台运营服务（文档 9.5）：店铺审核视图、采集监控汇总、反馈工单处理。

仅做查询/编排，写操作的审计由接口层统一记录（app/services/audit.py）。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    AlignmentReview,
    City,
    Feedback,
    JobRun,
    Mention as MentionRow,
    Restaurant,
)

FEEDBACK_STATUSES = ("pending", "processing", "resolved", "rejected")


def pending_reviews_detail(session: Session, *, limit: int = 100) -> list[dict]:
    """灰区审核队列（含 mention 原文与候选店铺名），供后台列表/详情展示。"""
    rows = session.scalars(
        select(AlignmentReview)
        .where(AlignmentReview.status == "pending")
        .order_by(AlignmentReview.id)
        .limit(min(200, max(1, limit)))
    ).all()
    out: list[dict] = []
    for review in rows:
        mention = session.get(MentionRow, review.mention_id)
        candidate = (
            session.get(Restaurant, review.candidate_restaurant_id)
            if review.candidate_restaurant_id
            else None
        )
        out.append(
            {
                "id": review.id,
                "mention_id": review.mention_id,
                "shop_name_raw": mention.shop_name_raw if mention else None,
                "address_text": mention.address_text if mention else None,
                "evidence_span": mention.evidence_span if mention else None,
                "score": review.score,
                "reason": review.reason,
                "candidate_restaurant_id": review.candidate_restaurant_id,
                "candidate_name": candidate.name if candidate else None,
                "city": (
                    candidate.city.name if candidate and candidate.city else None
                ),
            }
        )
    return out


def update_feedback_status(
    session: Session, feedback_id: int, status: str
) -> tuple[Feedback, str]:
    """更新工单状态（文档 9.5 反馈处理）；返回 (工单, 原状态)。"""
    if status not in FEEDBACK_STATUSES:
        raise ValueError(f"status 仅支持 {' | '.join(FEEDBACK_STATUSES)}")
    row = session.get(Feedback, feedback_id)
    if row is None:
        raise LookupError("工单不存在")
    before = row.status
    row.status = status
    session.flush()
    return row, before


def crawl_overview(session: Session, *, limit: int = 20) -> dict:
    """采集监控汇总：最近任务 + 熔断状态（熔断状态记在最近一条 crawl job_run）。"""
    jobs = session.scalars(
        select(JobRun)
        .order_by(JobRun.id.desc())
        .limit(min(100, max(1, limit)))
    ).all()
    last_crawl = session.scalar(
        select(JobRun)
        .where(JobRun.job_type == "crawl", JobRun.finished_at.is_not(None))
        .order_by(JobRun.id.desc())
        .limit(1)
    )
    stats = (last_crawl.stats or {}) if last_crawl else {}
    return {
        "jobs": [
            {
                "id": j.id,
                "job_type": j.job_type,
                "city": j.city.name if j.city else None,
                "status": j.status,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "stats": j.stats or {},
                "error": j.error,
            }
            for j in jobs
        ],
        "breaker": {
            "tripped": (stats.get("breaker") or {}).get("tripped") or [],
            "sources": stats.get("sources") or {},
            "totals": stats.get("totals") or {},
            "last_job_id": last_crawl.id if last_crawl else None,
        },
    }


def list_restaurants_admin(
    session: Session,
    *,
    city: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """店铺审核列表（文档 9.5）：支持城市/状态/关键词筛选 + 分页。"""
    stmt = select(Restaurant).join(City, Restaurant.city_id == City.id)
    count_stmt = select(func.count(Restaurant.id)).join(
        City, Restaurant.city_id == City.id
    )
    if city:
        stmt = stmt.where(City.name == city)
        count_stmt = count_stmt.where(City.name == city)
    if status:
        stmt = stmt.where(Restaurant.status == status)
        count_stmt = count_stmt.where(Restaurant.status == status)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(Restaurant.name.like(pattern))
        count_stmt = count_stmt.where(Restaurant.name.like(pattern))

    total = session.scalar(count_stmt) or 0
    rows = session.scalars(
        stmt.order_by(Restaurant.id)
        .limit(min(100, max(1, limit)))
        .offset(max(0, offset))
    ).all()
    items = [
        {
            "id": r.id,
            "name": r.name,
            "city": r.city.name if r.city else None,
            "address": r.address,
            "area": r.area,
            "cuisine": r.cuisine,
            "avg_price": r.avg_price,
            "status": r.status,
            "merged_into": r.merged_into,
            "mention_count": len(r.mentions),
            "alias_count": len(r.aliases),
        }
        for r in rows
    ]
    return items, total