"""C 端账号鉴权（文档 10.2 账号体系 / 12 章 V2.0）。

与管理后台（admin_auth）同一套密码学原语，但独立受众与签名密钥：
- 口令：PBKDF2-SHA256 + 随机盐，落库为 ``pbkdf2_sha256$iterations$salt$hash``
- 令牌：``base64url(payload).base64url(HMAC-SHA256)``，aud 固定为 ``alleybite-user``，
  与后台令牌（aud=alleybite-admin）互不通用，避免串签
复用 admin_auth 的哈希与签名实现，仅受众与配置项不同。
"""

from __future__ import annotations

import hmac
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..db.models import User
from .admin_auth import _b64d, _b64e, _sign, hash_password, verify_password

TOKEN_AUDIENCE = "alleybite-user"

# 用户名：2-32 位，中文/字母/数字/下划线/连字符，不含空白
USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fff-]{2,32}$", re.UNICODE)


class UserAuthError(Exception):
    """令牌缺失/无效/过期，或账号被禁用（统一按鉴权失败处理）。"""


def validate_username(username: str) -> str:
    """规范化并校验用户名；非法抛 ValueError。"""
    value = username.strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("用户名需为 2-32 位中文、字母、数字、下划线或连字符")
    return value


def validate_password(password: str) -> None:
    """口令长度校验（6-64 位）；非法抛 ValueError。"""
    if not (6 <= len(password) <= 64):
        raise ValueError("密码长度需为 6-64 位")


def register_user(
    session: Session, username: str, password: str, *, settings: Settings
) -> User:
    """注册新用户；用户名重复抛 ValueError。"""
    name = validate_username(username)
    validate_password(password)
    exists = session.scalar(select(User).where(User.username == name))
    if exists is not None:
        raise ValueError("用户名已被占用")
    user = User(
        username=name,
        password_hash=hash_password(
            password, iterations=settings.user_password_iterations
        ),
    )
    session.add(user)
    session.commit()
    return user


def authenticate_user(session: Session, username: str, password: str) -> User:
    """账号口令校验；失败统一抛 UserAuthError（不区分账号不存在/口令错）。"""
    user = session.scalar(
        select(User).where(User.username == (username or "").strip())
    )
    if user is None or not user.is_active or not verify_password(
        password, user.password_hash
    ):
        raise UserAuthError("账号或密码错误")
    return user


def issue_user_token(
    user: User, *, settings: Settings, now: datetime | None = None
) -> tuple[str, datetime]:
    """签发 C 端令牌，返回 (token, 过期时间)。"""
    now = now or datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.user_token_ttl_minutes)
    payload = {
        "aud": TOKEN_AUDIENCE,
        "sub": user.username,
        "uid": user.id,
        "exp": int(expires_at.timestamp()),
    }
    body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body, settings.user_token_key)}", expires_at


def verify_user_token(
    token: str | None, *, settings: Settings, now: datetime | None = None
) -> dict:
    """校验 C 端令牌签名/受众/有效期，返回 payload；失败抛 UserAuthError。"""
    if not token:
        raise UserAuthError("缺少令牌")
    parts = token.split(".")
    if len(parts) != 2:
        raise UserAuthError("令牌格式非法")
    body, signature = parts
    if not hmac.compare_digest(_sign(body, settings.user_token_key), signature):
        raise UserAuthError("令牌签名无效")
    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise UserAuthError("令牌内容非法")
    if payload.get("aud") != TOKEN_AUDIENCE:
        raise UserAuthError("令牌受众不符")
    now = now or datetime.now(timezone.utc)
    if int(payload.get("exp", 0)) <= int(now.timestamp()):
        raise UserAuthError("令牌已过期")
    return payload
