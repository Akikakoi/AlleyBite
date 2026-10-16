import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import ContentChunk, RawContent
from app.services.ingest import compute_content_hash, ingest_raw_content


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


def make_settings(**overrides) -> Settings:
    base = dict(
        llm_mock=True,
        llm_api_key="",
        extract_max_tokens=64,
        chunk_overlap_tokens=0,
        min_chinese_ratio=0.5,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


LONG_TEXT = "这家店锅气足，价钱实惠。" * 60


def test_ingest_creates_raw_content_and_chunks(session):
    row = ingest_raw_content(
        session,
        source="xiaohongshu",
        raw_text=LONG_TEXT,
        city_hint="成都",
        raw_title="成都苍蝇馆子",
        settings=make_settings(),
    )
    session.commit()

    assert row.id is not None
    assert row.status == "cleaned"
    assert row.lang == "zh"
    assert row.cleaned_text
    assert row.cleaned_at is not None
    assert row.chunk_count > 1
    assert row.chunk_count == session.scalar(
        select(func.count()).select_from(ContentChunk)
    )


def test_chunk_offsets_align_with_cleaned_text(session):
    row = ingest_raw_content(
        session, source="forum", raw_text=LONG_TEXT, settings=make_settings()
    )
    session.commit()

    assert row.cleaned_text is not None
    for chunk in row.chunks:
        assert row.cleaned_text[chunk.start_offset : chunk.end_offset] == chunk.text


def test_ingest_is_idempotent(session):
    for _ in range(2):
        ingest_raw_content(
            session, source="forum", raw_text=LONG_TEXT, settings=make_settings()
        )
        session.commit()

    assert session.scalar(select(func.count()).select_from(RawContent)) == 1
    chunks = session.scalars(select(ContentChunk)).all()
    indices = [c.chunk_index for c in chunks]
    assert indices == sorted(set(indices))  # 无重复块


def test_ingest_duplicate_preserves_extraction_result(session):
    """重复入库（同内容+同清洗配置）不得作废已抽取的 mention（2026-10-16 修复）。"""
    from app.db.models import Mention

    row = ingest_raw_content(
        session, source="forum", raw_text=LONG_TEXT, settings=make_settings()
    )
    session.commit()
    chunk = session.scalar(
        select(ContentChunk).where(ContentChunk.raw_content_id == row.id)
    )
    session.add(Mention(raw_content_id=row.id, chunk_id=chunk.id, shop_name_raw="明婷饭店"))
    row.status = "extracted"
    session.commit()

    again = ingest_raw_content(
        session, source="forum", raw_text=LONG_TEXT, settings=make_settings()
    )
    session.commit()

    assert again.id == row.id
    assert again.status == "extracted"  # 未被打回 cleaned（否则下游会重复烧 LLM）
    assert session.scalar(select(func.count()).select_from(Mention)) == 1
    assert session.scalar(select(func.count()).select_from(ContentChunk)) == row.chunk_count


def test_ingest_rebuilds_when_clean_fingerprint_changes(session):
    """清洗配置变化（指纹不同）时仍走重建，旧 mention 被替换。"""
    from app.db.models import Mention

    row = ingest_raw_content(
        session,
        source="forum",
        raw_text=LONG_TEXT,
        settings=make_settings(extract_max_tokens=64),
    )
    session.commit()
    chunk = session.scalar(
        select(ContentChunk).where(ContentChunk.raw_content_id == row.id)
    )
    session.add(Mention(raw_content_id=row.id, chunk_id=chunk.id, shop_name_raw="明婷饭店"))
    row.status = "extracted"
    session.commit()

    again = ingest_raw_content(
        session,
        source="forum",
        raw_text=LONG_TEXT,
        settings=make_settings(extract_max_tokens=32),
    )
    session.commit()

    assert again.id == row.id
    assert again.status == "cleaned"  # 重建后待重抽
    assert session.scalar(select(func.count()).select_from(Mention)) == 0


def test_ingest_non_chinese_has_no_chunks(session):
    row = ingest_raw_content(
        session,
        source="weibo",
        raw_text="this is a pure english post about food",
        settings=make_settings(),
    )
    session.commit()

    assert row.chunk_count == 0
    assert row.lang is None
    assert row.chunks == []


def test_ingest_marks_low_trust(session):
    row = ingest_raw_content(
        session,
        source="dianping",
        raw_text="【探店合作】成都新开的火锅店，团购链接见评论区",
        settings=make_settings(),
    )
    session.commit()

    assert row.low_trust is True
    assert "团购链接" in row.ad_hits


def test_compute_content_hash_stable():
    assert compute_content_hash("abc") == compute_content_hash("abc")
    assert compute_content_hash("abc").startswith("sha256:")
    assert compute_content_hash("abc") != compute_content_hash("abd")