"""登录失败限速（2026-10-16 加固）：服务层逻辑 + HTTP 端点行为。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.db import get_session
from app.db.base import Base
from app.main import app
from app.services.admin_auth import set_admin_password
from app.services.login_throttle import LoginThrottle, LoginThrottled


# --- 服务层 -----------------------------------------------------------------


class _FakeClock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def test_throttle_locks_after_max_failures():
    clock = _FakeClock()
    throttle = LoginThrottle(
        max_failures=3, window_seconds=900, lockout_seconds=600, now=clock
    )
    key = "admin:op:1.2.3.4"

    throttle.record_failure(key)
    throttle.record_failure(key)
    throttle.check(key)  # 未达上限，不抛

    throttle.record_failure(key)
    with pytest.raises(LoginThrottled):
        throttle.check(key)


def test_throttle_reset_clears_lock():
    clock = _FakeClock()
    throttle = LoginThrottle(
        max_failures=2, window_seconds=900, lockout_seconds=600, now=clock
    )
    key = "user:op:1.2.3.4"
    throttle.record_failure(key)
    throttle.record_failure(key)
    with pytest.raises(LoginThrottled):
        throttle.check(key)

    throttle.reset(key)
    throttle.check(key)  # 不抛


def test_throttle_unlocks_after_lockout():
    clock = _FakeClock()
    throttle = LoginThrottle(
        max_failures=1, window_seconds=900, lockout_seconds=600, now=clock
    )
    key = "admin:op:1.2.3.4"
    throttle.record_failure(key)
    with pytest.raises(LoginThrottled):
        throttle.check(key)

    clock.value += 601  # 越过锁定时长
    throttle.check(key)  # 不抛


def test_throttle_window_expires_old_failures():
    clock = _FakeClock()
    throttle = LoginThrottle(
        max_failures=2, window_seconds=900, lockout_seconds=600, now=clock
    )
    key = "admin:op:1.2.3.4"
    throttle.record_failure(key)
    clock.value += 901  # 越出失败窗口
    throttle.record_failure(key)
    throttle.check(key)  # 旧失败已滑出窗口，不计入


def test_throttle_keys_are_independent():
    throttle = LoginThrottle(max_failures=1, window_seconds=900, lockout_seconds=600)
    throttle.record_failure(LoginThrottle.key("admin", "op", "1.1.1.1"))
    with pytest.raises(LoginThrottled):
        throttle.check(LoginThrottle.key("admin", "op", "1.1.1.1"))
    # 其他用户/其他 scope/其他 IP 不受影响
    throttle.check(LoginThrottle.key("admin", "other", "1.1.1.1"))
    throttle.check(LoginThrottle.key("user", "op", "1.1.1.1"))
    throttle.check(LoginThrottle.key("admin", "op", "2.2.2.2"))


# --- HTTP 端点 ---------------------------------------------------------------


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


def test_admin_login_locks_after_repeated_failures(client, session):
    """连续失败达上限后，即使口令正确也返回 429。"""
    set_admin_password(session, "lock-op", "right-pw", settings=app_main.settings)
    for _ in range(app_main.settings.login_max_failures):
        resp = client.post(
            "/api/v1/admin/login",
            json={"username": "lock-op", "password": "wrong-pw"},
        )
        assert resp.status_code == 401

    resp = client.post(
        "/api/v1/admin/login",
        json={"username": "lock-op", "password": "right-pw"},
    )
    assert resp.status_code == 429


def test_admin_login_success_resets_counter(client, session):
    set_admin_password(session, "reset-op", "right-pw", settings=app_main.settings)
    for _ in range(app_main.settings.login_max_failures - 1):
        client.post(
            "/api/v1/admin/login",
            json={"username": "reset-op", "password": "wrong-pw"},
        )
    resp = client.post(
        "/api/v1/admin/login",
        json={"username": "reset-op", "password": "right-pw"},
    )
    assert resp.status_code == 200  # 成功即清零

    # 之后再失败 max-1 次仍不应锁定（计数已重置）
    for _ in range(app_main.settings.login_max_failures - 1):
        resp = client.post(
            "/api/v1/admin/login",
            json={"username": "reset-op", "password": "wrong-pw"},
        )
        assert resp.status_code == 401
