"""运维/管线端点鉴权（2026-10-16 加固）：一律不对匿名开放。

- 读类（scoring/alignment 队列/raw-contents）：任意管理角色；
- 写类（ingest/extract/alignment/run/rank/build/jobs/run）：operator 及以上，
  且关键写操作落 admin_audit_log。
公网 nginx 对这些路径已做 404 黑名单，此处为应用层纵深。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.db import get_session
from app.db.base import Base
from app.db.models import AdminAuditLog
from app.main import app
from app.services.admin_auth import set_admin_password


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


@pytest.fixture
def client(session):
    """不带 lifespan 的 TestClient：跳过 init_db，会话换成内存库。"""
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _admin_headers(client, session) -> dict:
    set_admin_password(session, "op", "pw", settings=app_main.settings)
    resp = client.post("/api/v1/admin/login", json={"username": "op", "password": "pw"})
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


OPS_WRITE_URLS = [
    ("post", "/api/v1/ingest", {"source": "seed", "text": "成都巷子里的明婷饭店，脑花豆腐一绝"}),
    ("post", "/api/v1/alignment/run", None),
    ("post", "/api/v1/rank/build?city=%E6%88%90%E9%83%BD", None),
    ("post", "/api/v1/jobs/run", None),
]
OPS_READ_URLS = [
    "/api/v1/scoring/shops",
    "/api/v1/alignment/reviews",
    "/api/v1/raw-contents/1",
]


def test_ops_write_endpoints_reject_anonymous(client):
    for method, url, payload in OPS_WRITE_URLS:
        kwargs = {"json": payload} if payload is not None else {}
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 401, url


def test_ops_read_endpoints_reject_anonymous(client):
    for url in OPS_READ_URLS:
        assert client.get(url).status_code == 401, url


def test_ops_endpoints_accept_admin_and_write_audit(client, session):
    headers = _admin_headers(client, session)

    resp = client.post(
        "/api/v1/ingest",
        json={"source": "seed", "text": "成都巷子里的明婷饭店，脑花豆腐一绝"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 0

    actions = set(session.scalars(select(AdminAuditLog.action)).all())
    assert "ingest.create" in actions

    # 读接口放行；raw-contents/9999 不存在返回 404（而非 401），说明鉴权已过
    assert client.get("/api/v1/scoring/shops", headers=headers).status_code == 200
    assert client.get("/api/v1/alignment/reviews", headers=headers).status_code == 200
    assert client.get("/api/v1/raw-contents/9999", headers=headers).status_code == 404


def test_crawl_run_sync_fallback(client, session):
    """默认（未开 CRAWL_RUN_ASYNC）同步执行并返回 job 结果。"""
    headers = _admin_headers(client, session)
    resp = client.post("/api/v1/admin/crawl/run", json={}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["dispatched"] is False
    assert body["job_id"] is not None


def test_crawl_run_async_dispatch(client, session, monkeypatch):
    """CRAWL_RUN_ASYNC=true 时投递 Celery 并立即返回 queued。"""
    dispatched: list[tuple[str, dict]] = []
    monkeypatch.setattr(app_main.settings, "crawl_run_async", True)
    monkeypatch.setattr(
        app_main, "_dispatch_celery", lambda name, **kw: dispatched.append((name, kw))
    )
    headers = _admin_headers(client, session)

    resp = client.post(
        "/api/v1/admin/crawl/run",
        json={"city": "成都", "mode": "incremental"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["dispatched"] is True
    assert body["status"] == "queued"
    assert body["job_id"] is None
    assert dispatched == [
        (
            "crawl_adhoc",
            {"city": "成都", "sources": None, "mode": "incremental", "force": False},
        )
    ]


def test_jobs_run_async_dispatch(client, session, monkeypatch):
    dispatched: list[tuple[str, dict]] = []
    monkeypatch.setattr(app_main.settings, "crawl_run_async", True)
    monkeypatch.setattr(
        app_main, "_dispatch_celery", lambda name, **kw: dispatched.append((name, kw))
    )
    headers = _admin_headers(client, session)

    resp = client.post("/api/v1/jobs/run?city=%E6%88%90%E9%83%BD", headers=headers)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body[0]["dispatched"] is True
    assert body[0]["status"] == "queued"
    assert dispatched == [("run_pipeline", {"city": "成都"})]
