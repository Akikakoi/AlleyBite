"""测试共用夹具。"""

import pytest

from app import main as app_main


@pytest.fixture(autouse=True)
def _fast_admin_auth(monkeypatch):
    """固定管理后台令牌密钥并降低 PBKDF2 迭代，避免受本机 .env 影响且加速测试。"""
    monkeypatch.setattr(app_main.settings, "admin_token_secret", "test-secret")
    monkeypatch.setattr(app_main.settings, "admin_password_iterations", 1_000)