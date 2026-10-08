"""短信验证码登录（文档 10.2 手机号验证码 / V2.0）。

- 验证码 6 位数字，10 分钟有效，一次性消费；库内只落 SHA-256 哈希
- 限流：同手机号每小时 N 条、同 IP 每小时 M 条（N/M 可配）
- 未配置 SMS_API_KEY 时走 mock：验证码打日志（便于本地联调），生产必须配置真实通道
- 登录：手机号无账号时自动注册（用户名默认"用户"+手机尾号4位，可在个人资料改名后继支持）
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..db.models import SmsCode, User
from .admin_auth import hash_password
from .user_auth import UserAuthError

logger = logging.getLogger("alleybite.sms")

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


class SmsError(Exception):
    """验证码发送/校验失败（统一业务错误，路由层转 4xx）。"""


def normalize_phone(phone: str) -> str:
    value = (phone or "").strip()
    if not PHONE_RE.fullmatch(value):
        raise SmsError("手机号格式不正确")
    return value


def _code_hash(phone: str, code: str, salt: str) -> str:
    return hashlib.sha256(f"{phone}:{code}:{salt}".encode("utf-8")).hexdigest()


def send_sms_code(
    session: Session,
    phone: str,
    *,
    settings: Settings,
    ip_hash: str | None = None,
    now: datetime | None = None,
) -> dict:
    """生成并发送验证码；返回 {'mock': bool, 'dev_code': str|None}。

    dev_code 仅在 mock 模式（未配 sms_api_key）返回，供本地联调。
    """
    now = now or datetime.now(timezone.utc)
    phone = normalize_phone(phone)

    phone_count = len(
        session.scalars(
            select(SmsCode).where(
                SmsCode.phone == phone,
                SmsCode.created_at >= now - timedelta(hours=1),
            )
        ).all()
    )
    if phone_count >= settings.sms_send_per_phone_hourly:
        raise SmsError("该手机号发送过于频繁，请稍后再试")

    if ip_hash:
        ip_count = len(
            session.scalars(
                select(SmsCode).where(
                    SmsCode.ip_hash == ip_hash,
                    SmsCode.created_at >= now - timedelta(hours=1),
                )
            ).all()
        )
        if ip_count >= settings.sms_send_per_ip_hourly:
            raise SmsError("发送过于频繁，请稍后再试")

    code = f"{random.randint(0, 999999):06d}"
    row = SmsCode(
        phone=phone,
        code_hash=_code_hash(phone, code, settings.feedback_ip_salt),
        ip_hash=ip_hash,
        expires_at=now + timedelta(minutes=settings.sms_code_ttl_minutes),
    )
    session.add(row)
    session.commit()

    mock = not settings.sms_api_key
    if mock:
        # 生产环境（DEBUG=false）未配置短信通道时只打日志，不外泄验证码到响应
        logger.warning("SMS mock 模式：phone=%s code=%s（未配置 SMS_API_KEY）", phone, code)
        dev_code = code
    else:
        _dispatch_via_provider(settings, phone, code)
        dev_code = None
    return {"mock": mock, "dev_code": dev_code}


def _dispatch_via_provider(settings: Settings, phone: str, code: str) -> None:
    """真实短信通道。当前支持阿里云短信（占位实现：配置齐全才调用）。

    生产接入时按所选厂商补签名/模板参数与错误处理；缺配置直接报错而非静默。
    """
    if not (settings.sms_sign_name and settings.sms_template_code):
        raise SmsError("短信通道配置不完整（SMS_SIGN_NAME / SMS_TEMPLATE_CODE）")
    # 阿里云 Dysmsapi 接入点（引入官方 SDK 后补全调用；此处保留明确失败以免假发送）
    raise SmsError("短信通道未实现：请接入短信服务商 SDK 后启用")


def verify_sms_code(session: Session, phone: str, code: str, *, settings: Settings) -> None:
    """校验并消费验证码；无效/过期/已用统一抛 SmsError。"""
    now = datetime.now(timezone.utc)
    phone = normalize_phone(phone)
    row = session.scalar(
        select(SmsCode)
        .where(
            SmsCode.phone == phone,
            SmsCode.code_hash == _code_hash(phone, (code or "").strip(), settings.feedback_ip_salt),
            SmsCode.used_at.is_(None),
            SmsCode.expires_at > now,
        )
        .order_by(SmsCode.id.desc())
    )
    if row is None:
        raise SmsError("验证码错误或已过期")
    row.used_at = now
    session.commit()


def login_or_register_via_sms(
    session: Session, phone: str, *, settings: Settings
) -> tuple[User, bool]:
    """验证码登录：已有手机号账号直接登录，否则自动注册；返回 (user, 是否新建)。"""
    user = session.scalar(select(User).where(User.phone == phone))
    if user is not None:
        if not user.is_active:
            raise UserAuthError("账号不可用")
        return user, False
    # 自动注册：用户名默认"用户+尾号4位"，撞名时补随机后缀；无口令（仅短信登录）
    base = f"用户{phone[-4:]}"
    username = base
    suffix = 0
    while session.scalar(select(User).where(User.username == username)) is not None:
        suffix += 1
        username = f"{base}{secrets.token_hex(2)}"
    user = User(
        username=username,
        phone=phone,
        password_hash=hash_password(
            secrets.token_hex(16), iterations=settings.user_password_iterations
        ),
    )
    session.add(user)
    session.commit()
    return user, True
