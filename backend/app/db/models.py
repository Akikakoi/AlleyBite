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