"""管理后台运营服务（文档 9.5）：店铺审核视图、采集监控汇总、反馈工单处理。

仅做查询/编排，写操作的审计由接口层统一记录（app/services/audit.py）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    AlignmentReview,
    City,
    Feedback,
    JobRun,
    Mention as MentionRow,
    RawContent,
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

def _day_list(start: datetime, days: int) -> list[str]:
    """近 N 天日期（含今日）的 YYYY-MM-DD 列表，升序。"""
    base = start.date()
    return [(base - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def build_admin_stats(session: Session, *, now: datetime | None = None, days: int = 14) -> dict:
    """数据看板聚合（文档 9.5 数据看板 / 10.4 监控）。

    口径：
    - 抽取失败率 = raw_content 中 failed / (extracted + failed)
    - 任务成功率按最近 N 天 job_run 的 success / failed 终态计（running 不计入）
    - 地址完整率 =（address_text 或 area 有值）/ mention 总数（同 14 章验收口径）
    - LLM 成本暂以每日抽取产出（mention 数）为代理；token 级计费需接厂商账单，属 Backlog
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    overview = {
        "cities": session.scalar(select(func.count()).select_from(City)) or 0,
        "restaurants_total": session.scalar(select(func.count()).select_from(Restaurant)) or 0,
        "restaurants_active": session.scalar(
            select(func.count()).select_from(Restaurant).where(Restaurant.status == "active")
        )
        or 0,
        "mentions_total": session.scalar(select(func.count()).select_from(MentionRow)) or 0,
        "raw_total": session.scalar(select(func.count()).select_from(RawContent)) or 0,
    }

    raw_status = [
        {"status": status or "unknown", "count": count}
        for status, count in session.execute(
            select(RawContent.status, func.count()).group_by(RawContent.status)
        ).all()
    ]
    by_status = {r["status"]: r["count"] for r in raw_status}
    extracted = by_status.get("extracted", 0)
    failed = by_status.get("failed", 0)
    mention_total = overview["mentions_total"]
    missing_address = session.scalar(
        select(func.count())
        .select_from(MentionRow)
        .where(
            (MentionRow.address_text.is_(None)) | (MentionRow.address_text == ""),
            (MentionRow.area.is_(None)) | (MentionRow.area == ""),
        )
    )
    extract = {
        "extracted": extracted,
        "failed": failed,
        "failure_rate": round(failed / (extracted + failed), 4) if extracted + failed else 0.0,
        "avg_confidence": round(float(session.scalar(select(func.avg(MentionRow.confidence))) or 0.0), 4),
        "address_coverage": round((mention_total - (missing_address or 0)) / mention_total, 4)
        if mention_total
        else 0.0,
    }

    # 任务：最近 N 天按天聚合终态
    job_rows = session.execute(
        select(JobRun.status, JobRun.started_at).where(JobRun.started_at >= cutoff)
    ).all()
    day_keys = _day_list(now, days)
    jobs_by_day = {d: {"success": 0, "failed": 0} for d in day_keys}
    jobs_summary = {"success": 0, "failed": 0}
    for status, started_at in job_rows:
        if started_at is None or status not in ("success", "failed"):
            continue
        key = started_at.date().isoformat()
        if key in jobs_by_day:
            jobs_by_day[key][status] += 1
        jobs_summary[status] += 1
    jobs_total = jobs_summary["success"] + jobs_summary["failed"]
    jobs_summary["success_rate"] = round(jobs_summary["success"] / jobs_total, 4) if jobs_total else 0.0
    jobs_14d = [{"date": d, **jobs_by_day[d]} for d in day_keys]

    # 增长：mention 按天（LLM 抽取产出的代理口径）
    mention_rows = session.execute(
        select(MentionRow.created_at).where(MentionRow.created_at >= cutoff)
    ).all()
    mention_by_day = {d: 0 for d in day_keys}
    for (created_at,) in mention_rows:
        if created_at is None:
            continue
        key = created_at.date().isoformat()
        if key in mention_by_day:
            mention_by_day[key] += 1
    mentions_14d = [{"date": d, "count": mention_by_day[d]} for d in day_keys]

    # 城市分布：active / total
    city_rows = session.execute(
        select(City.name, Restaurant.status, func.count())
        .join(Restaurant, Restaurant.city_id == City.id, isouter=True)
        .group_by(City.name, Restaurant.status)
    ).all()
    per_city: dict[str, dict] = {}
    for name, status, count in city_rows:
        row = per_city.setdefault(name, {"active": 0, "total": 0})
        if status is not None:
            row["total"] += count
            if status == "active":
                row["active"] += count
    city_restaurants = [
        {"city": name, "active": v["active"], "total": v["total"]}
        for name, v in sorted(per_city.items(), key=lambda kv: -kv[1]["active"])
    ]

    return {
        "overview": overview,
        "raw_status": raw_status,
        "extract": extract,
        "jobs_summary": jobs_summary,
        "jobs_14d": jobs_14d,
        "mentions_14d": mentions_14d,
        "city_restaurants": city_restaurants,
        "generated_at": now.isoformat(),
        "window_days": days,
    }
