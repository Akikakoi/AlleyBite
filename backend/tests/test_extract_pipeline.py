import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import ContentChunk, Mention, RawContent
from app.services.extract_pipeline import extract_raw_content
from app.services.extractor import ExtractionError
from app.services.ingest import ingest_raw_content


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
        extract_max_tokens=512,
        chunk_overlap_tokens=0,
        min_chinese_ratio=0.5,
        extract_confidence_min=0.6,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


MINGTING_TEXT = (
    "昨天去吃了明婷饭店，藏在青羊区同心路的巷子里，"
    "环境确实一般，但脑花豆腐和霸王兔太香了，锅气足，人均才 65。"
)
MINGTING_EVIDENCE = "环境确实一般，但脑花豆腐和霸王兔太香了"


def _ingest(session, text: str) -> RawContent:
    row = ingest_raw_content(
        session,
        source="demo",
        raw_text=text,
        city_hint="成都",
        raw_title="demo",
        settings=make_settings(),
    )
    session.commit()
    return row


def test_extract_writes_mentions_and_status(session):
    row = _ingest(session, MINGTING_TEXT)

    run = extract_raw_content(session, row.id, settings=make_settings())
    session.commit()

    assert run.mention_count == 1
    assert run.chunk_extracted == run.chunk_count == 1
    assert run.chunk_failed == 0
    assert run.status == "extracted"
    assert row.status == "extracted"
    assert all(c.status == "extracted" for c in row.chunks)

    mention = session.scalars(select(Mention)).one()
    assert mention.shop_name_raw == "明婷饭店"
    assert mention.sentiment == "positive"
    assert mention.is_recommendation is True
    assert "脑花豆腐" in mention.dishes


def test_evidence_offsets_align_with_cleaned_text(session):
    row = _ingest(session, MINGTING_TEXT)

    extract_raw_content(session, row.id, settings=make_settings())
    session.commit()

    mention = session.scalars(select(Mention)).one()
    assert mention.evidence_span == MINGTING_EVIDENCE
    assert mention.evidence_start is not None
    assert mention.evidence_end is not None
    assert (
        row.cleaned_text[mention.evidence_start : mention.evidence_end]
        == mention.evidence_span
    )


def test_extract_is_idempotent(session):
    row = _ingest(session, MINGTING_TEXT)

    for _ in range(2):
        extract_raw_content(session, row.id, settings=make_settings())
        session.commit()

    assert session.scalar(select(func.count()).select_from(Mention)) == 1


def test_extract_missing_raw_content_raises(session):
    with pytest.raises(LookupError):
        extract_raw_content(session, 9999, settings=make_settings())


def test_extract_marks_failed_when_chunk_fails(session):
    row = _ingest(session, MINGTING_TEXT)

    class _FailingExtractor:
        def extract(self, *args, **kwargs):
            raise ExtractionError("boom")

    run = extract_raw_content(
        session, row.id, extractor=_FailingExtractor(), settings=make_settings()
    )
    session.commit()

    assert run.chunk_failed == run.chunk_count
    assert run.chunk_extracted == 0
    assert run.mention_count == 0
    assert run.errors
    assert row.status == "failed"
    assert all(c.status == "failed" for c in row.chunks)
    assert session.scalar(select(func.count()).select_from(Mention)) == 0


def test_extract_no_chunks_keeps_status(session):
    row = _ingest(session, "this is a pure english post about food")
    assert row.chunk_count == 0

    run = extract_raw_content(session, row.id, settings=make_settings())
    session.commit()

    assert run.chunk_count == 0
    assert run.mention_count == 0
    assert run.status == "cleaned"  # 无块时不改写状态
    assert row.status == "cleaned"


def test_extract_confidence_filter(session):
    row = _ingest(session, "成都新开的沸腾里火锅，味道很一般。")

    run = extract_raw_content(session, row.id, settings=make_settings())
    session.commit()
    assert run.mention_count == 1  # mock 置信度 0.35，默认不过滤

    run = extract_raw_content(
        session, row.id, settings=make_settings(), apply_confidence_filter=True
    )
    session.commit()
    assert run.mention_count == 0
    assert session.scalar(select(func.count()).select_from(ContentChunk)) == 1