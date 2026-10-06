"""入库流水线：原始内容 → raw_content + content_chunk（对接文档 5.1/5.2/7.2）。"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..db.models import ContentChunk, Mention, RawContent
from .chunker import chunk_with_spans
from .cleaner import clean_text


def compute_content_hash(raw_text: str) -> str:
    """文档 4.3 的幂等键：以内容 hash 去重。"""
    return "sha256:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def ingest_raw_content(
    session: Session,
    *,
    source: str,
    raw_text: str,
    source_url: str | None = None,
    city_hint: str | None = None,
    raw_title: str | None = None,
    raw_ref: str | None = None,
    published_at: datetime | None = None,
    settings: Settings | None = None,
) -> RawContent:
    """写入/更新一条原始内容，并落清洗与切块结果。调用方负责 commit。

    幂等：同一 (source, content_hash) 只保留一行；重复入库时重算清洗与切块并替换旧块。
    """
    settings = settings or get_settings()
    digest = compute_content_hash(raw_text)

    row = session.scalar(
        select(RawContent).where(
            RawContent.source == source, RawContent.content_hash == digest
        )
    )
    if row is None:
        row = RawContent(source=source, content_hash=digest, raw_text=raw_text, status="raw")
        session.add(row)
        session.flush()

    row.source_url = source_url or row.source_url
    row.city_hint = city_hint or row.city_hint
    row.raw_title = raw_title or row.raw_title
    row.raw_ref = raw_ref or row.raw_ref
    row.published_at = published_at or row.published_at
    row.crawled_at = datetime.now(timezone.utc)

    report = clean_text(raw_text, min_chinese_ratio=settings.min_chinese_ratio)
    row.cleaned_text = report.text or None
    row.low_trust = report.low_trust
    row.ad_hits = report.ad_hits
    row.lang = "zh" if report.is_chinese else None

    chunks = []
    if report.is_chinese and report.text:
        chunks = chunk_with_spans(
            report.text,
            max_tokens=settings.extract_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )

    # 幂等替换：重新清洗意味着旧抽取结果失效，先删旧 mention，再删旧块、写新块
    session.execute(delete(Mention).where(Mention.raw_content_id == row.id))
    session.execute(delete(ContentChunk).where(ContentChunk.raw_content_id == row.id))
    for chunk in chunks:
        session.add(
            ContentChunk(
                raw_content_id=row.id,
                chunk_index=chunk.index,
                text=chunk.text,
                start_offset=chunk.start,
                end_offset=chunk.end,
                token_estimate=chunk.token_estimate,
                status="pending",
            )
        )

    row.chunk_count = len(chunks)
    row.cleaned_at = datetime.now(timezone.utc)
    row.status = "cleaned"
    session.flush()
    return row


def get_raw_content(session: Session, raw_content_id: int) -> RawContent | None:
    return session.get(RawContent, raw_content_id)