"""采集入口（文档第 4 章）：多源采集 → 原始内容入库 / POI 直写实体。

用法（在 backend 目录下）：
    python scripts/run_crawl.py --list                          # 查看各源就绪状态
    python scripts/run_crawl.py --city 成都 --sources seed      # 只跑人工种子
    python scripts/run_crawl.py --city 成都 --mode full         # 全量（忽略增量游标）
    python scripts/run_crawl.py --city 成都 --force             # 忽略夜间暂停

供系统定时任务调用（文档 4.4；本项目不内置调度器）：
    # 每日 03:00 全量刷新各城市 POI 基础数据
    0 3 * * *   cd backend && python scripts/run_crawl.py --city 成都 --sources amap --mode full
    # 每 6 小时 增量抓取 P0 文本源
    0 */6 * * * cd backend && python scripts/run_crawl.py --city 成都 --sources seed,rss,html_list
    # 每日 05:00 触发清洗→抽取→打分→榜单流水线（既有脚本）
    0 5 * * *   cd backend && python scripts/run_pipeline.py

每次运行落一条 job_run(job_type="crawl")，可用 GET /api/v1/admin/jobs 查看。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.collectors import DEFAULT_SEED_PATH, describe_sources, run_crawl  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AlleyBite 采集入口（文档第 4 章）")
    parser.add_argument("--list", action="store_true", help="列出数据源与就绪状态")
    parser.add_argument("--city", default=None, help="城市名，如 成都")
    parser.add_argument("--sources", default=None, help="逗号分隔的源名；默认全部 P0 源")
    parser.add_argument(
        "--mode",
        choices=["incremental", "full"],
        default="incremental",
        help="incremental 用发布时间游标；full 忽略游标",
    )
    parser.add_argument("--seed-file", default=DEFAULT_SEED_PATH, help="人工种子文件路径")
    parser.add_argument("--force", action="store_true", help="忽略夜间暂停限制")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()

    if args.list:
        for row in describe_sources(settings):
            mark = "就绪  " if row["ready"] else "未就绪"
            print(f"{row['source']:<12} {mark} {row['note']}")
        return 0

    init_db()
    sources = (
        [s.strip() for s in args.sources.split(",") if s.strip()]
        if args.sources
        else None
    )

    with SessionLocal() as session:
        job = run_crawl(
            session,
            settings,
            sources=sources,
            mode=args.mode,
            city_hint=args.city,
            seed_path=args.seed_file,
            force=args.force,
        )

    print(f"job#{job.id} 状态={job.status}")
    stats = job.stats or {}
    if stats.get("skipped") == "night":
        print("夜间暂停：未执行采集（加 --force 可强制运行）")
        return 0
    for name, data in (stats.get("sources") or {}).items():
        print(
            f"  [{name}] {data.get('status')} "
            f"抓取={data.get('fetched')} 新增={data.get('new')} "
            f"重复={data.get('duplicated')} 失败={data.get('failed')} "
            f"POI新建={data.get('poi_written')} "
            f"403={data.get('http_403')} 429={data.get('http_429')}"
            + (f" 错误={data.get('error')}" if data.get("error") else "")
        )
    print(f"合计：{stats.get('totals')}")
    tripped = (stats.get("breaker") or {}).get("tripped") or []
    if tripped:
        print(f"熔断暂停：{tripped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())