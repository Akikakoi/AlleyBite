"""管理后台鉴权（文档 9.5）：与 C 端隔离的独立登录 + HMAC 令牌。

C 端免登录（账号体系属 V2），故此处自建一套独立鉴权：
- 口令：PBKDF2-SHA256 + 随机盐，落库为 ``pbkdf2_sha256$iterations$salt$hash``
- 令牌：``base64url(payload).base64url(HMAC-SHA256)``，含 aud/exp，签名密钥独立配置
仅用标准库实现，不引入额外依赖；令牌 audience 固定为 admin，避免与其它用途混签。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..db.models import AdminUser

ALGORITHM = "pbkdf2_sha256"
TOKEN_AUDIENCE = "alleybite-admin"


class AdminAuthError(Exception):
    """令牌缺失/无效/过期，或账号被禁用（统一按鉴权失败处理）。"""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(
    password: str, *, iterations: int, salt: str | None = None
) -> str:
    """生成口令哈希；salt 为十六进制串，缺省随机。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    )
    return f"{ALGORITHM}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间比对口令；格式非法一律视为不匹配。"""
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def _sign(body: str, key: str) -> str:
    return _b64e(hmac.new(key.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def issue_token(
    user: AdminUser,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """签发管理后台令牌，返回 (token, 过期时间)。"""
    now = now or datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.admin_token_ttl_minutes)
    payload = {
        "aud": TOKEN_AUDIENCE,
        "sub": user.username,
        "role": user.role,
        "exp": int(expires_at.timestamp()),
    }
    body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body, settings.admin_token_key)}", expires_at


def verify_token(
    token: str | None,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> dict:
    """校验令牌签名/受众/有效期，返回 payload；失败抛 AdminAuthError。"""
    if not token:
        raise AdminAuthError("缺少令牌")
    parts = token.split(".")
    if len(parts) != 2:
        raise AdminAuthError("令牌格式非法")
    body, signature = parts
    if not hmac.compare_digest(_sign(body, settings.admin_token_key), signature):
        raise AdminAuthError("令牌签名无效")
    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise AdminAuthError("令牌内容非法")
    if payload.get("aud") != TOKEN_AUDIENCE:
        raise AdminAuthError("令牌受众不符")
    now = now or datetime.now(timezone.utc)
    if int(payload.get("exp", 0)) <= int(now.timestamp()):
        raise AdminAuthError("令牌已过期")
    return payload


def authenticate(
    session: Session, username: str, password: str
) -> AdminUser:
    """账号口令校验；失败统一抛 AdminAuthError（不区分账号不存在/口令错）。"""
    user = session.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise AdminAuthError("账号或密码错误")
    return user


def ensure_bootstrap_admin(
    session: Session, *, settings: Settings, now: datetime | None = None
) -> AdminUser | None:
    """按配置初始化超管（幂等）；ADMIN_PASSWORD 留空则不初始化。"""
    if not settings.admin_password:
        return None
    user = session.scalar(
        select(AdminUser).where(AdminUser.username == settings.admin_username)
    )
    if user is not None:
        return user
    user = AdminUser(
        username=settings.admin_username,
        password_hash=hash_password(
            settings.admin_password, iterations=settings.admin_password_iterations
        ),
        role="superadmin",
        created_at=now or datetime.now(timezone.utc),
    )
    session.add(user)
    session.commit()
    return user


def set_admin_password(
    session: Session,
    username: str,
    password: str,
    *,
    settings: Settings,
    role: str = "operator",
) -> AdminUser:
    """新增或重置管理后台账号（供 scripts/create_admin.py 使用）。"""
    user = session.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None:
        user = AdminUser(username=username, role=role)
        session.add(user)
    user.password_hash = hash_password(
        password, iterations=settings.admin_password_iterations
    )
    user.is_active = True
    session.commit()
    return user