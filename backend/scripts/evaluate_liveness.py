"""方向四第 3 批：推进存活监测状态机（miss 阈值 → suspect/closed）。

用法（backend 目录下）：
    python scripts/evaluate_liveness.py             # 推进并落 job_run
    python scripts/evaluate_liveness.py --dry-run   # 只算不动库

由 Celery beat 每日调用（tasks.evaluate_liveness，默认 04:30，
排在 03:00 POI 刷新之后）。阈值见 config：
    LIVENESS_SUSPECT_MISSES（默认 5）/ LIVENESS_CLOSED_MISSES（默认 10）
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.db.models import JobRun  # noqa: E402
from app.services.liveness import evaluate_liveness  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="存活监测状态机推进（方向四第 3 批）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落库")
    args = parser.parse_args(argv)

    init_db()
    started = datetime.now(timezone.utc)
    with SessionLocal() as session:
        stats = evaluate_liveness(session)
        if args.dry_run:
            session.rollback()
            print("[dry-run] 已回滚，未落库")
            return 0

        job = JobRun(
            job_type="liveness",
            status="success",
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            stats=stats,
        )
        session.add(job)
        session.commit()
        print(f"job#{job.id} liveness 状态机推进完成")

    print(f"  → suspect：{stats['to_suspect']} 家")
    print(f"  → closed：{stats['to_closed']} 家")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
