"""方向四阈值校准（2026-10-23 复盘点）：各城 miss 分布分析，只读不动库。

用法（backend 目录下）：
    python scripts/liveness_miss_report.py                # 打印摘要到 stdout
    python scripts/liveness_miss_report.py --out 报告.md  # 落 markdown（容器内用相对路径）

分析口径：
- 底座 = poi_id 非空；已见 = last_seen_at 非空（真失联信号仅来自已见店，
  从未命中的 POI 盲区店不参与 miss 累计）；
- 分桶看 miss_count 分布；交叉"近 N 天有 mention"（口碑证据可担保存活，
  POI 检索盲区导致的误伤候选）；
- 模拟阈值对 (5,10)/(7,14)/(10,20) 的 suspect/closed 命中量与误伤率。
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.db.models import City, JobRun, Restaurant
from app.services.scoring import to_utc

PAIRS = [(5, 10), (7, 14), (10, 20)]
VOUCHED_DAYS = 30


def _naive(dt):
    dt = to_utc(dt)
    return dt.replace(tzinfo=None) if dt else None


def poi_rounds(session) -> tuple[dict, str, str]:
    """近 60 天 crawl job 里 amap 实际跑掉的轮数（按城计数，排除夜间跳过/熔断/未配置）。

    生产 beat 对每城单独落 job（city_hint=城市名）；city_hint 为空的算全局轮，
    计入每个城。返回 ({城市: 轮数}, 最早, 最新)。
    """
    jobs = session.scalars(
        select(JobRun)
        .where(JobRun.job_type == "crawl", JobRun.finished_at.is_not(None))
        .order_by(JobRun.id.desc())
        .limit(400)
    ).all()
    per_city: dict[str, int] = defaultdict(int)
    dates = []
    for job in jobs:
        stats = job.stats or {}
        if stats.get("skipped"):
            continue
        amap = (stats.get("sources") or {}).get("amap") or {}
        if amap.get("status") not in ("ok", "partial"):
            continue
        started = _naive(job.started_at)
        dates.append(started)
        hint = stats.get("city_hint")
        if hint:
            per_city[hint] += 1
        else:
            per_city["__global__"] += 1
    if not dates:
        return {}, "-", "-"
    return dict(per_city), min(dates).strftime("%m-%d"), max(dates).strftime("%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser(description="各城 miss 分布分析（只读）")
    parser.add_argument("--out", default="", help="markdown 输出路径（缺省只打印 stdout）")
    args = parser.parse_args()

    init_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        rounds_map, first_round, last_round = poi_rounds(session)
        global_rounds = rounds_map.pop("__global__", 0)
        rows = session.execute(
            select(
                City.name,
                Restaurant.miss_count,
                Restaurant.liveness,
                Restaurant.poi_id,
                Restaurant.last_seen_at,
                Restaurant.last_mentioned_at,
                Restaurant.name,
            ).join(City, City.id == Restaurant.city_id)
        ).all()

    # city -> 聚合容器
    agg: dict[str, dict] = defaultdict(lambda: {
        "base": 0, "seen": 0, "never": 0,
        "b0": 0, "b14": 0, "b59": 0, "b10": 0,
        "state": defaultdict(int), "max_miss": 0,
        "hi_miss": 0, "vouched30": 0, "vouched_null": 0,
    })
    examples = []
    for city, miss, liveness, poi_id, last_seen, last_mentioned, rname in rows:
        a = agg[city]
        if poi_id is None:
            continue
        a["base"] += 1
        if last_seen is None:
            a["never"] += 1
            continue
        a["seen"] += 1
        miss = miss or 0
        a["state"][liveness or "active"] += 1
        a["max_miss"] = max(a["max_miss"], miss)
        if liveness == "closed":
            continue  # 已 closed 的不再进分布桶（终端态）
        if miss == 0:
            a["b0"] += 1
        elif miss <= 4:
            a["b14"] += 1
        elif miss <= 9:
            a["b59"] += 1
        else:
            a["b10"] += 1
        if miss >= 5:
            a["hi_miss"] += 1
            lm = _naive(last_mentioned)
            if lm is None:
                a["vouched_null"] += 1
            elif (now - lm).days <= VOUCHED_DAYS:
                a["vouched30"] += 1
                examples.append(((now - lm).days, city, rname, miss, lm.strftime("%m-%d")))

    lines = []
    lines.append("# 各城 miss 分布分析（阈值校准，2026-10-23）\n")
    lines.append(f"- 近 60 天 amap POI 刷新实跑轮数：按城 {len(rounds_map)} 城 + 全局 {global_rounds} 轮"
                 f"（{first_round} ~ {last_round}）")
    lines.append(f"- 口径：底座=poi_id 非空；已见=last_seen_at 非空；closed 店不进分布桶；"
                 f"「近30天有口碑」= last_mentioned_at 距今 ≤{VOUCHED_DAYS} 天\n")

    hdr = ("| 城市 | 刷新轮次 | 底座 | 已见 | 盲区率 | miss0 | 1-4 | 5-9 | ≥10 | max | active | suspect | closed | reloc | 高miss | 近30天有口碑 | 无口碑 |")
    sep = "|---" * 16 + "|"
    lines += [hdr, sep]
    total = defaultdict(int)
    for city in sorted(agg, key=lambda c: -agg[c]["base"]):
        a = agg[city]
        rounds = rounds_map.get(city, 0) + global_rounds
        blind = f"{a['never'] / a['base']:.0%}" if a["base"] else "-"
        lines.append(
            f"| {city} | {rounds} | {a['base']} | {a['seen']} | {blind} "
            f"| {a['b0']} | {a['b14']} | {a['b59']} | {a['b10']} | {a['max_miss']} "
            f"| {a['state']['active']} | {a['state']['suspect']} | {a['state']['closed']} | {a['state']['relocated']} "
            f"| {a['hi_miss']} | {a['vouched30']} | {a['vouched_null']} |"
        )
        for k in ("base", "seen", "never", "b0", "b14", "b59", "b10", "hi_miss", "vouched30", "vouched_null"):
            total[k] += a[k]
        total["max_miss"] = max(total["max_miss"], a["max_miss"])
        total["rounds"] = max(total["rounds"], rounds)
    blind = f"{total['never'] / total['base']:.0%}" if total["base"] else "-"
    lines.append(
        f"| **合计** | ≤{total['rounds']}/城 | {total['base']} | {total['seen']} | {blind} "
        f"| {total['b0']} | {total['b14']} | {total['b59']} | {total['b10']} | {total['max_miss']} "
        f"| | | | {total['hi_miss']} | {total['vouched30']} | {total['vouched_null']} |\n"
    )

    lines.append("## 阈值模拟（在已见、非 closed、非 relocated 店上）\n")
    lines.append("| 阈值对 | 将判 suspect | 将判 closed | 合计占已见 | 其中近30天有口碑（误伤候选） |")
    lines.append("|---|---|---|---|---|")
    # 模拟需重新按行算（closed/relocated 也算进分母之外）
    sim_rows = []
    for city, miss, liveness, poi_id, last_seen, last_mentioned, rname in rows:
        if poi_id is None or last_seen is None or liveness in ("closed", "relocated"):
            continue
        sim_rows.append((city, miss or 0, _naive(last_mentioned)))
    for s, c in PAIRS:
        sus = sum(1 for _, m, _ in sim_rows if s <= m < c)
        clo = sum(1 for _, m, _ in sim_rows if m >= c)
        vouched = sum(
            1 for _, m, lm in sim_rows
            if m >= s and lm is not None and (now - lm).days <= VOUCHED_DAYS
        )
        pct = f"{(sus + clo) / len(sim_rows):.1%}" if sim_rows else "-"
        lines.append(f"| ({s},{c}) | {sus} | {clo} | {pct} | {vouched} |")
    lines.append("")

    lines.append("## miss≥5 且近 30 天有口碑的样例（按口碑新鲜度排序，前 20）\n")
    examples.sort(key=lambda x: x[0])
    if examples:
        lines.append("| 距上次口碑 | 城市 | 店名 | miss | last_mentioned |")
        lines.append("|---|---|---|---|---|")
        for days, city, rname, miss, lm in examples[:20]:
            lines.append(f"| {days} 天 | {city} | {rname} | {miss} | {lm} |")
    else:
        lines.append("（无）")
    lines.append("")

    report = "\n".join(lines)
    print(f"seen={total['seen']} hi_miss={total['hi_miss']} vouched30={total['vouched30']} cities={len(rounds_map)}")
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"已写出 {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
