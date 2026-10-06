from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.config import get_settings
from .db import get_session, init_db
from .db.models import City, JobRun, Restaurant
from .services import (
    Extractor,
    align_mentions,
    build_rank_snapshot,
    build_restaurant_detail,
    build_restaurant_sources,
    collect_shop_scores,
    extract_raw_content,
    get_rank,
    get_raw_content,
    ingest_raw_content,
    list_cities,
    list_pending_reviews,
    make_rank_cache,
    run_all_cities,
    run_city_pipeline,
)
from .services.metrics import (
    CONTENT_TYPE_LATEST,
    add_http_metrics,
    render_metrics,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP 指标中间件（文档 10.4：QPS / P95 / 错误率）
add_http_metrics(app)

extractor = Extractor(settings)
rank_cache = make_rank_cache(settings)


def ok(data):
    return {"code": 0, "message": "ok", "data": data}


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, description="待抽取的文本片段")
    city_hint: str | None = Field(default=None, description="城市线索，如：成都")
    apply_confidence_filter: bool = Field(
        default=False, description="是否按 EXTRACT_CONFIDENCE_MIN 过滤低置信结果"
    )
    preprocess: bool = Field(
        default=False,
        description="true 时先执行清洗 + 切块流水线（文档 5.2）再抽取，适合原始长文",
    )


class IngestRequest(BaseModel):
    source: str = Field(description="数据源：dianping | xiaohongshu | forum | weibo | map")
    text: str = Field(min_length=1, description="原始文本（未清洗）")
    source_url: str | None = None
    city_hint: str | None = None
    raw_title: str | None = None


@app.get("/health")
def health():
    return ok({"status": "up", "llm_mode": "mock" if settings.use_mock else "live"})


@app.get("/metrics")
def metrics(session: Session = Depends(get_session)):
    """Prometheus 指标（文档 10.4）。

    仅由 Prometheus 内网抓取，nginx 不反代该路径，故不对外暴露。
    """
    return Response(content=render_metrics(session), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/extract")
def extract(req: ExtractRequest):
    if req.preprocess:
        result = extractor.extract_document(
            req.text,
            city_hint=req.city_hint,
            apply_confidence_filter=req.apply_confidence_filter,
        )
    else:
        result = extractor.extract(
            text=req.text,
            city_hint=req.city_hint,
            apply_confidence_filter=req.apply_confidence_filter,
        )
    return ok(result.model_dump())


@app.post("/api/v1/ingest")
def ingest(req: IngestRequest, session: Session = Depends(get_session)):
    row = ingest_raw_content(
        session,
        source=req.source,
        raw_text=req.text,
        source_url=req.source_url,
        city_hint=req.city_hint,
        raw_title=req.raw_title,
        settings=settings,
    )
    session.commit()
    return ok(
        {
            "id": row.id,
            "status": row.status,
            "lang": row.lang,
            "low_trust": row.low_trust,
            "ad_hits": row.ad_hits or [],
            "chunk_count": row.chunk_count,
        }
    )


@app.post("/api/v1/raw-contents/{raw_content_id}/extract")
def extract_raw_content_endpoint(
    raw_content_id: int,
    apply_confidence_filter: bool = False,
    session: Session = Depends(get_session),
):
    try:
        result = extract_raw_content(
            session,
            raw_content_id,
            extractor=extractor,
            settings=settings,
            apply_confidence_filter=apply_confidence_filter,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="raw_content 不存在")
    session.commit()
    return ok(result.model_dump())


@app.get("/api/v1/scoring/shops")
def scoring_shops(
    city_hint: str | None = None, session: Session = Depends(get_session)
):
    """聚合 mention 计算店铺得分（文档 6；按 restaurant 实体聚合）。"""
    scores = collect_shop_scores(session, city_hint=city_hint, settings=settings)
    return ok([s.model_dump() for s in scores])


@app.post("/api/v1/alignment/run")
def alignment_run(
    city_hint: str | None = None, session: Session = Depends(get_session)
):
    """对未归并的 mention 执行实体对齐（文档 5.5）。"""
    result = align_mentions(session, city_hint=city_hint, settings=settings)
    session.commit()
    return ok(result.model_dump())


@app.get("/api/v1/restaurants")
def list_restaurants(
    city_hint: str | None = None, session: Session = Depends(get_session)
):
    stmt = select(Restaurant).order_by(Restaurant.id)
    if city_hint:
        stmt = stmt.join(City, Restaurant.city_id == City.id).where(City.name == city_hint)
    return ok(
        [
            {
                "id": r.id,
                "name": r.name,
                "name_norm": r.name_norm,
                "city": r.city.name if r.city else None,
                "address": r.address,
                "status": r.status,
                "mention_count": len(r.mentions),
            }
            for r in session.scalars(stmt).all()
        ]
    )


@app.get("/api/v1/cities")
def cities(session: Session = Depends(get_session)):
    """城市列表（文档 8.1）：City 表 ∪ raw_content.city_hint。"""
    return ok(list_cities(session))


@app.get("/api/v1/cities/search")
def cities_search(q: str = "", session: Session = Depends(get_session)):
    """城市搜索（文档 8.1）；q 为空则返回全部。"""
    rows = list_cities(session)
    if q:
        rows = [c for c in rows if q in c["name"]]
    return ok(rows)


@app.get("/api/v1/restaurants/{restaurant_id}")
def restaurant_detail(restaurant_id: int, session: Session = Depends(get_session)):
    """店铺详情（文档 8.1 / 9.2 detail）。"""
    detail = build_restaurant_detail(session, restaurant_id, settings=settings)
    if detail is None:
        raise HTTPException(status_code=404, detail="店铺不存在")
    return ok(detail)


@app.get("/api/v1/restaurants/{restaurant_id}/sources")
def restaurant_sources(restaurant_id: int, session: Session = Depends(get_session)):
    """店铺来源引用列表（文档 8.1），做脱敏与外链提示（不内嵌第三方原文）。"""
    sources = build_restaurant_sources(session, restaurant_id)
    if sources is None:
        raise HTTPException(status_code=404, detail="店铺不存在")
    return ok(sources)


@app.get("/api/v1/alignment/reviews")
def alignment_reviews(session: Session = Depends(get_session)):
    return ok(
        [
            {
                "id": rv.id,
                "mention_id": rv.mention_id,
                "candidate_restaurant_id": rv.candidate_restaurant_id,
                "score": rv.score,
                "reason": rv.reason,
            }
            for rv in list_pending_reviews(session)
        ]
    )


@app.get("/api/v1/rank")
def rank(
    city: str,
    page: int = 1,
    page_size: int = 20,
    cuisine: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    area: str | None = None,
    session: Session = Depends(get_session),
):
    """读取该城市最新榜单快照（文档 8.2）。无快照 → 2001 语义。"""
    data = get_rank(
        session,
        city,
        page=max(1, page),
        page_size=max(1, min(100, page_size)),
        cuisine=cuisine,
        price_min=price_min,
        price_max=price_max,
        area=area,
        cache=rank_cache,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="该城市暂无榜单（正在收录）")
    return ok(data)


@app.post("/api/v1/rank/build")
def rank_build(city: str, session: Session = Depends(get_session)):
    """生成该城市榜单快照（文档 6.4 流水线第 4–5 步，供运营/定时任务触发）。"""
    try:
        snapshot = build_rank_snapshot(session, city, settings=settings, cache=rank_cache)
    except LookupError:
        raise HTTPException(status_code=404, detail="城市不存在")
    session.commit()
    return ok(
        {
            "snapshot_id": snapshot.id,
            "city": city,
            "generated_at": snapshot.generated_at.isoformat()
            if snapshot.generated_at
            else None,
            "algorithm_ver": snapshot.algorithm_ver,
            "total": len(snapshot.items or []),
        }
    )


@app.post("/api/v1/jobs/run")
def jobs_run(city: str | None = None, session: Session = Depends(get_session)):
    """触发城市流水线：抽取→对齐→打分→榜单快照（文档 4.4 / 6.4）。

    不传 city 则跑全部有内容的城市（供定时任务/运维调用）。
    失败会写入 job_run（GET /api/v1/admin/jobs 可查），并返回 500。
    """
    if city:
        jobs = [run_city_pipeline(session, city, settings=settings, cache=rank_cache)]
    else:
        jobs = run_all_cities(session, settings=settings, cache=rank_cache)
    return ok(
        [
            {
                "job_id": j.id,
                "job_type": j.job_type,
                "status": j.status,
                "stats": j.stats or {},
                "error": j.error,
            }
            for j in jobs
        ]
    )


@app.get("/api/v1/admin/jobs")
def admin_jobs(limit: int = 20, session: Session = Depends(get_session)):
    """最近的任务运行记录（文档 8.2 / 7.2 job_run）。"""
    rows = session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(min(100, max(1, limit)))).all()
    return ok(
        [
            {
                "id": r.id,
                "job_type": r.job_type,
                "city": r.city.name if r.city else None,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "stats": r.stats or {},
                "error": r.error,
            }
            for r in rows
        ]
    )


@app.get("/api/v1/raw-contents/{raw_content_id}")
def read_raw_content(raw_content_id: int, session: Session = Depends(get_session)):
    row = get_raw_content(session, raw_content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="raw_content 不存在")
    return ok(
        {
            "id": row.id,
            "source": row.source,
            "city_hint": row.city_hint,
            "status": row.status,
            "lang": row.lang,
            "low_trust": row.low_trust,
            "ad_hits": row.ad_hits or [],
            "chunk_count": row.chunk_count,
            "cleaned_text": row.cleaned_text,
            "chunks": [
                {
                    "chunk_index": c.chunk_index,
                    "text": c.text,
                    "start_offset": c.start_offset,
                    "end_offset": c.end_offset,
                    "token_estimate": c.token_estimate,
                    "status": c.status,
                }
                for c in row.chunks
            ],
            "mentions": [
                {
                    "id": m.id,
                    "shop_name_raw": m.shop_name_raw,
                    "address_text": m.address_text,
                    "dishes": m.dishes or [],
                    "sentiment": m.sentiment,
                    "praise_keywords": m.praise_keywords or [],
                    "complaints": m.complaints or [],
                    "is_recommendation": m.is_recommendation,
                    "confidence": m.confidence,
                    "evidence_span": m.evidence_span,
                    "evidence_start": m.evidence_start,
                    "evidence_end": m.evidence_end,
                }
                for m in row.mentions
            ],
        }
    )