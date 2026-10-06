"""并发抽取驱动（运维工具）：两阶段执行，避免 SQLite 写锁互相阻塞。

问题：extract_raw_content 会在 LLM 调用前开写事务、且读游标横跨整段 LLM 调用，
多线程并发时长时间持锁 → `database is locked`。

方案：拆成两阶段——
  阶段一（并发）：短连接读出块文本后立即关连接，纯调 LLM，不碰写库；
  阶段二（串行）：主线程逐个删旧 mention、写新 mention、更新状态并提交。
写锁只在阶段二短暂持有，阶段一可安全并发。

用法：python scripts/run_extract_parallel.py [--workers 5] [--city 成都]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.db.models import ContentChunk  # noqa: E402
from app.db.models import Mention as MentionRow  # noqa: E402
from app.db.models import RawContent  # noqa: E402
from app.services.extract_pipeline import _locate_evidence  # noqa: E402
from app.services.extractor import ExtractionError, Extractor  # noqa: E402

SETTINGS = get_settings()


def _extract_only(doc_id: int) -> tuple[int, list, str | None]:
    """阶段一：读块 → 并发调 LLM，返回原始结果；全程不写库。"""
    session = SessionLocal()
    try:
        row = session.get(RawContent, doc_id)
        if row is None:
            return doc_id, [], f"raw_content#{doc_id} 不存在"
        city_hint = row.city_hint
        chunks = [
            (c.id, c.chunk_index, c.text, c.start_offset) for c in row.chunks
        ]
    finally:
        session.close()

    extractor = Extractor(SETTINGS)
    results: list = []
    for chunk_id, chunk_index, text, start in chunks:
        try:
            res = extractor.extract(text, city_hint=city_hint)
        except ExtractionError as exc:
            results.append(("fail", chunk_id, chunk_index, text, start, str(exc)))
        else:
            results.append(("ok", chunk_id, chunk_index, text, start, res))
    return doc_id, results, None


def _persist(doc_id: int, results: list) -> int:
    """阶段二：串行写库（删旧 → 写新 → 更新状态）。返回写入 mention 数。"""
    session = SessionLocal()
    try:
        row = session.get(RawContent, doc_id)
        session.execute(delete(MentionRow).where(MentionRow.raw_content_id == doc_id))
        extracted = failed = mentions = 0
        for item in results:
            kind, chunk_id, _idx, text, start, payload = item
            if kind == "fail":
                chunk = session.get(ContentChunk, chunk_id)
                if chunk is not None:
                    chunk.status = "failed"
                failed += 1
                continue
            for mention in payload.mentions:
                ev_start, ev_end = _locate_evidence(text, start, mention.evidence_span)
                session.add(
                    MentionRow(
                        raw_content_id=doc_id,
                        chunk_id=chunk_id,
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
                        evidence_start=ev_start,
                        evidence_end=ev_end,
                    )
                )
                mentions += 1
            chunk = session.get(ContentChunk, chunk_id)
            if chunk is not None:
                chunk.status = "extracted"
            extracted += 1
        if results:
            row.status = "failed" if failed == len(results) else "extracted"
        session.commit()
        return mentions
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--city", default=None)
    args = parser.parse_args()

    session = SessionLocal()
    stmt = select(RawContent.id).where(RawContent.status == "cleaned")
    if args.city:
        stmt = stmt.where(RawContent.city_hint == args.city)
    doc_ids = list(session.scalars(stmt.order_by(RawContent.id)).all())
    session.close()

    total = len(doc_ids)
    print(f"待抽取 {total} 篇，并发 {args.workers}（两阶段）", flush=True)
    if not total:
        return 0

    started = time.time()
    done = failures = total_mentions = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_extract_only, d): d for d in doc_ids}
        for fut in cf.as_completed(futures):
            doc_id, results, err = fut.result()
            done += 1
            if err:
                failures += 1
                print(f"  [{done}/{total}] doc={doc_id} FAIL {err}", flush=True)
                continue
            try:
                mentions = _persist(doc_id, results)
                total_mentions += mentions
                print(
                    f"  [{done}/{total}] doc={doc_id} chunks={len(results)} "
                    f"mentions={mentions} elapsed={round(time.time() - started)}s",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  [{done}/{total}] doc={doc_id} PERSIST_FAIL {exc}", flush=True)

    print(
        f"完成 {done} 篇，mentions={total_mentions}，失败={failures}，"
        f"总耗时={round(time.time() - started)}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())