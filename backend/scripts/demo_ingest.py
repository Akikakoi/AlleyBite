"""M0 Demo：样例文本走「清洗 → 切块 → 抽取 → 实体对齐 → 打分」全链路。

落库表：raw_content + content_chunk + mention + city/restaurant/shop_alias。

用法（在 backend 目录下）：
    python scripts/demo_ingest.py

默认写入 SQLite（DATABASE_URL，默认 ./alleybite.db），可反复执行验证幂等。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.db.models import ContentChunk, Mention, RawContent, Restaurant  # noqa: E402
from app.services import (  # noqa: E402
    align_mentions,
    build_rank_snapshot,
    collect_shop_scores,
    extract_raw_content,
    get_rank,
    ingest_raw_content,
)


def main() -> None:
    settings = get_settings()
    init_db()
    mode = "mock" if settings.use_mock else "live"
    print(f"数据库：{settings.database_url} | LLM 模式：{mode}\n" + "=" * 64)

    samples_path = ROOT / "samples" / "samples.jsonl"
    lines = [ln for ln in samples_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    with SessionLocal() as session:
        # 阶段一：清洗 + 切块落库
        for line in lines:
            sample = json.loads(line)
            row = ingest_raw_content(
                session,
                source="demo",
                raw_text=sample["text"],
                city_hint=sample.get("city_hint"),
                raw_title=f"sample {sample['id']}",
                settings=settings,
            )
            session.commit()
            print(
                f"[{sample['id']}] ingest id={row.id} status={row.status} "
                f"lang={row.lang} low_trust={row.low_trust} chunks={row.chunk_count}"
            )

        # 阶段二：逐块抽取 + mention 落库
        print("-" * 64)
        for sample in (json.loads(ln) for ln in lines):
            row = session.scalar(
                select(RawContent).where(RawContent.raw_title == f"sample {sample['id']}")
            )
            run = extract_raw_content(session, row.id, settings=settings)
            session.commit()
            print(
                f"[{sample['id']}] extract id={row.id} status={run.status} "
                f"chunks={run.chunk_extracted}/{run.chunk_count} mentions={run.mention_count}"
            )
            for m in row.mentions:
                span = (
                    f"[{m.evidence_start}:{m.evidence_end}]"
                    if m.evidence_start is not None
                    else "(未对齐)"
                )
                print(
                    f"    · {m.shop_name_raw} | {m.sentiment} | conf={m.confidence} | "
                    f"evidence {span}"
                )

        # 阶段三：实体对齐（文档 5.5）
        print("-" * 64)
        run = align_mentions(session, settings=settings)
        session.commit()
        print(
            f"实体对齐：城市={run.cities} 新建 restaurant={run.restaurants_created} "
            f"归并 mention={run.mentions_aligned} 待审核={run.mentions_review} "
            f"新增别名={run.aliases_added}"
        )
        for restaurant in session.scalars(select(Restaurant).order_by(Restaurant.id)):
            print(
                f"    ▸ #{restaurant.id} {restaurant.name} "
                f"(norm={restaurant.name_norm}) mentions={len(restaurant.mentions)}"
            )

        print("-" * 64)
        print(
            f"raw_content 行数：{session.scalar(select(func.count()).select_from(RawContent))}，"
            f"content_chunk 行数：{session.scalar(select(func.count()).select_from(ContentChunk))}，"
            f"mention 行数：{session.scalar(select(func.count()).select_from(Mention))}，"
            f"restaurant 行数：{session.scalar(select(func.count()).select_from(Restaurant))}"
        )

        # 阶段四：聚合 mention → 打分排序（文档第 6 章）
        print("-" * 64)
        print("店铺打分（文档 6；按 restaurant 实体聚合）：")
        for shop in collect_shop_scores(session, settings=settings):
            tag = f"  ← 剔除：{shop.exclude_reason}" if shop.excluded else ""
            print(
                f"  #{shop.rank} {shop.shop_name} score={shop.score} "
                f"base={shop.base_score} decay={shop.time_decay} "
                f"conf={shop.confidence_factor} mentions={shop.mention_count}{tag}"
            )

        # 阶段五：榜单快照（文档 6.4）
        print("-" * 64)
        snapshot = build_rank_snapshot(session, "成都", settings=settings)
        session.commit()
        print(
            f"榜单快照：id={snapshot.id} ver={snapshot.algorithm_ver} "
            f"条目={len(snapshot.items or [])}"
        )
        rank = get_rank(session, "成都", page=1, page_size=10)
        for item in rank["items"]:
            print(
                f"  #{item['rank']} {item['name']} score={item['score']} "
                f"人均={item['avg_price']} 菜={item['recommended_dishes']} "
                f"口碑={item['praise_keywords']}"
            )

        first = session.scalars(select(RawContent).order_by(RawContent.id)).first()
        if first:
            print(f"\n示例 raw_content#{first.id} 的块（偏移相对 cleaned_text）：")
            for chunk in first.chunks:
                preview = chunk.text[:24].replace("\n", " ")
                print(
                    f"  #{chunk.chunk_index} [{chunk.start_offset}:{chunk.end_offset}] "
                    f"tokens≈{chunk.token_estimate} | {preview}…"
                )


if __name__ == "__main__":
    main()