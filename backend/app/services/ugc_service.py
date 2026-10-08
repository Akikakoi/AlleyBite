"""UGC 打卡/短评（文档 2.2 V2.0 用户 UGC 补充含图片）。

- 发布：登录用户对店铺发 300 字内打卡，最多 3 张图（先传图拿路径再提交）
- 展示：先审后显——仅 approved 对外；我的打卡含全部状态
- 审核：管理后台 approve/reject，写审计日志
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..core.config import Settings
from ..db.models import Restaurant, UgcPost

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class UgcError(Exception):
    """UGC 业务错误（内容不合法/图片不合法等），路由层转 4xx。"""


def save_upload(data: bytes, content_type: str, *, settings: Settings) -> str:
    """保存上传图片，返回相对路径 /uploads/<yyyymm>/<uuid><ext>。"""
    ext = ALLOWED_IMAGE_TYPES.get(content_type)
    if ext is None:
        raise UgcError("仅支持 JPG/PNG/WebP 图片")
    if len(data) > settings.ugc_image_max_bytes:
        raise UgcError("单张图片不能超过 5MB")
    if not data:
        raise UgcError("图片内容为空")
    now = Path(settings.uploads_dir)
    subdir = now / "ugc"
    subdir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (subdir / name).write_bytes(data)
    return f"/uploads/ugc/{name}"


def create_ugc_post(
    session: Session,
    user_id: int,
    restaurant_id: int,
    content: str,
    *,
    settings: Settings,
    images: list[str] | None = None,
) -> UgcPost:
    """发布打卡：店铺须存在，正文与图片数量/路径做校验，默认 pending 待审。"""
    text = (content or "").strip()
    if not text:
        raise UgcError("打卡内容不能为空")
    if len(text) > settings.ugc_content_max_len:
        raise UgcError(f"打卡内容不能超过 {settings.ugc_content_max_len} 字")
    if session.get(Restaurant, restaurant_id) is None:
        raise LookupError("店铺不存在")
    images = images or []
    if len(images) > settings.ugc_images_max:
        raise UgcError(f"最多上传 {settings.ugc_images_max} 张图片")
    for img in images:
        if not str(img).startswith("/uploads/"):
            raise UgcError("图片路径非法")
    post = UgcPost(
        user_id=user_id,
        restaurant_id=restaurant_id,
        content=text,
        images=images,
    )
    session.add(post)
    session.commit()
    return post


def _post_row(post: UgcPost) -> dict:
    user = post.user
    return {
        "id": post.id,
        "restaurant_id": post.restaurant_id,
        "restaurant_name": post.restaurant.name if post.restaurant else None,
        "username": user.username if user else "匿名",
        "content": post.content,
        "images": post.images or [],
        "status": post.status,
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }


def list_restaurant_ugc(
    session: Session, restaurant_id: int, *, limit: int = 20
) -> list[dict]:
    """店铺详情页打卡列表（仅 approved），最新在前。"""
    rows = (
        session.scalars(
            select(UgcPost)
            .options(joinedload(UgcPost.user), joinedload(UgcPost.restaurant))
            .where(UgcPost.restaurant_id == restaurant_id, UgcPost.status == "approved")
            .order_by(UgcPost.id.desc())
            .limit(min(50, max(1, limit)))
        )
        .unique()
        .all()
    )
    return [_post_row(r) for r in rows]


def list_my_ugc(session: Session, user_id: int) -> list[dict]:
    """我的打卡（含待审/被拒），最新在前。"""
    rows = (
        session.scalars(
            select(UgcPost)
            .options(joinedload(UgcPost.user), joinedload(UgcPost.restaurant))
            .where(UgcPost.user_id == user_id)
            .order_by(UgcPost.id.desc())
        )
        .unique()
        .all()
    )
    return [_post_row(r) for r in rows]


def list_ugc_admin(
    session: Session, *, status: str | None = None, limit: int = 50
) -> list[dict]:
    """管理后台审核列表。"""
    stmt = (
        select(UgcPost)
        .options(joinedload(UgcPost.user), joinedload(UgcPost.restaurant))
        .order_by(UgcPost.id.desc())
        .limit(min(200, max(1, limit)))
    )
    if status:
        stmt = stmt.where(UgcPost.status == status)
    rows = session.scalars(stmt).unique().all()
    return [_post_row(r) for r in rows]


def review_ugc(
    session: Session, post_id: int, status: str
) -> tuple[UgcPost, str]:
    """审核打卡：approved/rejected；返回 (post, 之前状态)。"""
    post = session.get(UgcPost, post_id)
    if post is None:
        raise LookupError("打卡不存在")
    if status not in ("approved", "rejected"):
        raise ValueError("状态非法")
    before = post.status
    post.status = status
    session.commit()
    return post, before
