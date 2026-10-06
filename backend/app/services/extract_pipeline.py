"""逐块 LLM 抽取 → mention 落库（对接文档 5.3 / 7.2）。"""

from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..db.models import Mention as MentionRow
from ..db.models import RawContent
from ..schemas import Mention as MentionSchema
from .extractor import ExtractionError, Extractor


class ExtractionRunResult(BaseModel):
    raw_content_id: int
    status: str
    chunk_count: int
    chunk_extracted: int
    chunk_failed: int
    mention_count: int
    errors: list[str] = Field(default_factory=list)


def _locate_evidence(
    chunk_text: str, chunk_start: int, evidence: str | None
) -> tuple[int | None, int | None]:
    """把 evidence_span 还原成 cleaned_text 上的偏移；找不到（模型改写）则留空。"""
    if not evidence:
        return None, None
    index = chunk_text.find(evidence)
    if index < 0:
        return None, None
    return chunk_start + index, chunk_start + index + len(evidence)


def extract_raw_content(
    session: Session,
    raw_content_id: int,
    *,
    extractor: Extractor | None = None,
    settings: Settings | None = None,
    apply_confidence_filter: bool = False,
) -> ExtractionRunResult:
    """对一条 raw_content 的所有块执行抽取并写入 mention；调用方负责 commit。

    幂等：重跑会先清空该 raw_content 的旧 mention 再写入。
    """
    settings = settings or get_settings()
    extractor = extractor or Extractor(settings)

    row = session.get(RawContent, raw_content_id)
    if row is None:
        raise LookupError(f"raw_content#{raw_content_id} 不存在")

    session.execute(delete(MentionRow).where(MentionRow.raw_content_id == row.id))
    session.flush()

    chunks = list(row.chunks)
    chunk_extracted = 0
    chunk_failed = 0
    mention_count = 0
    errors: list[str] = []

    for chunk in chunks:
        try:
            result = extractor.extract(
                chunk.text,
                city_hint=row.city_hint,
                apply_confidence_filter=apply_confidence_filter,
            )
        except ExtractionError as exc:
            chunk.status = "failed"
            chunk_failed += 1
            errors.append(f"chunk#{chunk.chunk_index}: {exc}")
            continue

        for mention in result.mentions:
            start, end = _locate_evidence(
                chunk.text, chunk.start_offset, mention.evidence_span
            )
            session.add(
                MentionRow(
                    raw_content_id=row.id,
                    chunk_id=chunk.id,
                    shop_name_raw=mention.shop_name,
                    address_text=mention.address_text,
                    area=mention.area,
                    cuisine=mention.cuisine,
                    avg_price=mention.avg_price,
                    dishes=mention.dishes,
                    sentiment=mention.sentiment,
                    praise_keywords=mention.praise_keywords,
                    complaints=mention.complaints,
                    is_recommendation=mention.is_recommendation,
                    confidence=mention.confidence,
                    evidence_span=mention.evidence_span,
                    evidence_start=start,
                    evidence_end=end,
                )
            )
            mention_count += 1

        chunk.status = "extracted"
        chunk_extracted += 1

    if chunks:
        row.status = "failed" if chunk_failed == len(chunks) else "extracted"
    session.flush()

    return ExtractionRunResult(
        raw_content_id=row.id,
        status=row.status,
        chunk_count=len(chunks),
        chunk_extracted=chunk_extracted,
        chunk_failed=chunk_failed,
        mention_count=mention_count,
        errors=errors,
    )