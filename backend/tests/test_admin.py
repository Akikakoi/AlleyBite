"""管理后台单测（文档 9.5）：独立鉴权、审计日志、店铺审核、采集监控。

覆盖：口令哈希/令牌签名、登录与鉴权拦截、灰区归并确认/驳回、店铺合并/别名/屏蔽、
采集监控汇总与手动触发、工单状态流转、审计日志留痕。
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.core.config import InsecureConfigError, Settings, assert_secure_secrets
from app.db import get_session
from app.db.base import Base
from app.db.models import (
    AdminAuditLog,
    AdminUser,
    AlignmentReview,
    City,
    Feedback,
    JobRun,
    Mention,
    RawContent,
    Restaurant,
)
from app.main import app
from app.services.admin_auth import (
    AdminAuthError,
    authenticate,
    ensure_bootstrap_admin,
    hash_password,
    issue_token,
    set_admin_password,
    verify_password,
    verify_token,
)
from app.services.alignment import (
    add_manual_alias,
    merge_restaurants,
    reject_review,
    set_restaurant_status,
)
from app.services.normalize import normalize_shop_name

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def make_settings(**overrides) -> Settings:
    base = dict(
        llm_mock=True,
        llm_api_key="",
        admin_token_secret="unit-secret",
        admin_password_iterations=1000,
        admin_username="root",
        admin_password="",
    )
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
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def admin_headers(client, session) -> dict:
    """建号并走真实登录接口取令牌。"""
    set_admin_password(session, "op", "pw", settings=app_main.settings)
    resp = client.post("/api/v1/admin/login", json={"username": "op", "password": "pw"})
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


# --- 口令与令牌 -------------------------------------------------------------


def test_password_hash_roundtrip_and_rejects_others():
    stored = hash_password("s3cret", iterations=1000, salt="ab" * 16)

    assert stored.startswith("pbkdf2_sha256$1000$")
    assert "s3cret" not in stored
    assert verify_password("s3cret", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret", "garbage")
    assert not verify_password("s3cret", "md5$1$aa$bb")


def test_token_roundtrip_and_tamper_detection():
    settings = make_settings()
    user = AdminUser(username="op", password_hash="x", role="operator")
    token, expires_at = issue_token(user, settings=settings, now=NOW)

    payload = verify_token(token, settings=settings, now=NOW)
    assert payload["sub"] == "op"
    assert payload["aud"] == "alleybite-admin"
    assert expires_at > NOW

    body, signature = token.split(".")
    with pytest.raises(AdminAuthError):
        verify_token(f"{body}.{signature[:-2]}xx", settings=settings, now=NOW)
    with pytest.raises(AdminAuthError):
        verify_token(token, settings=make_settings(admin_token_secret="other"), now=NOW)
    with pytest.raises(AdminAuthError):
        verify_token("nonsense", settings=settings, now=NOW)
    with pytest.raises(AdminAuthError):
        verify_token(None, settings=settings, now=NOW)


def test_token_expiry_rejected():
    settings = make_settings(admin_token_ttl_minutes=-1)
    user = AdminUser(username="op", password_hash="x")
    token, _ = issue_token(user, settings=settings, now=NOW)
    with pytest.raises(AdminAuthError):
        verify_token(token, settings=settings, now=NOW)


def test_authenticate_checks_password_and_active_flag(session):
    set_admin_password(session, "op", "pw", settings=make_settings())

    assert authenticate(session, "op", "pw").username == "op"
    with pytest.raises(AdminAuthError):
        authenticate(session, "op", "bad")
    with pytest.raises(AdminAuthError):
        authenticate(session, "ghost", "pw")

    user = session.scalar(select(AdminUser).where(AdminUser.username == "op"))
    user.is_active = False
    session.flush()
    with pytest.raises(AdminAuthError):
        authenticate(session, "op", "pw")


def test_ensure_bootstrap_admin_idempotent_and_skips_without_password(session):
    assert ensure_bootstrap_admin(session, settings=make_settings()) is None

    settings = make_settings(admin_username="root", admin_password="init-pw")
    first = ensure_bootstrap_admin(session, settings=settings, now=NOW)
    again = ensure_bootstrap_admin(session, settings=settings, now=NOW)

    assert first is not None and first.id == again.id
    assert first.role == "superadmin"
    assert session.scalars(select(AdminUser)).all() == [first]


# --- 登录与鉴权拦截 ---------------------------------------------------------


def test_login_success_updates_last_login_and_audits(client, session, admin_headers):
    user = session.scalar(select(AdminUser).where(AdminUser.username == "op"))
    assert user.last_login_at is not None
    actions = [r.action for r in session.scalars(select(AdminAuditLog)).all()]
    assert "admin.login" in actions


def test_login_rejects_wrong_password(client, session):
    set_admin_password(session, "op", "pw", settings=app_main.settings)
    resp = client.post("/api/v1/admin/login", json={"username": "op", "password": "bad"})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nonsense"},
        {"Authorization": "Basic abc"},
    ],
)
def test_admin_endpoints_reject_bad_credentials(client, headers):
    assert client.get("/api/v1/admin/me", headers=headers).status_code == 401
    assert client.get("/api/v1/admin/feedback", headers=headers).status_code == 401


def test_admin_me_returns_identity(client, admin_headers):
    data = client.get("/api/v1/admin/me", headers=admin_headers).json()["data"]
    assert data == {"username": "op", "role": "operator"}


def test_expired_token_rejected(client, session, monkeypatch):
    set_admin_password(session, "op", "pw", settings=app_main.settings)
    monkeypatch.setattr(app_main.settings, "admin_token_ttl_minutes", -1)
    token = client.post(
        "/api/v1/admin/login", json={"username": "op", "password": "pw"}
    ).json()["data"]["token"]
    monkeypatch.setattr(app_main.settings, "admin_token_ttl_minutes", 120)
    assert (
        client.get("/api/v1/admin/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )


# --- 店铺审核 ---------------------------------------------------------------


def _seed_review(session) -> AlignmentReview:
    city = City(name="成都")
    session.add(city)
    session.flush()
    raw = RawContent(source="seed", raw_text="明婷饭店 脑花豆腐", content_hash="h1", city_hint="成都")
    session.add(raw)
    session.flush()
    mention = Mention(raw_content_id=raw.id, shop_name_raw="明婷")
    session.add(mention)
    session.flush()
    candidate = Restaurant(city_id=city.id, name="明婷饭店", name_norm="明婷饭店")
    session.add(candidate)
    session.flush()
    review = AlignmentReview(
        mention_id=mention.id, candidate_restaurant_id=candidate.id, score=0.7
    )
    session.add(review)
    session.flush()
    return review


def test_reviews_list_confirm_and_audit(client, session, admin_headers):
    review = _seed_review(session)

    listed = client.get("/api/v1/admin/reviews", headers=admin_headers).json()["data"]
    assert listed[0]["id"] == review.id
    assert listed[0]["shop_name_raw"] == "明婷"
    assert listed[0]["candidate_name"] == "明婷饭店"

    resp = client.post(
        f"/api/v1/admin/reviews/{review.id}/confirm", json={}, headers=admin_headers
    )
    assert resp.json()["data"]["status"] == "confirmed"

    mention = session.get(Mention, review.mention_id)
    assert mention.restaurant_id == review.candidate_restaurant_id
    audit = session.scalars(
        select(AdminAuditLog).where(AdminAuditLog.action == "review.confirm")
    ).one()
    assert audit.operator == "op" and audit.target_id == str(review.id)
    assert audit.after["status"] == "confirmed"


def test_review_confirm_unknown_returns_404(client, admin_headers):
    assert (
        client.post("/api/v1/admin/reviews/999/confirm", json={}, headers=admin_headers).status_code
        == 404
    )


def test_review_reject_keeps_mention_unmerged(client, session, admin_headers):
    review = _seed_review(session)
    assert reject_review(session, review.id).status == "rejected"

    mention = session.get(Mention, review.mention_id)
    assert mention.restaurant_id is None
    assert client.get("/api/v1/admin/reviews", headers=admin_headers).json()["data"] == []


# --- 店铺合并 / 别名 / 屏蔽 --------------------------------------------------


def _seed_two_restaurants(session) -> tuple[Restaurant, Restaurant, Mention]:
    city = City(name="成都")
    session.add(city)
    session.flush()
    source = Restaurant(city_id=city.id, name="老明婷", name_norm="老明婷", address=None)
    target = Restaurant(
        city_id=city.id, name="明婷饭店", name_norm="明婷饭店", address="青羊区同心路"
    )
    session.add_all([source, target])
    session.flush()
    raw = RawContent(source="seed", raw_text="x", content_hash="h2", city_hint="成都")
    session.add(raw)
    session.flush()
    mention = Mention(raw_content_id=raw.id, shop_name_raw="老明婷", restaurant_id=source.id)
    session.add(mention)
    session.flush()
    return source, target, mention


def test_merge_restaurants_moves_mentions_and_keeps_alias(session):
    source, target, mention = _seed_two_restaurants(session)

    merged_source, merged_target, moved = merge_restaurants(session, source.id, target.id)

    assert moved == 1
    assert merged_source.status == "merged"
    assert merged_source.merged_into == target.id
    assert session.get(Mention, mention.id).restaurant_id == target.id
    assert "老明婷" in {a.alias for a in merged_target.aliases}


def test_merge_restaurants_rejects_self_and_unknown(session):
    source, target, _ = _seed_two_restaurants(session)
    with pytest.raises(ValueError):
        merge_restaurants(session, source.id, source.id)
    with pytest.raises(LookupError):
        merge_restaurants(session, source.id, 999)


def test_add_manual_alias_dedupes_and_normalizes(session):
    _, target, _ = _seed_two_restaurants(session)

    alias = add_manual_alias(session, target.id, " 明婷 ")
    assert alias is not None and alias.alias_norm == normalize_shop_name("明婷")
    assert add_manual_alias(session, target.id, "明婷") is None
    assert add_manual_alias(session, target.id, target.name) is None
    with pytest.raises(ValueError):
        add_manual_alias(session, target.id, "  ")
    with pytest.raises(LookupError):
        add_manual_alias(session, 999, "x")


def test_set_restaurant_status_only_active_blocked(session):
    source, _, _ = _seed_two_restaurants(session)

    restaurant, before = set_restaurant_status(session, source.id, "blocked")
    assert (before, restaurant.status) == ("active", "blocked")
    with pytest.raises(ValueError):
        set_restaurant_status(session, source.id, "merged")
    with pytest.raises(LookupError):
        set_restaurant_status(session, 999, "blocked")


def test_restaurant_admin_endpoints_flow(client, session, admin_headers):
    source, target, _ = _seed_two_restaurants(session)

    listed = client.get("/api/v1/admin/restaurants", headers=admin_headers).json()["data"]
    assert listed["total"] == 2

    alias_resp = client.post(
        f"/api/v1/admin/restaurants/{target.id}/aliases",
        json={"alias": "明婷"},
        headers=admin_headers,
    )
    assert alias_resp.json()["data"]["created"] is True

    merge_resp = client.post(
        f"/api/v1/admin/restaurants/{source.id}/merge",
        json={"target_id": target.id},
        headers=admin_headers,
    )
    assert merge_resp.json()["data"]["mentions_moved"] == 1

    status_resp = client.post(
        f"/api/v1/admin/restaurants/{target.id}/status",
        json={"status": "blocked"},
        headers=admin_headers,
    )
    assert status_resp.json()["data"]["status"] == "blocked"

    actions = {r.action for r in session.scalars(select(AdminAuditLog)).all()}
    assert {"restaurant.alias_add", "restaurant.merge", "restaurant.set_status"} <= actions


def test_restaurant_alias_conflict_returns_not_created(client, session, admin_headers):
    _, target, _ = _seed_two_restaurants(session)
    client.post(
        f"/api/v1/admin/restaurants/{target.id}/aliases",
        json={"alias": "明婷"},
        headers=admin_headers,
    )
    again = client.post(
        f"/api/v1/admin/restaurants/{target.id}/aliases",
        json={"alias": "明婷"},
        headers=admin_headers,
    )
    assert again.json()["data"]["created"] is False


# --- 生产环境弱密钥强校验（文档 11 章）--------------------------------------


def _prod_settings(**overrides) -> Settings:
    """构造生产配置（debug=False），不读 .env，避免受本地环境影响。"""
    base = {"debug": False, "feedback_ip_salt": "s" * 32, "admin_token_secret": "t" * 32}
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_production_rejects_weak_default_secrets():
    with pytest.raises(InsecureConfigError):
        assert_secure_secrets(
            _prod_settings(feedback_ip_salt="change-me", admin_token_secret="change-me")
        )


def test_production_rejects_short_secrets():
    with pytest.raises(InsecureConfigError):
        assert_secure_secrets(_prod_settings(admin_token_secret="short"))


def test_production_rejects_when_token_secret_falls_back_to_weak_salt():
    # ADMIN_TOKEN_SECRET 留空 → 回退到 FEEDBACK_IP_SALT；二者皆弱必须被拦下
    with pytest.raises(InsecureConfigError):
        assert_secure_secrets(_prod_settings(feedback_ip_salt="alleybite", admin_token_secret=""))


def test_production_accepts_strong_secrets():
    assert_secure_secrets(_prod_settings())  # 不抛错即通过


def test_debug_allows_weak_secrets():
    settings = Settings(_env_file=None, debug=True, feedback_ip_salt="alleybite", admin_token_secret="")
    assert_secure_secrets(settings)  # 本地/测试放行，保证开箱即跑


# --- 采集监控 ---------------------------------------------------------------


def test_crawl_overview_reports_breaker_state(client, session, admin_headers):
    session.add(
        JobRun(
            job_type="crawl",
            status="success",
            started_at=NOW,
            finished_at=NOW,
            stats={
                "breaker": {"tripped": ["rss"]},
                "sources": {"rss": {"status": "breaker_open"}},
                "totals": {"fetched": 3},
            },
        )
    )
    session.flush()

    data = client.get("/api/v1/admin/crawl", headers=admin_headers).json()["data"]
    assert data["breaker"]["tripped"] == ["rss"]
    assert data["breaker"]["totals"] == {"fetched": 3}
    assert data["jobs"][0]["job_type"] == "crawl"


def test_crawl_run_triggers_job_and_audits(client, session, admin_headers, monkeypatch):
    calls: dict = {}

    def fake_run_crawl(session_, settings_, *, sources=None, mode="incremental", city_hint=None, force=False):
        calls.update(sources=sources, mode=mode, city_hint=city_hint, force=force)
        job = JobRun(job_type="crawl", status="success", started_at=NOW, finished_at=NOW)
        session_.add(job)
        session_.flush()
        return job

    monkeypatch.setattr(app_main, "run_crawl", fake_run_crawl)

    resp = client.post(
        "/api/v1/admin/crawl/run",
        json={"city": "成都", "sources": ["seed"], "mode": "full", "force": True},
        headers=admin_headers,
    )
    assert resp.json()["data"]["status"] == "success"
    assert calls == {"sources": ["seed"], "mode": "full", "city_hint": "成都", "force": True}
    audit = session.scalars(
        select(AdminAuditLog).where(AdminAuditLog.action == "crawl.run")
    ).one()
    assert audit.target_type == "job_run"


# --- 反馈工单与审计 ---------------------------------------------------------


def test_feedback_status_update_and_audit(client, session, admin_headers):
    row = Feedback(content="地址错了", type="info", status="pending")
    session.add(row)
    session.flush()

    resp = client.patch(
        f"/api/v1/admin/feedback/{row.id}", json={"status": "resolved"}, headers=admin_headers
    )
    assert resp.json()["data"]["status"] == "resolved"

    audit = session.scalars(
        select(AdminAuditLog).where(AdminAuditLog.action == "feedback.update_status")
    ).one()
    assert audit.before == {"status": "pending"}
    assert audit.after == {"status": "resolved"}


def test_feedback_status_update_validates(client, session, admin_headers):
    row = Feedback(content="x", type="info", status="pending")
    session.add(row)
    session.flush()

    assert (
        client.patch(
            f"/api/v1/admin/feedback/{row.id}", json={"status": "weird"}, headers=admin_headers
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/v1/admin/feedback/999", json={"status": "resolved"}, headers=admin_headers
        ).status_code
        == 404
    )


def test_audit_list_filters_by_action(client, session, admin_headers):
    row = Feedback(content="x", type="info", status="pending")
    session.add(row)
    session.flush()
    client.patch(
        f"/api/v1/admin/feedback/{row.id}", json={"status": "processing"}, headers=admin_headers
    )

    data = client.get(
        "/api/v1/admin/audit", params={"action": "feedback.update_status"}, headers=admin_headers
    ).json()["data"]
    assert [r["action"] for r in data] == ["feedback.update_status"]
    assert "ip_hash" not in data[0]

# --- RBAC（文档 9.5 账号与权限）---------------------------------------------


def _headers_for(client, session, username: str, role: str) -> dict:
    """按指定角色建号并登录取令牌。"""
    set_admin_password(session, username, "pw", settings=app_main.settings, role=role)
    resp = client.post("/api/v1/admin/login", json={"username": username, "password": "pw"})
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


def test_rbac_reviewer_can_read_but_not_write(client, session):
    headers = _headers_for(client, session, "rev", "reviewer")
    assert client.get("/api/v1/admin/reviews", headers=headers).status_code == 200
    assert client.get("/api/v1/admin/restaurants", headers=headers).status_code == 200
    assert client.get("/api/v1/admin/crawl", headers=headers).status_code == 200
    # 写操作越权 → 403（权限闸先于资源查找）
    resp = client.post("/api/v1/admin/reviews/1/confirm", json={}, headers=headers)
    assert resp.status_code == 403
    resp = client.patch("/api/v1/admin/feedback/1", json={"status": "processing"}, headers=headers)
    assert resp.status_code == 403


def test_rbac_operator_can_write_but_not_manage_users(client, session):
    headers = _headers_for(client, session, "op2", "operator")
    # 通过权限闸到达业务层：不存在的工单返回 404 而非 403
    resp = client.post("/api/v1/admin/reviews/999/confirm", json={}, headers=headers)
    assert resp.status_code == 404
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    resp = client.post(
        "/api/v1/admin/users",
        json={"username": "x", "password": "secret66", "role": "reviewer"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_rbac_superadmin_manages_users(client, session):
    headers = _headers_for(client, session, "sa", "superadmin")
    resp = client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 200
    assert "sa" in {u["username"] for u in resp.json()["data"]}

    resp = client.post(
        "/api/v1/admin/users",
        json={"username": "newbie", "password": "secret66", "role": "reviewer"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["role"] == "reviewer"

    # 新账号立即可登录，角色生效
    login = client.post(
        "/api/v1/admin/login", json={"username": "newbie", "password": "secret66"}
    )
    assert login.status_code == 200
    assert login.json()["data"]["role"] == "reviewer"


# --- 数据看板（文档 9.5 数据看板）-------------------------------------------


def test_admin_stats_aggregates(client, session, admin_headers):
    city = City(name="成都", status="active")
    session.add(city)
    session.flush()
    session.add(Restaurant(city_id=city.id, name="甲店", name_norm="甲店", status="active"))
    session.add(Restaurant(city_id=city.id, name="乙店", name_norm="乙店", status="blocked"))
    rc1 = RawContent(source="seed", content_hash="h1", raw_text="x", city_hint="成都", status="extracted")
    rc2 = RawContent(source="seed", content_hash="h2", raw_text="y", city_hint="成都", status="failed")
    session.add_all([rc1, rc2])
    session.flush()
    session.add(
        Mention(
            raw_content_id=rc1.id,
            shop_name_raw="甲店",
            sentiment="positive",
            confidence=0.9,
            address_text="青羊区某巷",
        )
    )
    session.add(Mention(raw_content_id=rc2.id, shop_name_raw="乙店", sentiment="negative", confidence=0.5))
    session.add(
        JobRun(job_type="rank", city_id=city.id, status="success", started_at=datetime.now(timezone.utc))
    )
    session.add(JobRun(job_type="crawl", status="failed", started_at=datetime.now(timezone.utc)))
    session.commit()

    resp = client.get("/api/v1/admin/stats", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["overview"]["restaurants_total"] == 2
    assert data["overview"]["restaurants_active"] == 1
    assert data["overview"]["mentions_total"] == 2
    # 抽取失败率：extracted 1 / failed 1 → 0.5
    assert data["extract"]["failure_rate"] == 0.5
    # 地址完整率：2 条 mention 中 1 条有地址 → 0.5
    assert data["extract"]["address_coverage"] == 0.5
    # 任务成功率：success 1 / failed 1 → 0.5
    assert data["jobs_summary"]["success_rate"] == 0.5
    assert len(data["jobs_14d"]) == 14
    assert len(data["mentions_14d"]) == 14
    assert data["city_restaurants"][0]["city"] == "成都"
    assert data["city_restaurants"][0]["active"] == 1
    assert data["city_restaurants"][0]["total"] == 2


def test_admin_stats_empty_db_zero_rates(client, session, admin_headers):
    resp = client.get("/api/v1/admin/stats", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["overview"]["restaurants_total"] == 0
    assert data["extract"]["failure_rate"] == 0.0
    assert data["jobs_summary"]["success_rate"] == 0.0
