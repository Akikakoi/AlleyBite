"""城市流水线编排（文档 4.4 / 6.4）：抽取 → 对齐 → 打分 → 榜单快照。

以 job_run 记录每次运行（job_type=rank），便于运维观察与重跑。
清洗已在 ingest 阶段完成，故本流水线从「抽取未抽取的 cleaned 内容」开始。
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..db.models import City, JobRun, RankSnapshot, RawContent
from .alignment import align_mentions, get_or_create_city
from .cache import RankCache
from .extract_pipeline import extract_raw_content
from .extractor import Extractor
from .metrics import llm_usage_delta, llm_usage_snapshot
from .rank_service import build_rank_snapshot
from .scoring import to_utc


def run_city_pipeline(
    session: Session,
    city_hint: str,
    *,
    settings: Settings | None = None,
    extractor: Extractor | None = None,
    now: datetime | None = None,
    top_n: int | None = None,
    cache: RankCache | None = None,
) -> JobRun:
    """执行单个城市流水线并落 job_run；成功/失败均 commit（运维需持久记录）。"""
    settings = settings or get_settings()
    started = to_utc(now) or datetime.now(timezone.utc)

    city = get_or_create_city(session, city_hint)
    job = JobRun(job_type="rank", city_id=city.id, started_at=started)
    session.add(job)
    session.flush()

    try:
        pending = list(
            session.scalars(
                select(RawContent).where(
                    RawContent.city_hint == city_hint,
                    RawContent.status == "cleaned",
                )
            ).all()
        )
        extracted = 0
        mentions = 0
        usage_before = llm_usage_snapshot()
        for row in pending:
            if not row.chunks:
                continue
            result = extract_raw_content(
                session, row.id, extractor=extractor, settings=settings
            )
            extracted += 1
            mentions += result.mention_count
        # LLM token 用量落 job_run.stats，供 /metrics 汇总成本（文档 10.4）
        usage = llm_usage_delta(usage_before)

        alignment = align_mentions(session, city_hint=city_hint, settings=settings)
        snapshot = build_rank_snapshot(
            session,
            city_hint,
            settings=settings,
            now=started,
            top_n=top_n or 50,
            cache=cache,
        )

        job.status = "success"
        job.stats = {
            "extracted_contents": extracted,
            "mentions": mentions,
            "llm_input_tokens": usage["input"],
            "llm_output_tokens": usage["output"],
            "restaurants_created": alignment.restaurants_created,
            "mentions_aligned": alignment.mentions_aligned,
            "mentions_review": alignment.mentions_review,
            "rank_items": len(snapshot.items or []),
            "snapshot_id": snapshot.id,
            "algorithm_ver": snapshot.algorithm_ver,
        }
    except Exception as exc:  # noqa: BLE001  失败需记入 job_run 后再抛出
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        session.flush()
        session.commit()
        raise

    job.finished_at = datetime.now(timezone.utc)
    session.flush()
    session.commit()
    return job


def list_cities(session: Session) -> list[dict]:
    """城市列表（文档 8.1 /cities）：City 表 ∪ raw_content.city_hint 去重。

    仅有内容线索、尚无 City 行的城市补 status="collecting"（City 行在对齐时才惰性创建）。
    """
    cities = {c.name: c for c in session.scalars(select(City)).all()}
    hints = set(
        session.scalars(
            select(RawContent.city_hint).where(RawContent.city_hint.is_not(None))
        ).all()
    )
    ranked_city_ids = set(session.scalars(select(RankSnapshot.city_id).distinct()).all())

    rows: list[dict] = []
    for name in sorted(set(cities) | hints):
        city = cities.get(name)
        rows.append(
            {
                "name": name,
                "code": city.code if city else None,
                "status": city.status if city else "collecting",
                "has_rank": bool(city and city.id in ranked_city_ids),
            }
        )
    return rows


def list_city_names(session: Session) -> list[str]:
    """有内容待处理的城市名（来自 raw_content.city_hint），供定时任务遍历。

    以内容线索而非 city 表为准：city 行在对齐时才惰性创建，早期可能尚不存在。
    """
    return list(
        session.scalars(
            select(RawContent.city_hint)
            .where(RawContent.city_hint.is_not(None))
            .distinct()
            .order_by(RawContent.city_hint)
        ).all()
    )


def run_all_cities(
    session: Session,
    *,
    settings: Settings | None = None,
    extractor: Extractor | None = None,
    now: datetime | None = None,
    cache: RankCache | None = None,
) -> list[JobRun]:
    """对全部有内容的城市依次跑流水线；单城失败不阻断其余（失败记录已落 job_run）。"""
    jobs: list[JobRun] = []
    for name in list_city_names(session):
        try:
            jobs.append(
                run_city_pipeline(
                    session,
                    name,
                    settings=settings,
                    extractor=extractor,
                    now=now,
                    cache=cache,
                )
            )
        except Exception:  # noqa: BLE001  继续跑其余城市
            continue
    return jobs