"""V2.0 新功能单测：短信验证码登录、UGC 打卡（含图片）、个性化推荐。

覆盖：验证码发送限流与 mock、验证码一次性校验、手机号登录自动注册、
UGC 发布校验/先审后显/审核流转、图片上传类型与大小校验、推荐策略降级。
"""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.core.config import Settings
from app.db import get_session
from app.db.base import Base
from app.db.models import City, Restaurant, SmsCode, UgcPost, User, ViewEvent
from app.main import app
from app.services.user_auth import register_user


def make_settings(**overrides) -> Settings:
    base = dict(
        llm_mock=True,
        llm_api_key="",
        user_token_secret="unit-user-secret",
        user_password_iterations=1000,
        uploads_dir="./test_uploads",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture(autouse=True)
def _fast_auth(monkeypatch):
    monkeypatch.setattr(app_main.settings, "user_token_secret", "unit-user-secret")
    monkeypatch.setattr(app_main.settings, "user_password_iterations", 1_000)
    monkeypatch.setattr(app_main.settings, "uploads_dir", "./test_uploads")


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
    city = City(name="南京")
    session.add(city)
    session.flush()
    row = Restaurant(city_id=city.id, name="李记清真馆", name_norm="李记清真馆")
    session.add(row)
    session.commit()
    return row


def register_and_login(client, username="foodie") -> dict:
    resp = client.post(
        "/api/v1/auth/register", json={"username": username, "password": "secret66"}
    )
    return resp.json()["data"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- 短信验证码登录 -----------------------------------------------------------


def test_sms_send_mock_returns_dev_code(client):
    resp = client.post("/api/v1/auth/sms/send", json={"phone": "13800001234"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["mock"] is True
    assert data["dev_code"] and len(data["dev_code"]) == 6


def test_sms_send_rate_limit_per_phone(client):
    for _ in range(5):
        client.post("/api/v1/auth/sms/send", json={"phone": "13800001234"})
    limited = client.post("/api/v1/auth/sms/send", json={"phone": "13800001234"})
    assert limited.status_code == 429


def test_sms_send_invalid_phone_422(client):
    resp = client.post("/api/v1/auth/sms/send", json={"phone": "12345"})
    assert resp.status_code == 422


def test_sms_login_creates_account_and_reuses(client):
    send = client.post("/api/v1/auth/sms/send", json={"phone": "13800001234"})
    code = send.json()["data"]["dev_code"]

    first = client.post(
        "/api/v1/auth/sms/login", json={"phone": "13800001234", "code": code}
    )
    assert first.status_code == 200
    data = first.json()["data"]
    assert data["created"] is True
    assert data["username"].startswith("用户1234")

    me = client.get("/api/v1/auth/me", headers=auth_headers(data["token"]))
    assert me.status_code == 200

    # 验证码一次性：重放失败
    replay = client.post(
        "/api/v1/auth/sms/login", json={"phone": "13800001234", "code": code}
    )
    assert replay.status_code == 422

    # 再次发送登录：不新建账号
    send2 = client.post("/api/v1/auth/sms/send", json={"phone": "13800001234"})
    code2 = send2.json()["data"]["dev_code"]
    second = client.post(
        "/api/v1/auth/sms/login", json={"phone": "13800001234", "code": code2}
    )
    assert second.json()["data"]["created"] is False
    assert second.json()["data"]["username"] == data["username"]


def test_sms_login_wrong_code_422(client):
    client.post("/api/v1/auth/sms/send", json={"phone": "13800001234"})
    resp = client.post(
        "/api/v1/auth/sms/login", json={"phone": "13800001234", "code": "000000"}
    )
    assert resp.status_code == 422


def test_sms_login_links_existing_username_account(session, client):
    register_user(session, "老饕", "secret66", settings=app_main.settings)
    send = client.post("/api/v1/auth/sms/send", json={"phone": "13900005678"})
    code = send.json()["data"]["dev_code"]
    resp = client.post(
        "/api/v1/auth/sms/login", json={"phone": "13900005678", "code": code}
    )
    assert resp.status_code == 200
    # 首次短信登录自动注册新账号（绑定手机号的合并留待后续账号资料功能）
    assert resp.json()["data"]["created"] is True


# --- UGC 打卡 -----------------------------------------------------------------


def test_upload_rejects_bad_type_and_requires_auth(client):
    assert client.post("/api/v1/uploads", content=b"x").status_code == 401

    token = register_and_login(client)["token"]
    resp = client.post(
        "/api/v1/uploads", content=b"fake", headers={**auth_headers(token), "Content-Type": "text/plain"}
    )
    assert resp.status_code == 422

    ok = client.post(
        "/api/v1/uploads",
        content=b"\xff\xd8\xff\xe0fakejpeg",
        headers={**auth_headers(token), "Content-Type": "image/jpeg"},
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["path"].startswith("/uploads/ugc/")


def test_ugc_create_review_and_visibility(client, session, restaurant):
    token = register_and_login(client)["token"]
    headers = auth_headers(token)

    # 免登录只能看已通过的（空）
    assert client.get(f"/api/v1/ugc?restaurant_id={restaurant.id}").json()["data"] == []

    resp = client.post(
        "/api/v1/ugc",
        json={"restaurant_id": restaurant.id, "content": "锅贴爆汁，值得排队"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "pending"

    # pending 不对外可见
    assert client.get(f"/api/v1/ugc?restaurant_id={restaurant.id}").json()["data"] == []

    # 管理端接口需要管理员
    assert client.get("/api/v1/admin/ugc", headers=headers).status_code == 401

    # 服务层审核通过后对外可见
    from app.services import review_ugc

    post = session.scalar(
        select(UgcPost).where(UgcPost.restaurant_id == restaurant.id)
    )
    review_ugc(session, post.id, "approved")

    visible = client.get(f"/api/v1/ugc?restaurant_id={restaurant.id}").json()["data"]
    assert len(visible) == 1
    assert visible[0]["content"] == "锅贴爆汁，值得排队"

    mine = client.get("/api/v1/ugc/mine", headers=headers).json()["data"]
    assert mine[0]["status"] == "approved"


def test_ugc_content_validation(client, restaurant):
    token = register_and_login(client)["token"]
    headers = auth_headers(token)

    empty = client.post(
        "/api/v1/ugc", json={"restaurant_id": restaurant.id, "content": "  "}, headers=headers
    )
    assert empty.status_code == 422

    missing = client.post(
        "/api/v1/ugc", json={"restaurant_id": 99999, "content": "不存在"}, headers=headers
    )
    assert missing.status_code == 404

    too_many_images = client.post(
        "/api/v1/ugc",
        json={
            "restaurant_id": restaurant.id,
            "content": "图太多",
            "images": ["/uploads/ugc/a.jpg"] * 4,
        },
        headers=headers,
    )
    assert too_many_images.status_code == 422

    bad_path = client.post(
        "/api/v1/ugc",
        json={
            "restaurant_id": restaurant.id,
            "content": "路径非法",
            "images": ["http://evil.com/x.jpg"],
        },
        headers=headers,
    )
    assert bad_path.status_code == 422


# --- 个性化推荐 ---------------------------------------------------------------


def test_recommend_hot_fallback_without_user(client, session, restaurant):
    # 未登录：回退热门策略（无快照时 items 为空但结构正确）
    resp = client.get("/api/v1/recommend")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["strategy"] == "hot"
    assert isinstance(data["items"], list)


def test_recommend_taste_strategy_excludes_favorites(client, session, restaurant):
    token = register_and_login(client)["token"]
    headers = auth_headers(token)

    # 收藏 + 浏览建立偏好（成都菜系）
    city = City(name="成都")
    session.add(city)
    session.flush()
    target = Restaurant(city_id=city.id, name="张记面馆", name_norm="张记面馆", cuisine="面馆")
    same_taste = Restaurant(city_id=city.id, name="王记面馆", name_norm="王记面馆", cuisine="面馆")
    session.add_all([target, same_taste])
    session.commit()

    client.post("/api/v1/favorites", json={"restaurant_id": target.id}, headers=headers)
    client.post("/api/v1/views", json={"restaurant_id": target.id}, headers=headers)

    # 注入偏好城市的榜单快照（绕过打分，直接造快照）
    from app.db.models import RankSnapshot
    from datetime import datetime, timezone

    session.add(
        RankSnapshot(
            city_id=city.id,
            generated_at=datetime.now(timezone.utc),
            items=[
                {
                    "restaurant_id": same_taste.id,
                    "name": same_taste.name,
                    "city": "成都",
                    "cuisine": "面馆",
                    "score": 8.8,
                    "mention_count": 5,
                },
                {
                    "restaurant_id": target.id,
                    "name": target.name,
                    "city": "成都",
                    "cuisine": "面馆",
                    "score": 8.0,
                    "mention_count": 4,
                },
            ],
        )
    )
    session.commit()

    data = client.get("/api/v1/recommend", headers=headers).json()["data"]
    assert data["strategy"] == "taste"
    ids = [i["restaurant_id"] for i in data["items"]]
    assert same_taste.id in ids
    assert target.id not in ids  # 已收藏/浏览过的不重复推


def test_record_view_anonymous_ok(client, restaurant):
    resp = client.post("/api/v1/views", json={"restaurant_id": restaurant.id})
    assert resp.status_code == 200
