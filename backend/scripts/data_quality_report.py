"""数据质量补测（文档 12 章 M2 通过标准 / 14 章验收标准）。

产出**可复核**的口径报告，覆盖文档明确的四条量化标准：

    1. 实体去重后重复店率 < 5%
       —— 同城市 active 店铺两两比对综合相似度（名称 + 地址），
          达到 ALIGN_MATCH_THRESHOLD（默认 0.85）即计一对疑似重复；
          口径：重复对数 / 店铺数。
    2. 抽取字段完整率（地址 / 菜名 / 情感）≥ 80%
       —— mention 中 address_text 非空、dishes 非空、sentiment 已判定 的比例。
    3. 榜单 Top20 人工评审认可率 ≥ 70%
       —— 无法自动判定，脚本导出评审台账 CSV（含证据片段），人工填写 verdict 后
          用 --review 回读统计。
    4. 已知网红店误入 Top20 比例 < 10%
       —— 快照 Top20 条目名称/口碑关键词命中网红词库（含 mention 证据）即计误入。

用法（在 backend 目录下）：
    python scripts/data_quality_report.py                    # 跑全部城市
    python scripts/data_quality_report.py --city 成都
    python scripts/data_quality_report.py --export-review    # 导出 Top20 评审台账
    python scripts/data_quality_report.py --review <csv>     # 回读台账统计认可率
    python scripts/data_quality_report.py --json             # 输出 JSON（供 CI/留档）

说明：本脚本**只读**，不写任何业务表。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.db.models import (  # noqa: E402
    City,
    Mention,
    RankSnapshot,
    Restaurant,
)
from app.services.alignment import composite_similarity  # noqa: E402
from app.services.scoring import INFLUENCER_KEYWORDS  # noqa: E402

REVIEW_COLUMNS = (
    "city",
    "rank",
    "restaurant_id",
    "name",
    "score",
    "avg_price",
    "address",
    "praise_keywords",
    "evidence",
    "verdict",
)


def _duplicate_pairs(
    restaurants: list[Restaurant], settings
) -> list[tuple[int, int, float]]:
    """同城 active 店铺两两比对，返回相似度 ≥ match 阈值的疑似重复对。"""
    pairs: list[tuple[int, int, float]] = []
    for left, right in combinations(restaurants, 2):
        score = composite_similarity(left.name_norm, left.address, right, settings)
        if score >= settings.align_match_threshold:
            pairs.append((left.id, right.id, round(score, 4)))
    return pairs


def duplicate_rate(session, settings) -> dict:
    """口径 1：重复店率 = 疑似重复对数 / 店铺总数。"""
    total_restaurants = 0
    total_pairs = 0
    per_city: list[dict] = []
    for city in session.scalars(select(City).order_by(City.id)).all():
        restaurants = list(
            session.scalars(
                select(Restaurant).where(
                    Restaurant.city_id == city.id, Restaurant.status == "active"
                )
            ).all()
        )
        if not restaurants:
            continue
        pairs = _duplicate_pairs(restaurants, settings)
        total_restaurants += len(restaurants)
        total_pairs += len(pairs)
        per_city.append(
            {
                "city": city.name,
                "restaurants": len(restaurants),
                "duplicate_pairs": len(pairs),
                "rate": round(len(pairs) / len(restaurants), 4) if restaurants else 0.0,
                "samples": pairs[:10],
            }
        )
    rate = round(total_pairs / total_restaurants, 4) if total_restaurants else 0.0
    return {
        "metric": "重复店率",
        "criterion": "< 5%",
        "value": rate,
        "passed": rate < 0.05,
        "restaurants": total_restaurants,
        "duplicate_pairs": total_pairs,
        "per_city": per_city,
    }


def field_completeness(session) -> dict:
    """口径 2：抽取字段完整率 = 三字段齐全的 mention / mention 总数。"""
    mentions = session.scalars(
        select(Mention).where(Mention.restaurant_id.is_not(None))
    ).all()
    total = len(mentions)
    per_field: dict[str, int] = {}
    complete = 0
    for row in mentions:
        hits = {
            "address_text": bool((row.address_text or "").strip()),
            "dishes": bool(row.dishes),
            "sentiment": bool((row.sentiment or "").strip()),
        }
        for key, ok in hits.items():
            per_field[key] = per_field.get(key, 0) + int(ok)
        if all(hits.values()):
            complete += 1
    rate = round(complete / total, 4) if total else 0.0
    return {
        "metric": "抽取字段完整率（地址/菜名/情感）",
        "criterion": "≥ 80%",
        "value": rate,
        "passed": rate >= 0.8,
        "mentions": total,
        "complete_mentions": complete,
        "per_field_rate": {
            key: round(hits / total, 4) if total else 0.0
            for key, hits in per_field.items()
        },
    }


def _influencer_hits(text_blob: str) -> list[str]:
    return [kw for kw in INFLUENCER_KEYWORDS if kw and kw in text_blob]


def influencer_intrusion(session, top_n: int = 20) -> dict:
    """口径 4：Top20 中命中网红词库的条目占比（含 mention 证据文本）。"""
    per_city: list[dict] = []
    total_items = 0
    total_hits = 0
    for city in session.scalars(select(City).order_by(City.id)).all():
        snapshot = session.scalar(
            select(RankSnapshot)
            .where(RankSnapshot.city_id == city.id)
            .order_by(RankSnapshot.id.desc())
            .limit(1)
        )
        if snapshot is None:
            continue
        items = (snapshot.items or [])[:top_n]
        hits: list[dict] = []
        for item in items:
            restaurant_id = item.get("restaurant_id")
            blob = " ".join(
                [
                    str(item.get("name") or ""),
                    " ".join(item.get("praise_keywords") or []),
                ]
            )
            mentions = (
                session.scalars(
                    select(Mention).where(Mention.restaurant_id == restaurant_id)
                ).all()
                if restaurant_id
                else []
            )
            for row in mentions:
                blob += " " + " ".join(
                    part
                    for part in (
                        " ".join(row.praise_keywords or []),
                        " ".join(row.complaints or []),
                        row.evidence_span or "",
                    )
                    if part
                )
            matched = _influencer_hits(blob)
            if matched:
                hits.append(
                    {
                        "rank": item.get("rank"),
                        "restaurant_id": restaurant_id,
                        "name": item.get("name"),
                        "keywords": matched,
                    }
                )
        total_items += len(items)
        total_hits += len(hits)
        per_city.append(
            {
                "city": city.name,
                "snapshot_id": snapshot.id,
                "items": len(items),
                "intrusions": len(hits),
                "rate": round(len(hits) / len(items), 4) if items else 0.0,
                "hits": hits,
            }
        )
    rate = round(total_hits / total_items, 4) if total_items else 0.0
    return {
        "metric": f"网红店误入 Top{top_n} 比例",
        "criterion": "< 10%",
        "value": rate,
        "passed": rate < 0.1,
        "top_n": top_n,
        "items": total_items,
        "intrusions": total_hits,
        "per_city": per_city,
    }


def export_review_sheet(session, path: Path, top_n: int = 20) -> int:
    """导出 Top20 人工评审台账（CSV）；录入 verdict 后用 --review 回读。

    verdict 取值：y（认可）/ n（不认可）/ 留空（未评审，不计入分母）。
    """
    rows: list[dict] = []
    for city in session.scalars(select(City).order_by(City.id)).all():
        snapshot = session.scalar(
            select(RankSnapshot)
            .where(RankSnapshot.city_id == city.id)
            .order_by(RankSnapshot.id.desc())
            .limit(1)
        )
        if snapshot is None:
            continue
        for item in (snapshot.items or [])[:top_n]:
            restaurant_id = item.get("restaurant_id")
            evidence = ""
            if restaurant_id:
                mentions = session.scalars(
                    select(Mention)
                    .where(Mention.restaurant_id == restaurant_id)
                    .limit(3)
                ).all()
                evidence = " | ".join(
                    (m.evidence_span or m.shop_name_raw or "")[:60] for m in mentions
                )
            rows.append(
                {
                    "city": city.name,
                    "rank": item.get("rank"),
                    "restaurant_id": restaurant_id,
                    "name": item.get("name"),
                    "score": round(float(item.get("score") or 0.0), 2),
                    "avg_price": item.get("avg_price"),
                    "address": item.get("address"),
                    "praise_keywords": "/".join(item.get("praise_keywords") or []),
                    "evidence": evidence,
                    "verdict": "",
                }
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(REVIEW_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_review_sheet(path: Path) -> dict:
    """回读台账：认可率 = verdict=y 的条数 / 已填 verdict 的条数。"""
    reviewed = 0
    accepted = 0
    with path.open("r", encoding="utf-8-sig") as fp:
        for row in csv.DictReader(fp):
            verdict = (row.get("verdict") or "").strip().lower()
            if verdict not in ("y", "n"):
                continue
            reviewed += 1
            accepted += int(verdict == "y")
    rate = round(accepted / reviewed, 4) if reviewed else 0.0
    return {
        "metric": "榜单 Top20 人工评审认可率",
        "criterion": "≥ 70%",
        "value": rate,
        "passed": rate >= 0.7,
        "reviewed": reviewed,
        "accepted": accepted,
        "sheet": str(path),
    }


def _print_section(report: dict) -> None:
    mark = "通过" if report["passed"] else "未达标"
    print(f"\n【{report['metric']}】标准 {report['criterion']}")
    print(f"  实测值：{report['value']:.2%}　判定：{mark}")
    for key in ("restaurants", "duplicate_pairs", "mentions", "complete_mentions",
                "items", "intrusions", "reviewed", "accepted"):
        if key in report:
            print(f"  {key}：{report[key]}")
    if "per_field_rate" in report:
        for field, rate in report["per_field_rate"].items():
            print(f"    {field} 完整率：{rate:.2%}")
    for city in report.get("per_city", []):
        print(
            f"  - {city['city']}："
            + (
                f"店铺 {city['restaurants']}，疑似重复对 {city['duplicate_pairs']}，"
                f"重复率 {city['rate']:.2%}"
                if "restaurants" in city
                else f"Top {city['items']}，误入 {city['intrusions']}，比例 {city['rate']:.2%}"
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AlleyBite 数据质量补测报告（文档 12/14 章）")
    parser.add_argument("--city", default=None, help="仅统计指定城市（默认全部）")
    parser.add_argument("--top-n", type=int, default=20, help="Top N（默认 20）")
    parser.add_argument("--export-review", action="store_true", help="导出 Top20 评审台账 CSV")
    parser.add_argument("--review", default=None, help="回读评审台账 CSV 统计认可率")
    parser.add_argument("--out", default=None, help="台账 CSV 路径（默认 ./data_quality_review.csv）")
    parser.add_argument("--json", action="store_true", help="额外输出 JSON（供留档/CI）")
    args = parser.parse_args(argv)

    sheet = Path(args.out or (ROOT / "data_quality_review.csv"))

    if args.review:
        report = read_review_sheet(Path(args.review))
        _print_section(report)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1

    if args.export_review:
        with SessionLocal() as session:
            count = export_review_sheet(session, sheet, top_n=args.top_n)
        print(f"已导出 {count} 条 Top{args.top_n} 待评审条目 → {sheet}")
        print("请填写 verdict 列（y=认可 / n=不认可），再执行："
              f" python scripts/data_quality_report.py --review \"{sheet}\"")
        return 0

    settings = get_settings()
    with SessionLocal() as session:
        if args.city:
            city = session.scalar(select(City).where(City.name == args.city))
            if city is None:
                print(f"城市不存在：{args.city}")
                return 2
            # 单城口径：临时复用全量函数后过滤，保持口径一致
            dup = duplicate_rate(session, settings)
            dup["per_city"] = [c for c in dup["per_city"] if c["city"] == args.city]
            cities = [c for c in dup["per_city"]]
            dup["restaurants"] = sum(c["restaurants"] for c in cities)
            dup["duplicate_pairs"] = sum(c["duplicate_pairs"] for c in cities)
            dup["value"] = (
                round(dup["duplicate_pairs"] / dup["restaurants"], 4)
                if dup["restaurants"]
                else 0.0
            )
            dup["passed"] = dup["value"] < 0.05
            infl = influencer_intrusion(session, top_n=args.top_n)
            infl["per_city"] = [c for c in infl["per_city"] if c["city"] == args.city]
            infl["items"] = sum(c["items"] for c in infl["per_city"])
            infl["intrusions"] = sum(c["intrusions"] for c in infl["per_city"])
            infl["value"] = (
                round(infl["intrusions"] / infl["items"], 4) if infl["items"] else 0.0
            )
            infl["passed"] = infl["value"] < 0.1
        else:
            dup = duplicate_rate(session, settings)
            infl = influencer_intrusion(session, top_n=args.top_n)

        complete = field_completeness(session)

    print("=" * 68)
    print("AlleyBite 数据质量补测报告（文档 12 章 M2 / 14 章验收）")
    print("=" * 68)
    for report in (dup, complete, infl):
        _print_section(report)

    reviewed = read_review_sheet(sheet) if sheet.exists() else None
    if reviewed is None:
        print("\n【榜单 Top20 人工评审认可率】标准 ≥ 70%")
        print(f"  尚无评审台账（{sheet}）。请先执行："
              " python scripts/data_quality_report.py --export-review")
    else:
        _print_section(reviewed)

    if args.json:
        payload = {"duplicate_rate": dup, "field_completeness": complete,
                   "influencer_intrusion": infl, "manual_review": reviewed}
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    auto_passed = dup["passed"] and complete["passed"] and infl["passed"]
    print("\n自动口径判定：" + ("全部达标" if auto_passed else "存在未达标项"))
    return 0 if auto_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())