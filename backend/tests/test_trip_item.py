"""行前清单单测（方向六批 3）：勾店/核销幂等、trip_save/trip_check 带走事件、摘要列表。

覆盖：勾店/核销/移除/状态/清单端点、店铺或清单条目不存在 404、
幂等语义（重复勾店/重复核销不重复落事件）、
事件入库（created/updated=True 才记，city 取店铺所属城）、
埋点限流不阻塞勾店、trip 动作计入带走率汇总。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.core.config import Settings
from app.db import get_session
from app.db.base import Base
from app.db.models import City, Restaurant, TrackEvent, TripItem
from app.main import app
from app.services.track_service import record_track_event, track_daily_summary


def make_settings(**overrides) -> Settings:
    base = {
        "llm_mock": True,
        "llm_api_key": "",
        "user_token_secret": "unit-user-secret",
        "user_password_iterations": 1000,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture(autouse=True)
def _fast_user_auth(monkeypatch):
    """固定 C 端令牌密钥并降低 PBKDF2 迭代，避免受本机 .env 影响且加速测试。"""
    monkeypatch.setattr(app_main.settings, "user_token_secret", "unit-user-secret")
    monkeypatch.setattr(app_main.settings, "user_password_iterations", 1_000)


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
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def city(session):
    city = City(name="成都")
    session.add(city)
    session.commit()
    return city


@pytest.fixture
def restaurants(session, city):
    a = Restaurant(city_id=city.id, name="甲店", name_norm="甲店", status="active")
    b = Restaurant(city_id=city.id, name="乙店", name_norm="乙店", status="active")
    session.add_all([a, b])
    session.commit()
    return a, b


def auth_headers(client, username="foodie") -> dict:
    resp = client.post(
        "/api/v1/auth/register", json={"username": username, "password": "secret66"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


# --- 勾店 ----------------------------------------------------------------------


def test_add_creates_planned_and_trip_save_event(session, client, restaurants):
    a, _ = restaurants
    headers = auth_headers(client)
    resp = client.post(
        "/api/v1/trip-items",
        json={"restaurant_id": a.id, "session_key": "session-abc"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "restaurant_id": a.id,
        "saved": True,
        "created": True,
        "status": "planned",
    }

    item = session.scalar(select(TripItem))
    assert item is not None
    assert item.status == "planned"
    assert item.visited_at is None

    event = session.scalar(select(TrackEvent).where(TrackEvent.action == "trip_save"))
    assert event is not None
    assert event.restaurant_id == a.id
    assert event.city == "成都"
    assert event.session_key == "session-abc"
    assert event.user_id == item.user_id


def test_add_idempotent_no_extra_event(session, client, restaurants):
    a, _ = restaurants
    headers = auth_headers(client)
    client.post("/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers)
    resp = client.post(
        "/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["created"] is False
    assert session.scalar(select(func.count(TripItem.id))) == 1
    assert (
        session.scalar(select(func.count(TrackEvent.id)).where(TrackEvent.action == "trip_save"))
        == 1
    )


def test_add_does_not_reset_visited_status(session, client, restaurants):
    a, _ = restaurants
    headers = auth_headers(client)
    client.post("/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers)
    client.post(f"/api/v1/trip-items/{a.id}/visit", json={}, headers=headers)
    resp = client.post(
        "/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "visited"


def test_add_unknown_restaurant_404(session, client, city):
    headers = auth_headers(client)
    resp = client.post("/api/v1/trip-items", json={"restaurant_id": 999}, headers=headers)
    assert resp.status_code == 404
    assert session.scalar(select(func.count(TripItem.id))) == 0


# --- 核销 ----------------------------------------------------------------------


def test_visit_marks_visited_and_trip_check_event(session, client, restaurants):
    a, _ = restaurants
    headers = auth_headers(client)
    client.post(
        "/api/v1/trip-items", json={"restaurant_id": a.id, "session_key": "s1"},
        headers=headers,
    )
    resp = client.post(
        f"/api/v1/trip-items/{a.id}/visit",
        json={"session_key": "s1"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "restaurant_id": a.id,
        "status": "visited",
        "visited": True,
        "updated": True,
    }

    item = session.scalar(select(TripItem))
    assert item.status == "visited"
    assert item.visited_at is not None

    event = session.scalar(select(TrackEvent).where(TrackEvent.action == "trip_check"))
    assert event is not None
    assert event.restaurant_id == a.id
    assert event.city == "成都"
    assert event.user_id == item.user_id


def test_visit_idempotent_no_extra_event(session, client, restaurants):
    a, _ = restaurants
    headers = auth_headers(client)
    client.post("/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers)
    client.post(f"/api/v1/trip-items/{a.id}/visit", json={}, headers=headers)
    resp = client.post(f"/api/v1/trip-items/{a.id}/visit", json={}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["updated"] is False
    assert (
        session.scalar(select(func.count(TrackEvent.id)).where(TrackEvent.action == "trip_check"))
        == 1
    )


def test_visit_missing_item_404(session, client, restaurants):
    a, _ = restaurants
    headers = auth_headers(client)
    resp = client.post(f"/api/v1/trip-items/{a.id}/visit", json={}, headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "清单中没有这家店"


# --- 移除 / 状态 / 清单 ---------------------------------------------------------


def test_remove_idempotent(session, client, restaurants):
    a, _ = restaurants
    headers = auth_headers(client)
    client.post("/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers)
    first = client.delete(f"/api/v1/trip-items/{a.id}", headers=headers)
    assert first.status_code == 200
    assert first.json()["data"] == {"restaurant_id": a.id, "saved": False}
    again = client.delete(f"/api/v1/trip-items/{a.id}", headers=headers)
    assert again.status_code == 200
    assert again.json()["data"]["saved"] is False


def test_status_endpoint(session, client, restaurants):
    a, _ = restaurants
    headers = auth_headers(client)
    assert client.get(f"/api/v1/trip-items/{a.id}", headers=headers).json()["data"] == {
        "restaurant_id": a.id,
        "saved": False,
        "status": None,
    }
    client.post("/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers)
    assert client.get(f"/api/v1/trip-items/{a.id}", headers=headers).json()["data"] == {
        "restaurant_id": a.id,
        "saved": True,
        "status": "planned",
    }


def test_list_returns_summary_ordered_by_saved_at(session, client, restaurants):
    a, b = restaurants
    headers = auth_headers(client)
    client.post("/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers)
    client.post("/api/v1/trip-items", json={"restaurant_id": b.id}, headers=headers)
    client.post(f"/api/v1/trip-items/{b.id}/visit", json={}, headers=headers)

    data = client.get("/api/v1/trip-items", headers=headers).json()["data"]
    assert len(data) == 2
    # 勾店时间倒序：后勾的乙店在前
    assert [row["name"] for row in data] == ["乙店", "甲店"]
    row_b, row_a = data
    assert row_b["city"] == "成都"
    assert row_b["status"] == "visited"
    assert row_b["visited_at"]
    assert row_b["saved_at"]
    assert row_a["status"] == "planned"
    assert row_a["visited_at"] is None
    assert {"restaurant_id", "area", "cuisine", "avg_price", "address"} <= set(row_a)


def test_list_empty(session, client):
    headers = auth_headers(client)
    assert client.get("/api/v1/trip-items", headers=headers).json()["data"] == []


# --- 鉴权与埋点降级 --------------------------------------------------------------


def test_endpoints_require_auth(session, client, restaurants):
    a, _ = restaurants
    assert client.get("/api/v1/trip-items").status_code == 401
    assert client.post("/api/v1/trip-items", json={"restaurant_id": a.id}).status_code == 401
    assert client.post(f"/api/v1/trip-items/{a.id}/visit", json={}).status_code == 401
    assert client.delete(f"/api/v1/trip-items/{a.id}").status_code == 401
    assert client.get(f"/api/v1/trip-items/{a.id}").status_code == 401


def test_trip_save_rate_limit_does_not_block_add(session, client, restaurants, monkeypatch):
    a, _ = restaurants
    monkeypatch.setattr(app_main.settings, "track_rate_limit_max", 1)
    headers = auth_headers(client)
    # 同 IP 先消耗掉唯一一次埋点配额
    assert client.post("/api/v1/track", json={"action": "rank_view"}).status_code == 200

    resp = client.post("/api/v1/trip-items", json={"restaurant_id": a.id}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["created"] is True
    assert session.scalar(select(func.count(TripItem.id))) == 1
    assert session.scalar(select(func.count(TrackEvent.id))) == 1  # 仅 rank_view


def test_trip_actions_count_as_takeaway(session):
    record_track_event(
        session,
        action="trip_save",
        settings=make_settings(),
        session_key="s1",
        city="成都",
    )
    record_track_event(
        session,
        action="trip_check",
        settings=make_settings(),
        session_key="s1",
        city="成都",
    )
    summary = track_daily_summary(session)
    actions = {a["action"]: a["count"] for a in summary["actions"]}
    assert actions["trip_save"] == 1
    assert actions["trip_check"] == 1
    assert summary["totals"]["takeaway_uv"] == 1
