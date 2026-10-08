"""邮箱验证码登录（文档 10.2 邮箱验证码 / V2.0）。

- 验证码 6 位数字，10 分钟有效，一次性消费；库内只落 SHA-256 哈希
- 限流：同邮箱每小时 N 条、同 IP 每小时 M 条（N/M 可配）
- 未配置 SMTP_USER 时走 mock：验证码打日志（便于本地联调），生产配置 QQ 邮箱等
  SMTP 发信账号后自动切换为真实发信（smtplib 标准库，无额外依赖）
- 登录：邮箱无账号时自动注册（用户名默认"食客"+邮箱前4位，撞名时补随机后缀）
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..db.models import EmailCode, User
from .admin_auth import hash_password
from .user_auth import UserAuthError

logger = logging.getLogger("alleybite.email")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class EmailAuthError(Exception):
    """验证码发送/校验失败（统一业务错误，路由层转 4xx）。"""


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not EMAIL_RE.fullmatch(value) or len(value) > 254:
        raise EmailAuthError("邮箱格式不正确")
    return value


def _code_hash(email: str, code: str, salt: str) -> str:
    return hashlib.sha256(f"{email}:{code}:{salt}".encode("utf-8")).hexdigest()


def send_email_code(
    session: Session,
    email: str,
    *,
    settings: Settings,
    ip_hash: str | None = None,
    now: datetime | None = None,
) -> dict:
    """生成并发送验证码；返回 {'mock': bool, 'dev_code': str|None}。

    dev_code 仅在 mock 模式（未配 smtp_user）返回，供本地联调。
    """
    now = now or datetime.now(timezone.utc)
    email = normalize_email(email)

    mail_count = len(
        session.scalars(
            select(EmailCode).where(
                EmailCode.email == email,
                EmailCode.created_at >= now - timedelta(hours=1),
            )
        ).all()
    )
    if mail_count >= settings.email_send_per_mailbox_hourly:
        raise EmailAuthError("该邮箱发送过于频繁，请稍后再试")

    if ip_hash:
        ip_count = len(
            session.scalars(
                select(EmailCode).where(
                    EmailCode.ip_hash == ip_hash,
                    EmailCode.created_at >= now - timedelta(hours=1),
                )
            ).all()
        )
        if ip_count >= settings.email_send_per_ip_hourly:
            raise EmailAuthError("发送过于频繁，请稍后再试")

    code = f"{random.randint(0, 999999):06d}"
    row = EmailCode(
        email=email,
        code_hash=_code_hash(email, code, settings.feedback_ip_salt),
        ip_hash=ip_hash,
        expires_at=now + timedelta(minutes=settings.email_code_ttl_minutes),
    )
    session.add(row)
    session.commit()

    mock = not settings.smtp_user
    if mock:
        # 生产环境（DEBUG=false）未配置发信账号时只打日志，不外泄验证码到响应
        logger.warning(
            "Email mock 模式：email=%s code=%s（未配置 SMTP_USER）", email, code
        )
        dev_code = code
    else:
        _dispatch_via_smtp(settings, email, code)
        dev_code = None
    return {"mock": mock, "dev_code": dev_code}


def _dispatch_via_smtp(settings: Settings, email: str, code: str) -> None:
    """SMTP 真实发信（smtplib 标准库，SSL 端口；QQ 邮箱等授权码登录）。"""
    if not settings.smtp_password:
        raise EmailAuthError("发信账号配置不完整（SMTP_PASSWORD 缺失）")
    message = MIMEText(
        f"你的验证码是 {code}，{settings.email_code_ttl_minutes} 分钟内有效。"
        "如非本人操作请忽略本邮件。",
        "plain",
        "utf-8",
    )
    message["Subject"] = Header(
        f"{settings.smtp_from_name}登录验证码：{code}", "utf-8"
    )
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_user))
    message["To"] = formataddr(("", email))
    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.sendmail(settings.smtp_user, [email], message.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("邮件发送失败 email=%s: %s", email, exc)
        raise EmailAuthError("验证码邮件发送失败，请稍后再试") from exc


def verify_email_code(
    session: Session, email: str, code: str, *, settings: Settings
) -> None:
    """校验并消费验证码；无效/过期/已用统一抛 EmailAuthError。"""
    now = datetime.now(timezone.utc)
    email = normalize_email(email)
    row = session.scalar(
        select(EmailCode)
        .where(
            EmailCode.email == email,
            EmailCode.code_hash
            == _code_hash(email, (code or "").strip(), settings.feedback_ip_salt),
            EmailCode.used_at.is_(None),
            EmailCode.expires_at > now,
        )
        .order_by(EmailCode.id.desc())
    )
    if row is None:
        raise EmailAuthError("验证码错误或已过期")
    row.used_at = now
    session.commit()


def login_or_register_via_email(
    session: Session, email: str, *, settings: Settings
) -> tuple[User, bool]:
    """验证码登录：已有邮箱账号直接登录，否则自动注册；返回 (user, 是否新建)。"""
    user = session.scalar(select(User).where(User.email == email))
    if user is not None:
        if not user.is_active:
            raise UserAuthError("账号不可用")
        return user, False
    # 自动注册：用户名默认"食客+邮箱前4位"，撞名时补随机后缀；无口令（仅邮箱验证码登录）
    local_part = email.split("@", 1)[0]
    base = f"食客{local_part[:4]}"
    username = base
    while session.scalar(select(User).where(User.username == username)) is not None:
        username = f"{base}{secrets.token_hex(2)}"
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(
            secrets.token_hex(16), iterations=settings.user_password_iterations
        ),
    )
    session.add(user)
    session.commit()
    return user, True
