"""监控指标层（开发文档 10.4）。

暴露 Prometheus 指标（`GET /metrics`），覆盖文档 10.4 的四类告警维度：

- 采集：各源成功率、403/429 比例、熔断状态（读取最近一条 crawl 的 job_run.stats）
- 抽取：LLM 调用失败率、JSON 解析失败率、内容/切块失败率
- 数据：去重率、无地址率、每日新增 mention
- 服务：HTTP QPS、P95 延迟、错误率（中间件在本进程内累计）
- 成本：LLM token 用量、高德 API 调用量（进程内计数器）

设计要点：
- 业务指标（采集/抽取/数据）在**每次抓取时**从数据库实时计算，故脚本进程写入的
  job_run / raw_content 变化能被 api 进程读到，不依赖跨进程累计。
- 服务/成本指标为进程内计数器，由处理该请求的进程累计（api）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db.models import ContentChunk, JobRun, Mention, RawContent

# --- 采集（文档 10.4：各源成功率、403/429 比例） --------------------------------

_crawl_success = Gauge(
    "alleybite_crawl_success_ratio",
    "各数据源最近一次采集成功率（fetched/(fetched+failed)）",
    ["source"],
)
_crawl_403 = Gauge(
    "alleybite_crawl_http_403_ratio",
    "各数据源最近一次采集的 403 占比",
    ["source"],
)
_crawl_429 = Gauge(
    "alleybite_crawl_http_429_ratio",
    "各数据源最近一次采集的 429 占比",
    ["source"],
)
_crawl_breaker = Gauge(
    "alleybite_crawl_breaker_open",
    "各数据源是否处于熔断暂停（1 是 / 0 否）",
    ["source"],
)
_crawl_last_run = Gauge(
    "alleybite_crawl_source_last_run_timestamp",
    "各数据源最近一次采集完成时间（Unix 时间戳）",
    ["source"],
)

# --- 抽取（文档 10.4：LLM 调用失败率、JSON 解析失败率） ------------------------

_extract_failure = Gauge(
    "alleybite_extract_failure_ratio",
    "内容抽取失败率（status=failed / (extracted+failed)）",
)
_chunk_failure = Gauge(
    "alleybite_extract_chunk_failure_ratio",
    "切块抽取失败率（chunk status=failed / 已抽取切块）",
)
_llm_requests = Counter(
    "alleybite_llm_requests_total",
    "LLM 调用次数（按结果）",
    ["provider", "result"],
)
_llm_tokens = Counter(
    "alleybite_llm_tokens_total",
    "LLM token 用量（input/output，进程内实时值）",
    ["provider", "kind"],
)

# --- 数据（文档 10.4：去重率、无地址率、每日新增 mention） --------------------

_dedup_ratio = Gauge(
    "alleybite_dedup_ratio",
    "最近一次采集的去重率（duplicated/(new+duplicated)）",
)
_missing_address = Gauge(
    "alleybite_mention_missing_address_ratio",
    "mention 无地址占比",
)
_mentions_24h = Gauge(
    "alleybite_mention_new_24h",
    "最近 24 小时新增 mention 数",
)

# --- 成本（文档 10.4：地图 API 配额） -----------------------------------------

_amap_requests = Counter(
    "alleybite_amap_requests_total",
    "高德地图 API 请求次数（按结果，进程内实时值）",
    ["result"],
)

# 成本预算指标：从 job_run.stats 汇总近 24h 用量，跨进程/批量任务均可见
_llm_tokens_24h = Gauge(
    "alleybite_llm_tokens_24h",
    "近 24 小时 LLM token 用量（从 job_run 汇总）",
    ["kind"],
)
_amap_requests_24h = Gauge(
    "alleybite_amap_requests_24h",
    "近 24 小时高德 API 实际请求次数（从 job_run 汇总）",
)

# --- 服务（文档 10.4：QPS / P95 延迟 / 错误率） --------------------------------

_http_requests = Counter(
    "alleybite_http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)
_http_latency = Histogram(
    "alleybite_http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 2.5, 5.0, 10.0),
)

_crawl_gauges = (_crawl_success, _crawl_403, _crawl_429, _crawl_breaker, _crawl_last_run)

# 进程内 LLM 用量累计，供流水线在任务结束时取差值写入 job_run.stats
_llm_usage = {"input": 0, "output": 0}


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _timestamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def update_business_metrics(session: Session, now: datetime | None = None) -> None:
    """从数据库实时刷新业务指标（采集/抽取/数据）。抓取时调用，无副作用。"""
    now = now or datetime.now(timezone.utc)

    # 采集：最近一条 crawl job_run 的分源统计
    for gauge in _crawl_gauges:
        gauge.clear()
    job = session.scalar(
        select(JobRun)
        .where(JobRun.job_type == "crawl")
        .order_by(JobRun.id.desc())
        .limit(1)
    )
    new_total = dup_total = 0
    if job is not None:
        finished = _timestamp(job.finished_at or job.started_at)
        for name, data in ((job.stats or {}).get("sources") or {}).items():
            fetched = int(data.get("fetched") or 0)
            failed = int(data.get("failed") or 0)
            ratio = _ratio(fetched, fetched + failed)
            if fetched == 0 and failed == 0:
                ratio = 1.0 if data.get("status") == "ok" else 0.0
            _crawl_success.labels(name).set(ratio)
            _crawl_403.labels(name).set(_ratio(int(data.get("http_403") or 0), fetched))
            _crawl_429.labels(name).set(_ratio(int(data.get("http_429") or 0), fetched))
            _crawl_breaker.labels(name).set(
                1.0 if data.get("status") == "breaker_open" else 0.0
            )
            _crawl_last_run.labels(name).set(finished)
            new_total += int(data.get("new") or 0)
            dup_total += int(data.get("duplicated") or 0)
    _dedup_ratio.set(_ratio(dup_total, new_total + dup_total))

    # 抽取：内容与切块失败率
    extracted = session.scalar(
        select(func.count()).select_from(RawContent).where(RawContent.status == "extracted")
    ) or 0
    failed = session.scalar(
        select(func.count()).select_from(RawContent).where(RawContent.status == "failed")
    ) or 0
    _extract_failure.set(_ratio(failed, extracted + failed))

    chunk_done = session.scalar(
        select(func.count())
        .select_from(ContentChunk)
        .where(ContentChunk.status == "extracted")
    ) or 0
    chunk_failed = session.scalar(
        select(func.count())
        .select_from(ContentChunk)
        .where(ContentChunk.status == "failed")
    ) or 0
    _chunk_failure.set(_ratio(chunk_failed, chunk_done + chunk_failed))

    # 数据：无地址率、每日新增 mention
    mentions_total = session.scalar(select(func.count()).select_from(Mention)) or 0
    mentions_missing = session.scalar(
        select(func.count())
        .select_from(Mention)
        .where(or_(Mention.address_text.is_(None), Mention.address_text == ""))
    ) or 0
    _missing_address.set(_ratio(mentions_missing, mentions_total))
    since = now - timedelta(hours=24)
    _mentions_24h.set(
        session.scalar(
            select(func.count())
            .select_from(Mention)
            .where(Mention.created_at >= since)
        )
        or 0
    )

    # 成本：从近 24h 完成的 job_run.stats 汇总 LLM token 与地图 API 请求数
    llm_in = llm_out = amap_calls = 0
    for stats in session.scalars(
        select(JobRun.stats).where(
            JobRun.finished_at.is_not(None), JobRun.finished_at >= since
        )
    ):
        stats = stats or {}
        llm_in += int(stats.get("llm_input_tokens") or 0)
        llm_out += int(stats.get("llm_output_tokens") or 0)
        amap = ((stats.get("sources") or {}).get("amap") or {}).get("detail") or {}
        amap_calls += int(amap.get("requests") or 0)
    _llm_tokens_24h.labels("input").set(llm_in)
    _llm_tokens_24h.labels("output").set(llm_out)
    _amap_requests_24h.set(amap_calls)


def render_metrics(session: Session, now: datetime | None = None) -> bytes:
    """刷新业务指标并返回 Prometheus 文本格式。"""
    update_business_metrics(session, now=now)
    return generate_latest()


def record_llm_call(provider: str, *, ok: bool, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """记录一次 LLM 调用（成功/失败）与 token 用量。"""
    _llm_requests.labels(provider or "unknown", "ok" if ok else "error").inc()
    if input_tokens:
        _llm_tokens.labels(provider or "unknown", "input").inc(input_tokens)
        _llm_usage["input"] += input_tokens
    if output_tokens:
        _llm_tokens.labels(provider or "unknown", "output").inc(output_tokens)
        _llm_usage["output"] += output_tokens


def llm_usage_snapshot() -> dict[str, int]:
    """当前进程 LLM 用量快照，供流水线在任务前后取差值。"""
    return dict(_llm_usage)


def llm_usage_delta(before: dict[str, int]) -> dict[str, int]:
    """相对快照的增量用量，用于写入 job_run.stats 做跨进程持久化。"""
    return {kind: _llm_usage[kind] - before.get(kind, 0) for kind in _llm_usage}


def record_amap_call(*, ok: bool) -> None:
    """记录一次高德 API 调用（成功/失败）。"""
    _amap_requests.labels("ok" if ok else "error").inc()


def add_http_metrics(app) -> None:
    """注册 HTTP 指标中间件：请求数、耗时直方图（用于 QPS/P95/错误率）。"""
    import time

    @app.middleware("http")
    async def _http_metrics(request, call_next):  # noqa: ANN001
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start

        # 用路由模板而非真实 URL，避免 /restaurants/{id} 造成标签爆炸
        path = request.url.path
        if path != "/metrics":
            route = request.scope.get("route")
            label_path = getattr(route, "path", None) or "__unmatched__"
            _http_requests.labels(request.method, label_path, str(response.status_code)).inc()
            _http_latency.labels(request.method, label_path).observe(elapsed)
        return response


__all__ = [
    "CONTENT_TYPE_LATEST",
    "add_http_metrics",
    "llm_usage_delta",
    "llm_usage_snapshot",
    "record_amap_call",
    "record_llm_call",
    "render_metrics",
    "update_business_metrics",
]
