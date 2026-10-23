"""按城收藏包单测（方向六批 2）：订阅幂等、城市校验、pack_save 带走事件、快照摘要。

覆盖：订阅/退订/状态/列表端点、城市未收录 404、幂等语义、
pack_save 事件入库（created=True 才记）、埋点限流不阻塞订阅、
列表附最新快照元信息（rank_count/maturity/generated_at）。
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
from app.db.models import City, CityPack, RankSnapshot, TrackEvent
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
def cities(session):
    session.add(City(name="成都"))
    session.add(City(name="广州"))
    session.commit()
    return session


def auth_headers(client, username="foodie") -> dict:
    resp = client.post(
        "/api/v1/auth/register", json={"username": username, "password": "secret66"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


# --- 订阅 / 退订 --------------------------------------------------------------


def test_subscribe_creates_pack_and_pack_save_event(session, client, cities):
    headers = auth_headers(client)
    resp = client.post(
        "/api/v1/city-packs",
        json={"city": "成都", "session_key": "session-abc"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"city": "成都", "packed": True, "created": True}

    pack = session.scalar(select(CityPack))
    assert pack is not None and pack.city == "成都"

    event = session.scalar(
        select(TrackEvent).where(TrackEvent.action == "pack_save")
    )
    assert event is not None
    assert event.city == "成都"
    assert event.session_key == "session-abc"
    assert event.user_id == pack.user_id


def test_subscribe_idempotent_no_extra_event(session, client, cities):
    headers = auth_headers(client)
    client.post("/api/v1/city-packs", json={"city": "成都"}, headers=headers)
    resp = client.post("/api/v1/city-packs", json={"city": "成都"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["created"] is False
    assert session.scalar(select(func.count(CityPack.id))) == 1
    assert (
        session.scalar(select(func.count(TrackEvent.id)).where(TrackEvent.action == "pack_save"))
        == 1
    )


def test_subscribe_strips_city_name(session, client, cities):
    headers = auth_headers(client)
    resp = client.post("/api/v1/city-packs", json={"city": "  成都  "}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["city"] == "成都"


def test_subscribe_unknown_city_404(session, client, cities):
    headers = auth_headers(client)
    resp = client.post("/api/v1/city-packs", json={"city": "东京"}, headers=headers)
    assert resp.status_code == 404
    assert session.scalar(select(func.count(CityPack.id))) == 0


def test_unsubscribe_idempotent(session, client, cities):
    headers = auth_headers(client)
    client.post("/api/v1/city-packs", json={"city": "成都"}, headers=headers)
    first = client.delete("/api/v1/city-packs/成都", headers=headers)
    assert first.status_code == 200
    assert first.json()["data"] == {"city": "成都", "packed": False}
    assert session.scalar(select(func.count(CityPack.id))) == 0

    again = client.delete("/api/v1/city-packs/成都", headers=headers)
    assert again.status_code == 200
    assert again.json()["data"]["packed"] is False


def test_status_endpoint(session, client, cities):
    headers = auth_headers(client)
    assert client.get("/api/v1/city-packs/成都", headers=headers).json()["data"] == {
        "city": "成都",
        "packed": False,
    }
    client.post("/api/v1/city-packs", json={"city": "成都"}, headers=headers)
    assert client.get("/api/v1/city-packs/成都", headers=headers).json()["data"] == {
        "city": "成都",
        "packed": True,
    }


# --- 列表摘要 -----------------------------------------------------------------


def test_list_returns_snapshot_meta(session, client, cities):
    chengdu = session.scalar(select(City).where(City.name == "成都"))
    guangzhou = session.scalar(select(City).where(City.name == "广州"))
    session.add(RankSnapshot(city_id=chengdu.id, items=[{"rank": i} for i in range(3)]))
    session.add(
        RankSnapshot(city_id=guangzhou.id, items=[{"rank": i} for i in range(30)])
    )
    session.commit()

    headers = auth_headers(client)
    client.post("/api/v1/city-packs", json={"city": "成都"}, headers=headers)
    client.post("/api/v1/city-packs", json={"city": "广州"}, headers=headers)

    data = client.get("/api/v1/city-packs", headers=headers).json()["data"]
    assert len(data) == 2
    by_city = {row["city"]: row for row in data}
    assert by_city["成都"]["rank_count"] == 3
    assert by_city["成都"]["maturity"] == "collecting"
    assert by_city["成都"]["generated_at"]
    assert by_city["成都"]["packed_at"]
    assert by_city["广州"]["rank_count"] == 30
    assert by_city["广州"]["maturity"] == "deep"
    # 订阅时间倒序：后订阅的广州在前
    assert [row["city"] for row in data] == ["广州", "成都"]


def test_list_without_snapshot_is_collecting(session, client, cities):
    headers = auth_headers(client)
    client.post("/api/v1/city-packs", json={"city": "成都"}, headers=headers)
    data = client.get("/api/v1/city-packs", headers=headers).json()["data"]
    assert data[0]["rank_count"] == 0
    assert data[0]["maturity"] == "collecting"
    assert data[0]["generated_at"] is None


# --- 鉴权与埋点降级 ------------------------------------------------------------


def test_endpoints_require_auth(session, client, cities):
    assert client.get("/api/v1/city-packs").status_code == 401
    assert client.post("/api/v1/city-packs", json={"city": "成都"}).status_code == 401
    assert client.delete("/api/v1/city-packs/成都").status_code == 401
    assert client.get("/api/v1/city-packs/成都").status_code == 401


def test_pack_save_rate_limit_does_not_block_subscription(session, client, cities, monkeypatch):
    monkeypatch.setattr(app_main.settings, "track_rate_limit_max", 1)
    headers = auth_headers(client)
    # 同 IP 先消耗掉唯一一次埋点配额
    assert client.post("/api/v1/track", json={"action": "rank_view"}).status_code == 200

    resp = client.post("/api/v1/city-packs", json={"city": "成都"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["created"] is True
    assert session.scalar(select(func.count(CityPack.id))) == 1
    assert session.scalar(select(func.count(TrackEvent.id))) == 1  # 仅 rank_view


def test_pack_save_counts_as_takeaway(session):
    record_track_event(
        session,
        action="pack_save",
        settings=make_settings(),
        session_key="s1",
        city="成都",
    )
    summary = track_daily_summary(session)
    actions = {a["action"]: a["count"] for a in summary["actions"]}
    assert actions["pack_save"] == 1
    assert summary["totals"]["takeaway_uv"] == 1
