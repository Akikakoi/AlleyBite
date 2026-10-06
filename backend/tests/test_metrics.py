"""监控指标单测（文档 10.4）：业务指标计算 + HTTP 中间件 + Prometheus 文本输出。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import ContentChunk, JobRun, Mention, RawContent
from app.services.metrics import add_http_metrics, render_metrics

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


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


def _metric_value(text: str, prefix: str) -> float:
    """从 Prometheus 文本中取首行匹配的指标值。"""
    for line in text.splitlines():
        if line.startswith(prefix + " ") or line.startswith(prefix + "{"):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"未找到指标：{prefix}")


def _seed_crawl_job(session):
    session.add(
        JobRun(
            job_type="crawl",
            status="success",
            started_at=NOW - timedelta(minutes=1),
            finished_at=NOW,
            stats={
                "sources": {
                    "seed": {
                        "status": "ok",
                        "fetched": 8,
                        "failed": 2,
                        "http_403": 1,
                        "http_429": 0,
                        "new": 6,
                        "duplicated": 4,
                    },
                    "rss": {"status": "breaker_open", "fetched": 0, "failed": 0},
                    "amap": {
                        "status": "ok",
                        "fetched": 20,
                        "failed": 0,
                        "detail": {"requests": 6},
                    },
                }
            },
        )
    )
    # 近 24h 完成的流水线任务：LLM token 用量
    session.add(
        JobRun(
            job_type="rank",
            status="success",
            started_at=NOW - timedelta(hours=2),
            finished_at=NOW - timedelta(hours=1),
            stats={"llm_input_tokens": 1000, "llm_output_tokens": 500},
        )
    )


def _seed_content(session):
    for idx, status in enumerate(["extracted", "extracted", "extracted", "failed"]):
        session.add(
            RawContent(
                source="forum",
                content_hash=f"hash-{idx}",
                raw_text="x",
                status=status,
            )
        )
    session.flush()
    raw = session.query(RawContent).first()
    for idx, status in enumerate(["extracted", "extracted", "failed"]):
        session.add(
            ContentChunk(
                raw_content_id=raw.id,
                chunk_index=idx,
                text="c",
                start_offset=0,
                end_offset=1,
                status=status,
            )
        )


def _seed_mentions(session):
    for idx in range(4):
        session.add(
            Mention(
                raw_content_id=1,
                shop_name_raw=f"店{idx}",
                address_text="某地址" if idx < 2 else None,
                created_at=NOW - timedelta(hours=1 if idx < 3 else 30),
            )
        )


def test_business_metrics(session):
    _seed_crawl_job(session)
    _seed_content(session)
    _seed_mentions(session)
    session.flush()

    text = render_metrics(session, now=NOW).decode("utf-8")

    assert _metric_value(text, 'alleybite_crawl_success_ratio{source="seed"}') == 0.8
    assert _metric_value(text, 'alleybite_crawl_http_403_ratio{source="seed"}') == 0.125
    assert _metric_value(text, 'alleybite_crawl_breaker_open{source="rss"}') == 1.0
    assert _metric_value(text, "alleybite_dedup_ratio") == 0.4
    assert _metric_value(text, "alleybite_extract_failure_ratio") == 0.25
    assert _metric_value(text, "alleybite_mention_missing_address_ratio") == 0.5
    # 前 3 条在 24h 内，第 4 条在 30h 前
    assert _metric_value(text, "alleybite_mention_new_24h") == 3.0
    # 成本：从 job_run.stats 汇总近 24h 用量
    assert _metric_value(text, 'alleybite_llm_tokens_24h{kind="input"}') == 1000.0
    assert _metric_value(text, 'alleybite_llm_tokens_24h{kind="output"}') == 500.0
    assert _metric_value(text, "alleybite_amap_requests_24h") == 6.0


def test_empty_database_metrics_are_zero(session):
    text = render_metrics(session, now=NOW).decode("utf-8")
    assert _metric_value(text, "alleybite_extract_failure_ratio") == 0.0
    assert _metric_value(text, "alleybite_dedup_ratio") == 0.0


def test_http_middleware_records_request():
    app = FastAPI()
    add_http_metrics(app)

    @app.get("/ping")
    def ping():  # noqa: ANN202
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/ping").status_code == 200

    from prometheus_client import generate_latest

    text = generate_latest().decode("utf-8")
    value = _metric_value(
        text, 'alleybite_http_requests_total{method="GET",path="/ping",status="200"}'
    )
    assert value >= 1.0
