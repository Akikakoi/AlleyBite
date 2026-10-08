"""ORM 模型：对应开发文档 7.2（city / restaurant / shop_alias / raw_content / mention）。

另含扩展表：content_chunk（切块）、alignment_review（实体对齐灰区审核队列）。
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawContent(Base):
    """采集到的原始内容；清洗结果回写本表。"""

    __tablename__ = "raw_content"
    __table_args__ = (
        UniqueConstraint("source", "content_hash", name="uq_raw_content_source_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    city_hint: Mapped[str | None] = mapped_column(String(64))
    raw_title: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    raw_ref: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    content_hash: Mapped[str] = mapped_column(String(80))
    lang: Mapped[str | None] = mapped_column(String(16))
    # raw | cleaned | extracted | failed
    status: Mapped[str] = mapped_column(String(16), default="raw", index=True)

    # 清洗结果（文档 5.2）
    cleaned_text: Mapped[str | None] = mapped_column(Text)
    low_trust: Mapped[bool] = mapped_column(default=False, index=True)
    ad_hits: Mapped[list | None] = mapped_column(JSON)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    chunks: Mapped[list["ContentChunk"]] = relationship(
        back_populates="raw_content",
        cascade="all, delete-orphan",
        order_by="ContentChunk.chunk_index",
    )
    mentions: Mapped[list["Mention"]] = relationship(
        back_populates="raw_content",
        cascade="all, delete-orphan",
    )


class ContentChunk(Base):
    """清洗后切出的文本块，供 LLM 逐块抽取；偏移相对 cleaned_text。"""

    __tablename__ = "content_chunk"
    __table_args__ = (
        UniqueConstraint(
            "raw_content_id", "chunk_index", name="uq_content_chunk_index"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_content.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    # pending | extracted | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    raw_content: Mapped[RawContent] = relationship(back_populates="chunks")
    mentions: Mapped[list["Mention"]] = relationship(back_populates="chunk")


class Mention(Base):
    """对某店铺的一条提及证据（文档 7.2 的 mention）。

    - restaurant_id 暂不加外键，等实体对齐里程碑落地 restaurant 表后再补
    - evidence_span 为原文片段；evidence_start/end 是其在 cleaned_text 中的偏移
    """

    __tablename__ = "mention"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_content.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_chunk.id", ondelete="SET NULL"), index=True
    )
    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurant.id", ondelete="SET NULL"), index=True
    )

    shop_name_raw: Mapped[str] = mapped_column(String(128), index=True)
    address_text: Mapped[str | None] = mapped_column(String(256))
    area: Mapped[str | None] = mapped_column(String(64))
    cuisine: Mapped[str | None] = mapped_column(String(64))
    avg_price: Mapped[float | None] = mapped_column(Float)
    dishes: Mapped[list | None] = mapped_column(JSON)
    sentiment: Mapped[str] = mapped_column(String(16), default="neutral")
    praise_keywords: Mapped[list | None] = mapped_column(JSON)
    complaints: Mapped[list | None] = mapped_column(JSON)
    is_recommendation: Mapped[bool] = mapped_column(default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_span: Mapped[str | None] = mapped_column(Text)
    evidence_start: Mapped[int | None] = mapped_column(Integer)
    evidence_end: Mapped[int | None] = mapped_column(Integer)
    author_city: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    raw_content: Mapped[RawContent] = relationship(back_populates="mentions")
    chunk: Mapped[ContentChunk | None] = relationship(back_populates="mentions")
    restaurant: Mapped[Optional["Restaurant"]] = relationship(back_populates="mentions")


class City(Base):
    """城市（文档 7.2）。本地 SQLite 用经纬度两列代替 PostGIS geography。"""

    __tablename__ = "city"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    code: Mapped[str | None] = mapped_column(String(16))
    # collecting | active | paused
    status: Mapped[str] = mapped_column(String(16), default="collecting")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    restaurants: Mapped[list["Restaurant"]] = relationship(back_populates="city")


class Restaurant(Base):
    """店铺主实体，一店一条（文档 7.2）。mention 经对齐后挂靠到本表。"""

    __tablename__ = "restaurant"
    __table_args__ = (Index("ix_restaurant_city_norm", "city_id", "name_norm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("city.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    name_norm: Mapped[str] = mapped_column(String(128))
    poi_source: Mapped[str | None] = mapped_column(String(32))
    poi_id: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(256))
    area: Mapped[str | None] = mapped_column(String(64))
    cuisine: Mapped[str | None] = mapped_column(String(64))
    avg_price: Mapped[float | None] = mapped_column(Float)
    is_chain: Mapped[bool] = mapped_column(default=False)
    # active | merged | blocked
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    merged_into: Mapped[int | None] = mapped_column(ForeignKey("restaurant.id"))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    city: Mapped[City] = relationship(back_populates="restaurants")
    aliases: Mapped[list["ShopAlias"]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    mentions: Mapped[list["Mention"]] = relationship(back_populates="restaurant")


class ShopAlias(Base):
    """店铺别名（文档 7.2 / 5.5）：人工或自动确认的别名，后续命中直接归并。"""

    __tablename__ = "shop_alias"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "alias_norm", name="uq_shop_alias_norm"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurant.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(128))
    alias_norm: Mapped[str] = mapped_column(String(128), index=True)

    restaurant: Mapped[Restaurant] = relationship(back_populates="aliases")


class RankSnapshot(Base):
    """榜单快照（文档 6.4 / 7.2）：每次重排落一条，前端读取稳定结果。"""

    __tablename__ = "rank_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("city.id", ondelete="CASCADE"), index=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    algorithm_ver: Mapped[str] = mapped_column(String(16), default="score-v1")
    items: Mapped[list] = mapped_column(JSON, default=list)  # 榜单条目快照（文档 7.3）

    city: Mapped[City] = relationship()


class AlignmentReview(Base):
    """实体对齐灰区人工审核队列（文档 5.5 第 4 步）。"""

    __tablename__ = "alignment_review"
    __table_args__ = (UniqueConstraint("mention_id", name="uq_alignment_review_mention"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mention_id: Mapped[int] = mapped_column(
        ForeignKey("mention.id", ondelete="CASCADE"), index=True
    )
    candidate_restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurant.id", ondelete="SET NULL")
    )
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(128), default="灰区相似度")
    # pending | confirmed | rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    mention: Mapped[Mention] = relationship()
    candidate_restaurant: Mapped[Optional[Restaurant]] = relationship()


class Feedback(Base):
    """店铺纠错/举报工单（文档 9.3 纠错入口 / 9.5 反馈处理 / 14 章合规验收）。

    - 免登录提交，故只落 ip_hash（加盐 SHA-256），不存原始 IP 等个人信息
    - restaurant_id 置空保留工单，店铺被合并/下架也不丢反馈
    """

    __tablename__ = "feedback"
    __table_args__ = (Index("ix_feedback_ip_created", "ip_hash", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurant.id", ondelete="SET NULL"), index=True
    )
    # info（信息有误）| closed（已关停）| label（标签不当）| other（其他）
    type: Mapped[str] = mapped_column(String(16), default="info")
    content: Mapped[str] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(128))
    # pending | processing | resolved | rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    restaurant: Mapped[Optional[Restaurant]] = relationship()


class JobRun(Base):
    """采集/流水线任务运行记录（文档 7.2）。"""

    __tablename__ = "job_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    # crawl | extract | score | rank
    job_type: Mapped[str] = mapped_column(String(32))
    city_id: Mapped[int | None] = mapped_column(
        ForeignKey("city.id", ondelete="SET NULL"), index=True
    )
    # running | success | failed
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    city: Mapped[Optional[City]] = relationship()


class AdminUser(Base):
    """管理后台账号（文档 9.5）：独立于 C 端，口令以 PBKDF2 哈希存储。"""

    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    # superadmin | operator | reviewer（RBAC 细分留 M3/M4，本期仅区分是否可写）
    role: Mapped[str] = mapped_column(String(16), default="operator")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminAuditLog(Base):
    """管理后台写操作审计日志（文档 9.5）：操作人、动作、目标与前后值。

    仅存 ip_hash（加盐 SHA-256），不落原始 IP，符合"无个人信息入库"。
    """

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    operator: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


class User(Base):
    """C 端用户（文档 10.2 账号体系 / 12 章 V2.0）。

    独立于管理后台 AdminUser；口令同样以 PBKDF2 哈希存储。
    表名用 app_user，避开 PostgreSQL 的 user 保留字。
    """

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # 手机号（短信登录用，V2.0）：unique 可空，兼容早期纯用户名账号
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    favorites: Mapped[list["Favorite"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Favorite(Base):
    """收藏（文档 8.1 favorites / V2.0）：用户态写操作，店铺下架后条目随级联删除。"""

    __tablename__ = "favorite"
    __table_args__ = (
        UniqueConstraint("user_id", "restaurant_id", name="uq_favorite_user_restaurant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurant.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="favorites")
    restaurant: Mapped[Restaurant] = relationship()


class SmsCode(Base):
    """短信验证码（文档 10.2 手机号验证码登录 / V2.0）。

    只落 code 哈希与 ip_hash，不存明文验证码与原始 IP；
    used_at 非空表示已消费，验证一次性。
    """

    __tablename__ = "sms_code"
    __table_args__ = (Index("ix_sms_code_phone_created", "phone", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    # login
    purpose: Mapped[str] = mapped_column(String(16), default="login")
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class UgcPost(Base):
    """用户 UGC 打卡/短评（文档 2.2 V2.0 用户 UGC 补充含图片）。

    先审后显：status=approved 才对外可见；images 为相对路径列表（/uploads/...）。
    """

    __tablename__ = "ugc_post"
    __table_args__ = (Index("ix_ugc_post_restaurant_status", "restaurant_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurant.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    images: Mapped[list | None] = mapped_column(JSON, default=list)
    # pending | approved | rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    user: Mapped[User] = relationship()
    restaurant: Mapped[Restaurant] = relationship()


class ViewEvent(Base):
    """详情页浏览事件（V2.0 个性化推荐依据）：匿名也记录，user_id 可空。"""

    __tablename__ = "view_event"
    __table_args__ = (Index("ix_view_event_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurant.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )