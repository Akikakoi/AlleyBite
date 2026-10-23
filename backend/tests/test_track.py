"""行为埋点单测（方向六 榜单可带走 批 1）：动作白名单、匿名写入、限流、日汇总。

覆盖：record_track_event 校验与截断、TrackRateLimited、track_daily_summary
分桶/UV 去重/带走率口径、/api/v1/track 匿名端点、admin 汇总端点鉴权。
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.core.config import Settings
from app.db import get_session
from app.db.base import Base
from app.db.models import TrackEvent
from app.main import app
from app.services.admin_auth import set_admin_password
from app.services.track_service import (
    TrackRateLimited,
    record_track_event,
    track_daily_summary,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def make_settings(**overrides) -> Settings:
    base = {"llm_mock": True, "llm_api_key": ""}
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


# --- 服务层：写入 -----------------------------------------------------------------


def test_unknown_action_rejected(session):
    settings = make_settings()
    with pytest.raises(ValueError):
        record_track_event(session, action="hack", settings=settings, now=NOW)


def test_record_clamps_free_text_fields(session):
    settings = make_settings()
    row = record_track_event(
        session,
        action="rank_view",
        settings=settings,
        session_key="s" * 100,
        city="  成都  ",
        now=NOW,
    )
    assert len(row.session_key) == 64
    assert row.city == "成都"
    assert row.action == "rank_view"
    assert row.user_id is None and row.ip_hash is None


def test_rate_limit_per_ip(session):
    settings = make_settings(track_rate_limit_max=2, track_rate_limit_window_minutes=60)
    for _ in range(2):
        record_track_event(
            session,
            action="rank_view",
            settings=settings,
            session_key="s1",
            ip_hash="aaa",
            now=NOW,
        )
    with pytest.raises(TrackRateLimited):
        record_track_event(
            session, action="rank_view", settings=settings, ip_hash="aaa", now=NOW
        )
    # 其他 IP 不受影响；无 ip_hash（直连测试客户端）不限流
    record_track_event(
        session, action="rank_view", settings=settings, ip_hash="bbb", now=NOW
    )
    record_track_event(session, action="rank_view", settings=settings, now=NOW)


def test_rate_limit_window_expires(session):
    settings = make_settings(track_rate_limit_max=1, track_rate_limit_window_minutes=60)
    record_track_event(
        session, action="rank_view", settings=settings, ip_hash="aaa", now=NOW
    )
    record_track_event(
        session,
        action="rank_view",
        settings=settings,
        ip_hash="aaa",
        now=NOW + timedelta(hours=2),
    )


# --- 服务层：日汇总 ----------------------------------------------------------------


def _seed_summary_events(session):
    today = NOW
    yesterday = NOW - timedelta(days=1)
    events = [
        # 今天：会话 s1 两次浏览 + 一次带走 → UV 去重
        (today, "rank_view", "s1"),
        (today, "rank_view", "s1"),
        (today, "rank_view", "s2"),
        (today, "share_poster_rank", "s1"),
        (today, "copy_link_rank", "s3"),
        # 昨天：只有浏览，带走率 0
        (yesterday, "rank_view", "s4"),
    ]
    for created_at, action, session_key in events:
        session.add(
            TrackEvent(
                action=action,
                session_key=session_key,
                created_at=created_at,
            )
        )
    # session_key 缺失的事件：独立计数，不并入任何会话
    session.add(TrackEvent(action="rank_view", session_key=None, created_at=today))
    session.flush()


def test_summary_buckets_and_uv(session):
    _seed_summary_events(session)
    summary = track_daily_summary(session, now=NOW, days=14)

    # 今日 rank_view：s1、s2、1 条匿名事件（独立计数）→ UV 3
    today = next(d for d in summary["days"] if d["date"] == "2026-10-23")
    yesterday = next(d for d in summary["days"] if d["date"] == "2026-10-22")
    assert today["rank_view_uv"] == 3
    assert yesterday["rank_view_uv"] == 1
    assert today["takeaway_uv"] == 2
    assert today["takeaway_rate"] == round(2 / 3, 4)
    assert yesterday["takeaway_rate"] == 0.0
    assert summary["totals"]["rank_view_uv"] == 4
    assert summary["totals"]["takeaway_uv"] == 2
    actions = {a["action"]: a["count"] for a in summary["actions"]}
    assert actions == {
        "rank_view": 5,
        "share_poster_rank": 1,
        "copy_link_rank": 1,
    }


def test_summary_window_clamped(session):
    _seed_summary_events(session)
    summary = track_daily_summary(session, now=NOW, days=999)
    assert summary["window_days"] == 90


# --- 端点 ------------------------------------------------------------------


def test_track_endpoint_records(session, client):
    resp = client.post(
        "/api/v1/track",
        json={
            "action": "rank_view",
            "session_key": "session-abc",
            "city": "成都",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"recorded": True}
    row = session.scalar(select(TrackEvent).order_by(TrackEvent.id.desc()))
    assert row.action == "rank_view"
    assert row.session_key == "session-abc"
    assert row.city == "成都"
    assert row.ip_hash is not None  # TestClient 带 client host，落入哈希


def test_track_endpoint_unknown_action_422(session, client):
    resp = client.post("/api/v1/track", json={"action": "nope"})
    assert resp.status_code == 422


def test_track_endpoint_rate_limited(session, client, monkeypatch):
    monkeypatch.setattr(app_main.settings, "track_rate_limit_max", 1)
    assert client.post("/api/v1/track", json={"action": "rank_view"}).status_code == 200
    assert (
        client.post("/api/v1/track", json={"action": "rank_view"}).status_code == 429
    )


def test_track_summary_requires_admin(session, client):
    resp = client.get("/api/v1/admin/track/summary")
    assert resp.status_code == 401


def test_track_summary_with_admin(session, client):
    _seed_summary_events(session)
    set_admin_password(
        session, "admin", "super-secret-pw", settings=app_main.settings
    )
    login = client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "super-secret-pw"}
    )
    token = login.json()["data"]["token"]
    resp = client.get(
        "/api/v1/admin/track/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["window_days"] == 14
    assert body["totals"]["takeaway_uv"] == 2
    assert session.scalar(select(func.count(TrackEvent.id))) == 7
