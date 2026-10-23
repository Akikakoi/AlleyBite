"""is_chain 回填（方向五第 2 批，薄壳，核心在 app/services/alignment.py）。

口径：同城内 brand_stem 相同的活跃店 ≥2 家 → 该组全部标记 is_chain=True
（normalize 已剥位置性分店后缀，brand_stem 再归并中部的"一店/二店/三店"编号，
使"乐三姐飘香馆一店脆鱼 / …二店脆鱼"归入同品牌组）。

用法（backend 目录下）：
    python scripts/backfill_chain_flags.py              # 回填并打印统计
    python scripts/backfill_chain_flags.py --dry-run    # 只统计不落库

幂等：已标记 is_chain=True 的店跳过；可重复执行。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.services.alignment import backfill_chain_flags  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="is_chain 启发式回填（方向五第 2 批）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落库")
    args = parser.parse_args(argv)

    init_db()
    with SessionLocal() as session:
        stats = backfill_chain_flags(session)
        if args.dry_run:
            session.rollback()
            print("[dry-run] 已回滚，未落库")
        else:
            session.commit()
            print("回填完成并提交")

    print(f"同品牌多店组（同城 brand_stem ≥2）：{stats['brand_groups']} 组")
    print(f"本次新标记 is_chain=True：{stats['flagged']} 家")
    print(f"原有 is_chain=True：{stats['already_chain']} 家")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
