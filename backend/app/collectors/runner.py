"""采集编排（文档 4.3 / 4.4）：多源采集 → 入库/写实体 → 落 job_run。

- 文本类源：逐条走 ``ingest_raw_content``（唯一入库出口），靠 (source, content_hash) 幂等
- POI 源：直写 restaurant 实体基准
- 每源独立 try/except 且**结束即 commit**，单源失败不阻断其余
- 熔断状态记在最近一条 crawl job_run 的 stats 中（不新建表）
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..db.models import JobRun, RawContent
from ..services.ingest import compute_content_hash, ingest_raw_content
from ..services.scoring import to_utc
from .amap import AmapPoiCollector, upsert_pois
from .base import BaseCollector
from .core import CollectorDeps, SourceResult, make_default_deps
from .registry import P0_SOURCES, build_collector
from .seed import DEFAULT_SEED_PATH


def _cursor(session: Session, source: str, city_hint: str | None) -> datetime | None:
    """增量游标：该 source（可选限定城市）已入库内容的最大发布时间。"""
    stmt = select(func.max(RawContent.published_at)).where(RawContent.source == source)
    if city_hint:
        stmt = stmt.where(RawContent.city_hint == city_hint)
    return to_utc(session.scalar(stmt))


def _load_breaker(session: Session) -> dict[str, datetime]:
    """从最近一条**已完成**的 crawl job_run 恢复各源熔断到期时间。

    必须排除本轮尚未写 stats 的 running 记录，否则状态永远读不到。
    """
    job = session.scalar(
        select(JobRun)
        .where(JobRun.job_type == "crawl", JobRun.finished_at.is_not(None))
        .order_by(JobRun.id.desc())
        .limit(1)
    )
    if job is None or not job.stats:
        return {}
    state: dict[str, datetime] = {}
    for name, data in (job.stats.get("sources") or {}).items():
        raw = (data or {}).get("tripped_until")
        if not raw:
            continue
        try:
            parsed = to_utc(datetime.fromisoformat(raw))
        except (TypeError, ValueError):
            continue
        if parsed:
            state[name] = parsed
    return state


def _run_text_source(
    session: Session,
    collector: BaseCollector,
    result: SourceResult,
    settings: Settings,
    since: datetime | None,
) -> None:
    items = collector.fetch(since=since)
    for item in items:
        digest = compute_content_hash(item.raw_text)
        exists = session.scalar(
            select(RawContent.id).where(
                RawContent.source == item.source, RawContent.content_hash == digest
            )
        )
        ingest_raw_content(
            session,
            source=item.source,
            raw_text=item.raw_text,
            source_url=item.source_url,
            city_hint=item.city_hint or collector.city_hint,
            raw_title=item.raw_title,
            raw_ref=item.raw_ref,
            published_at=to_utc(item.published_at),
            settings=settings,
        )
        if exists is None:
            result.new += 1
        else:
            result.duplicated += 1
    result.fetched = len(items)


def _run_poi_source(collector: AmapPoiCollector, result: SourceResult, session: Session) -> None:
    if not collector.city_hint:
        result.status = "skipped"
        result.error = "缺少 city_hint，无法检索 POI"
        return
    records = collector.search()
    created, matched = upsert_pois(session, collector.city_hint, records)
    result.fetched = len(records)
    result.poi_written = created
    result.detail = {"poi_matched": matched}


def run_source(
    session: Session,
    collector: BaseCollector,
    *,
    settings: Settings,
    since: datetime | None = None,
) -> SourceResult:
    """执行单个采集器并汇总统计；异常转为 failed 而不抛出。"""
    result = SourceResult(source=collector.source, cursor_at=since)
    try:
        if collector.kind == "poi":
            _run_poi_source(collector, result, session)  # type: ignore[arg-type]
        else:
            _run_text_source(session, collector, result, settings, since)
    except Exception as exc:  # noqa: BLE001  单源失败需记录后继续
        result.status = "failed"
        result.error = str(exc)

    result.http_403 = collector.counters["http_403"]
    result.http_429 = collector.counters["http_429"]
    # 实际请求数：供地图 API 配额监控（文档 10.4）从 job_run 汇总
    result.detail["requests"] = collector.counters["requests"]
    if result.status == "ok":
        result.failed = collector.counters["failed"]
    return result


def run_crawl(
    session: Session,
    settings: Settings | None = None,
    *,
    sources: list[str] | None = None,
    mode: str = "incremental",
    deps: CollectorDeps | None = None,
    city_hint: str | None = None,
    seed_path: str = DEFAULT_SEED_PATH,
    force: bool = False,
) -> JobRun:
    """跑一轮采集，落一条 job_type="crawl" 的 job_run。"""
    settings = settings or get_settings()
    deps = deps or make_default_deps(settings)
    started = to_utc(deps.now())

    job = JobRun(job_type="crawl", status="running", started_at=started)
    session.add(job)
    session.flush()

    names = list(sources) if sources else list(P0_SOURCES)
    stats: dict = {
        "mode": mode,
        "city_hint": city_hint,
        "sources": {},
        "totals": {},
        "breaker": {"tripped": []},
    }

    if settings.is_night(deps.local_hour()) and not force:
        stats["skipped"] = "night"
        job.status = "success"
        job.stats = stats
        job.finished_at = to_utc(deps.now())
        session.flush()
        session.commit()
        return job

    breaker_state = _load_breaker(session)
    results: list[SourceResult] = []

    for name in names:
        tripped_until = breaker_state.get(name)
        if tripped_until and tripped_until > started:
            result = SourceResult(
                source=name, status="breaker_open", tripped_until=tripped_until
            )
            deps.alert(
                "breaker_open", {"source": name, "until": tripped_until.isoformat()}
            )
            stats["sources"][name] = result.to_stats()
            stats["breaker"]["tripped"].append(name)
            results.append(result)
            continue

        try:
            collector = build_collector(
                name, settings, city_hint=city_hint, deps=deps, seed_path=seed_path
            )
        except ValueError as exc:
            result = SourceResult(source=name, status="failed", error=str(exc))
            stats["sources"][name] = result.to_stats()
            results.append(result)
            continue

        if not collector.is_ready:
            result = SourceResult(source=name, status="skipped", error="配置未就绪")
            stats["sources"][name] = result.to_stats()
            results.append(result)
            continue

        since = None if mode == "full" else _cursor(session, name, city_hint)
        result = run_source(session, collector, settings=settings, since=since)

        if result.http_403 + result.http_429 >= settings.crawl_breaker_fail_threshold:
            until = to_utc(deps.now()) + timedelta(
                minutes=settings.crawl_breaker_cooldown_minutes
            )
            result.tripped_until = until
            result.detail["breaker"] = "tripped"
            deps.alert(
                "breaker_trip",
                {
                    "source": name,
                    "until": until.isoformat(),
                    "http_403": result.http_403,
                    "http_429": result.http_429,
                },
            )
            stats["breaker"]["tripped"].append(name)

        stats["sources"][name] = result.to_stats()
        results.append(result)
        session.commit()  # 每源结束即提交：单源失败隔离

    if results and all(r.status == "failed" for r in results):
        job.status = "failed"
        job.error = "; ".join(r.error for r in results if r.error) or "全部源失败"
    else:
        job.status = "success"

    stats["totals"] = {
        "sources": len(results),
        "ok": sum(1 for r in results if r.status == "ok"),
        "skipped": sum(1 for r in results if r.status in ("skipped", "breaker_open")),
        "fetched": sum(r.fetched for r in results),
        "new": sum(r.new for r in results),
        "duplicated": sum(r.duplicated for r in results),
        "failed": sum(r.failed for r in results),
        "poi_written": sum(r.poi_written for r in results),
    }
    job.stats = stats
    job.finished_at = to_utc(deps.now())
    session.flush()
    session.commit()
    return job