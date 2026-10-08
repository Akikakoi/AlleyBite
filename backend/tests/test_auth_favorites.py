"""C 端账号与收藏单测（文档 10.2 账号体系 / 8.1 favorites / V2.0）。

覆盖：注册/登录换令牌、用户名/口令校验、令牌受众隔离（后台令牌不可冒充 C 端）、
用户态鉴权拦截、收藏幂等增删查、店铺不存在 404、收藏列表摘要字段。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.core.config import Settings
from app.db import get_session
from app.db.base import Base
from app.db.models import AdminUser, City, Restaurant, User
from app.main import app
from app.services.admin_auth import issue_token as issue_admin_token
from app.services.user_auth import (
    UserAuthError,
    authenticate_user,
    issue_user_token,
    register_user,
    validate_username,
    verify_user_token,
)


def make_settings(**overrides) -> Settings:
    base = dict(
        llm_mock=True,
        llm_api_key="",
        user_token_secret="unit-user-secret",
        user_password_iterations=1000,
    )
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
def restaurant(session) -> Restaurant:
    city = City(name="成都")
    session.add(city)
    session.flush()
    row = Restaurant(city_id=city.id, name="明婷饭店", name_norm="明婷饭店")
    session.add(row)
    session.commit()
    return row


def register_and_login(client, username="foodie", password="secret66") -> dict:
    resp = client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- 注册 / 登录 -------------------------------------------------------------


def test_register_then_me(client):
    data = register_and_login(client)
    assert data["username"] == "foodie"
    assert data["token"] and data["expires_at"]

    me = client.get("/api/v1/auth/me", headers=auth_headers(data["token"]))
    assert me.status_code == 200
    assert me.json()["data"] == {"username": "foodie"}


def test_register_duplicate_username_conflict(client):
    register_and_login(client, username="foodie")
    resp = client.post(
        "/api/v1/auth/register", json={"username": "foodie", "password": "secret66"}
    )
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "username",
    ["a", "", "has space", "x" * 33, "bad!name"],
)
def test_register_invalid_username_422(client, username):
    resp = client.post(
        "/api/v1/auth/register", json={"username": username, "password": "secret66"}
    )
    assert resp.status_code == 422


def test_login_success_and_wrong_password(client):
    register_and_login(client, username="foodie", password="secret66")
    ok = client.post(
        "/api/v1/auth/login", json={"username": "foodie", "password": "secret66"}
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["username"] == "foodie"

    bad = client.post(
        "/api/v1/auth/login", json={"username": "foodie", "password": "wrongpw"}
    )
    assert bad.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get(
        "/api/v1/auth/me", headers=auth_headers("not.a.token")
    ).status_code == 401


# --- 令牌：受众隔离与函数级校验 ----------------------------------------------


def test_user_token_audience_isolation(session):
    settings = make_settings()
    user = register_user(session, "foodie", "secret66", settings=settings)
    token, _ = issue_user_token(user, settings=settings)

    payload = verify_user_token(token, settings=settings)
    assert payload["aud"] == "alleybite-user"
    assert payload["sub"] == "foodie"

    # C 端令牌不能当后台令牌用，反之亦然
    from app.services.admin_auth import AdminAuthError, verify_token

    with pytest.raises(AdminAuthError):
        verify_token(token, settings=settings)

    admin_user = AdminUser(username="op", password_hash="x", role="operator")
    admin_token, _ = issue_admin_token(admin_user, settings=settings)
    with pytest.raises(UserAuthError):
        verify_user_token(admin_token, settings=settings)


def test_authenticate_user_rejects_bad_password(session):
    register_user(session, "foodie", "secret66", settings=make_settings())
    assert authenticate_user(session, "foodie", "secret66") is not None
    with pytest.raises(UserAuthError):
        authenticate_user(session, "foodie", "wrongpw")
    with pytest.raises(UserAuthError):
        authenticate_user(session, "ghost", "secret66")


def test_validate_username_strips_and_rejects():
    assert validate_username("  foodie ") == "foodie"
    assert validate_username("川味侦察兵") == "川味侦察兵"
    with pytest.raises(ValueError):
        validate_username("a")


# --- 收藏 -------------------------------------------------------------------


def test_favorite_add_list_remove_idempotent(client, restaurant):
    token = register_and_login(client)["token"]
    headers = auth_headers(token)

    first = client.post(
        "/api/v1/favorites", json={"restaurant_id": restaurant.id}, headers=headers
    )
    assert first.status_code == 200
    assert first.json()["data"] == {
        "restaurant_id": restaurant.id,
        "favorited": True,
        "created": True,
    }

    again = client.post(
        "/api/v1/favorites", json={"restaurant_id": restaurant.id}, headers=headers
    )
    assert again.json()["data"]["created"] is False

    status = client.get(
        f"/api/v1/favorites/{restaurant.id}", headers=headers
    )
    assert status.json()["data"]["favorited"] is True

    listing = client.get("/api/v1/favorites", headers=headers)
    items = listing.json()["data"]
    assert len(items) == 1
    assert items[0]["restaurant_id"] == restaurant.id
    assert items[0]["name"] == "明婷饭店"
    assert items[0]["city"] == "成都"
    assert items[0]["favorited_at"]

    removed = client.delete(
        f"/api/v1/favorites/{restaurant.id}", headers=headers
    )
    assert removed.json()["data"] == {"restaurant_id": restaurant.id, "favorited": False}

    # 重复删除幂等
    removed_again = client.delete(
        f"/api/v1/favorites/{restaurant.id}", headers=headers
    )
    assert removed_again.status_code == 200

    listing = client.get("/api/v1/favorites", headers=headers)
    assert listing.json()["data"] == []


def test_favorite_requires_auth(client, restaurant):
    resp = client.post(
        "/api/v1/favorites", json={"restaurant_id": restaurant.id}
    )
    assert resp.status_code == 401
    assert client.get("/api/v1/favorites").status_code == 401


def test_favorite_missing_restaurant_404(client):
    token = register_and_login(client)["token"]
    resp = client.post(
        "/api/v1/favorites", json={"restaurant_id": 99999}, headers=auth_headers(token)
    )
    assert resp.status_code == 404


def test_favorite_list_orders_by_newest(client, session, restaurant):
    city = City(name="重庆")
    session.add(city)
    session.flush()
    other = Restaurant(city_id=city.id, name="板凳面", name_norm="板凳面")
    session.add(other)
    session.commit()

    token = register_and_login(client)["token"]
    headers = auth_headers(token)
    client.post("/api/v1/favorites", json={"restaurant_id": restaurant.id}, headers=headers)
    client.post("/api/v1/favorites", json={"restaurant_id": other.id}, headers=headers)

    items = client.get("/api/v1/favorites", headers=headers).json()["data"]
    assert [i["restaurant_id"] for i in items] == [other.id, restaurant.id]
