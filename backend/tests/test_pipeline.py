from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import City, JobRun, RankSnapshot
from app.services.ingest import ingest_raw_content
from app.services.pipeline import list_city_names, run_all_cities, run_city_pipeline

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)

TEXTS = [
    "昨天去吃了明婷饭店，藏在青羊区同心路的巷子里，环境一般，但脑花豆腐太香，人均 65。",
    "王妈手撕烤兔，开在玉林，兔头麻辣入味，人均 40。",
]


def make_settings(**overrides) -> Settings:
    base = dict(llm_mock=True, llm_api_key="")
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


def _ingest(session, city, settings):
    for text in TEXTS:
        ingest_raw_content(
            session, source="forum", raw_text=text, city_hint=city, settings=settings
        )
    session.commit()


def test_run_city_pipeline_success(session):
    settings = make_settings()
    _ingest(session, "成都", settings)

    job = run_city_pipeline(session, "成都", settings=settings, now=NOW)

    assert job.status == "success"
    assert job.stats["extracted_contents"] == 2
    assert job.stats["rank_items"] == 2
    assert job.stats["snapshot_id"] is not None
    session.refresh(job)
    assert job.finished_at is not None
    assert session.scalar(select(RankSnapshot)) is not None
    assert session.scalar(select(City).where(City.name == "成都")).status == "active"


def test_run_city_pipeline_records_failure(session, monkeypatch):
    settings = make_settings()
    _ingest(session, "成都", settings)

    import app.services.pipeline as pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("build failed")

    monkeypatch.setattr(pipeline, "build_rank_snapshot", boom)

    with pytest.raises(RuntimeError):
        run_city_pipeline(session, "成都", settings=settings, now=NOW)

    job = session.scalars(select(JobRun)).one()
    assert job.status == "failed"
    assert "build failed" in job.error
    assert job.finished_at is not None


def test_run_city_pipeline_is_idempotent(session):
    settings = make_settings()
    _ingest(session, "成都", settings)

    run_city_pipeline(session, "成都", settings=settings, now=NOW)
    second = run_city_pipeline(session, "成都", settings=settings, now=NOW)

    assert second.status == "success"
    assert second.stats["extracted_contents"] == 0  # 已抽取内容不重复抽取
    assert second.stats["rank_items"] == 2


def test_list_city_names_and_run_all(session):
    settings = make_settings()
    for city, text in zip(("成都", "重庆"), TEXTS):
        ingest_raw_content(
            session, source="forum", raw_text=text, city_hint=city, settings=settings
        )
    session.commit()

    assert set(list_city_names(session)) == {"成都", "重庆"}

    jobs = run_all_cities(session, settings=settings, now=NOW)
    assert len(jobs) == 2
    assert all(j.status == "success" for j in jobs)