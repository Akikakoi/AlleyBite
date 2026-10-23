"""方向四第 1 批：一次性回填 restaurant.first/last_mentioned_at；
2026-10-23 增补：回填打卡补证的 last_checkin_at（approved 打卡聚合）。

用法（backend 目录下）：
    python scripts/backfill_liveness.py          # 回填并打印统计
    python scripts/backfill_liveness.py --dry-run  # 只统计不落库

幂等：可重复执行，全量覆盖式更新。验收基线（生产库 2026-10-14 实测）：
有提及店约 633 家，其中 published_at 可靠约 382 家。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db
from app.services.liveness import (
    backfill_first_last_mentioned,
    backfill_last_checkin,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填老店档案时间字段（方向四第 1 批）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不提交")
    args = parser.parse_args(argv)

    init_db()
    with SessionLocal() as session:
        stats = backfill_first_last_mentioned(session)
        checkin_stats = backfill_last_checkin(session)
        if args.dry_run:
            session.rollback()
            print("[dry-run] 已回滚，未落库")
        else:
            session.commit()
            print("回填完成并提交")

    print(f"有提及的店：{stats['restaurants_with_mention']}")
    print(f"  可靠（有 published_at）：{stats['confident']}")
    print(f"  退化（仅 crawled_at，不参与打分/展示）：{stats['crawl_only']}")
    if stats["missing_restaurant"]:
        print(f"  残留 mention（店铺已删）：{stats['missing_restaurant']}")
    print(f"有 approved 打卡的店：{checkin_stats['restaurants_with_checkin']}")
    if checkin_stats["missing_restaurant"]:
        print(f"  残留打卡（店铺已删）：{checkin_stats['missing_restaurant']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
