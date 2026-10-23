"""定时跑批入口：对城市执行「抽取 → 对齐 → 打分 → 榜单快照」（文档 4.4 / 6.4）。

用法（在 backend 目录下）：
    python scripts/run_pipeline.py            # 跑全部城市
    python scripts/run_pipeline.py 成都        # 只跑指定城市

供 cron / Windows 任务计划程序在每日 05:00 调用（文档 4.4 调度）。
每次运行落 job_run 记录（job_type=rank），可用 GET /api/v1/admin/jobs 查看。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services import make_rank_cache, run_city_pipeline  # noqa: E402
from app.services import list_city_names  # noqa: E402
from app.services.rank_service import DEFAULT_TOP_N  # noqa: E402


def main() -> int:
    settings = get_settings()
    init_db()
    cache = make_rank_cache(settings)
    city = sys.argv[1] if len(sys.argv) > 1 else None
    # 防呆：位置参数误传选项（如 --help 会被当成城市名建出假城市 + 空快照）
    if city and city.startswith("-"):
        print(__doc__)
        return 0

    with SessionLocal() as session:
        if city:
            # 城市行在对齐时才惰性创建：未知城市名直接拒绝，防止误建
            if city not in list_city_names(session):
                print(f"城市不存在：{city}（可选：{'、'.join(list_city_names(session))}）")
                return 1
        names = [city] if city else list_city_names(session)
        if not names:
            print("没有可运行的城市（先用 /api/v1/ingest 或 demo 灌入数据）。")
            return 0

        for name in names:
            try:
                job = run_city_pipeline(
                    session, name, settings=settings, cache=cache, top_n=DEFAULT_TOP_N
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] 失败：{exc}")
                continue
            print(f"[{name}] {job.status} stats={job.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())