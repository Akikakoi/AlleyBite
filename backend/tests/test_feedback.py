"""纠错/举报单测（文档 9.3 纠错入口 / 9.5 反馈处理 / 14 章合规验收）。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.core.config import Settings
from app.db import get_session
from app.db.base import Base
from app.db.models import Feedback
from app.main import app
from app.services.admin_auth import set_admin_password
from app.services.feedback_service import (
    FeedbackRateLimited,
    create_feedback,
    hash_ip,
    list_feedback,
    recent_feedback_count,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


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


@pytest.fixture
def client(session):
    """不带 lifespan 的 TestClient：跳过 init_db，会话换成内存库。"""
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- 服务层 -----------------------------------------------------------------

def test_hash_ip_salted_deterministic_without_plaintext():
    settings = make_settings(feedback_ip_salt="pepper")
    hashed = hash_ip("1.2.3.4", settings=settings)

    assert hashed == hash_ip("1.2.3.4", settings=settings)
    assert len(hashed) == 64
    assert "1.2.3.4" not in hashed
    assert hashed != hash_ip("1.2.3.4", settings=make_settings(feedback_ip_salt="other"))
    assert hash_ip(None, settings=settings) is None


def test_create_feedback_persists_and_trims(session):
    settings = make_settings()
    row = create_feedback(
        session,
        content="  地址写错了  ",
        settings=settings,
        contact="  a@b.com  ",
        ip_hash=hash_ip("9.9.9.9", settings=settings),
        now=NOW,
    )

    assert row.id is not None
    assert row.content == "地址写错了"
    assert row.contact == "a@b.com"
    assert row.type == "info"
    assert row.status == "pending"
    assert row.created_at is not None
    assert len(session.scalars(select(Feedback)).all()) == 1


def test_create_feedback_unknown_type_falls_back_to_other(session):
    row = create_feedback(session, content="x", settings=make_settings(), type="weird")
    assert row.type == "other"


@pytest.mark.parametrize("content", ["", "   "])
def test_create_feedback_rejects_empty_content(session, content):
    with pytest.raises(ValueError):
        create_feedback(session, content=content, settings=make_settings())


def test_create_feedback_rejects_overlong_content(session):
    with pytest.raises(ValueError):
        create_feedback(
            session,
            content="字" * 11,
            settings=make_settings(feedback_content_max_len=10),
        )


def test_create_feedback_missing_restaurant_raises(session):
    with pytest.raises(LookupError):
        create_feedback(session, content="x", settings=make_settings(), restaurant_id=999)


def test_rate_limit_blocks_within_window_then_recovers(session):
    settings = make_settings(feedback_rate_limit_max=2, feedback_rate_limit_window_minutes=60)
    ip_hash = hash_ip("5.5.5.5", settings=settings)

    for _ in range(2):
        create_feedback(session, content="x", settings=settings, ip_hash=ip_hash, now=NOW)

    with pytest.raises(FeedbackRateLimited):
        create_feedback(session, content="x", settings=settings, ip_hash=ip_hash, now=NOW)

    # 窗口外的提交释放额度
    row = create_feedback(
        session,
        content="x",
        settings=settings,
        ip_hash=ip_hash,
        now=NOW + timedelta(minutes=61),
    )
    assert row.id is not None


def test_recent_feedback_count_window_and_none_ip(session):
    settings = make_settings(feedback_rate_limit_max=99, feedback_rate_limit_window_minutes=60)
    ip_hash = hash_ip("5.5.5.5", settings=settings)

    create_feedback(
        session, content="x", settings=settings, ip_hash=ip_hash, now=NOW - timedelta(minutes=120)
    )
    create_feedback(session, content="x", settings=settings, ip_hash=ip_hash, now=NOW)

    assert recent_feedback_count(session, ip_hash, settings=settings, now=NOW) == 1
    assert recent_feedback_count(session, None, settings=settings, now=NOW) == 0


def test_list_feedback_orders_desc_and_filters_status(session):
    settings = make_settings()
    first = create_feedback(session, content="a", settings=settings)
    second = create_feedback(session, content="b", settings=settings)
    second.status = "resolved"
    session.flush()

    rows = list_feedback(session)
    assert [r.id for r in rows] == [second.id, first.id]
    assert [r.id for r in list_feedback(session, status="resolved")] == [second.id]


# --- HTTP 接口 --------------------------------------------------------------

def test_submit_feedback_endpoint_stores_hashed_ip(client, session):
    resp = client.post(
        "/api/v1/feedback",
        json={"content": "这家店地址写错了", "type": "info", "contact": "1@x.com"},
        headers={"x-forwarded-for": "1.1.1.1, 10.0.0.1"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "pending"

    row = session.scalars(select(Feedback)).one()
    assert row.contact == "1@x.com"
    assert row.ip_hash and "1.1.1.1" not in row.ip_hash


def test_submit_feedback_endpoint_rejects_empty_content(client):
    assert client.post("/api/v1/feedback", json={"content": ""}).status_code == 422


def test_submit_feedback_endpoint_missing_restaurant(client):
    resp = client.post("/api/v1/feedback", json={"content": "x", "restaurant_id": 999})
    assert resp.status_code == 404


def test_submit_feedback_endpoint_rate_limited(client):
    headers = {"x-forwarded-for": "2.2.2.2"}
    for _ in range(5):  # 默认上限 5
        assert (
            client.post("/api/v1/feedback", json={"content": "x"}, headers=headers).status_code
            == 200
        )
    assert (
        client.post("/api/v1/feedback", json={"content": "x"}, headers=headers).status_code == 429
    )


def _admin_headers(client, session) -> dict:
    """走真实登录流程取后台令牌（口令迭代已在 conftest 降低）。"""
    set_admin_password(session, "op", "pw", settings=app_main.settings)
    resp = client.post(
        "/api/v1/admin/login", json={"username": "op", "password": "pw"}
    )
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def test_admin_feedback_requires_auth(client):
    assert client.get("/api/v1/admin/feedback").status_code == 401


def test_admin_feedback_lists_rows(client, session):
    client.post("/api/v1/feedback", json={"content": "关停了", "type": "closed"})

    data = client.get(
        "/api/v1/admin/feedback", headers=_admin_headers(client, session)
    ).json()["data"]
    assert len(data) == 1
    assert data[0]["type"] == "closed"
    assert data[0]["content"] == "关停了"
    assert "ip_hash" not in data[0]