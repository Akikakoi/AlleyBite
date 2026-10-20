"""同名店归并扫描（方向五第 1 批）：量化"归一化同名同城"重复组的规模与形态。

用法（backend 目录下）：
    python scripts/scan_duplicate_shops.py                 # 打印摘要
    python scripts/scan_duplicate_shops.py --out scan.md   # 追加输出 Markdown 报告

只读不改库，可在生产环境安全执行（docker compose exec api python scripts/...）。

四份清单：
  1. 同名同城组：name_norm + 城市分组后 >1 的活跃店，附地址两两重合度与处置建议
  2. 展示名分店后缀残留：剥括号后仍含"总店/分店/旗舰店/一店/二店"等
     （name_norm 层面 _MODIFIER_SUFFIX 已剥位置性后缀，此清单只反映展示名形态）
  3. 别名冲突：同一 alias_norm 挂在多个活跃店上，按同城（需裁决）/跨城（无害）分列
  4. 同品牌多店组：同城 brand_stem 相同 ≥2 家——is_chain 回填的数据源
"""

import argparse
import re
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, func  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.db.models import City, Mention as MentionRow, Restaurant, ShopAlias  # noqa: E402
from app.services.normalize import address_similarity, brand_stem  # noqa: E402

# 分店后缀词：用于"剥括号后仍含后缀"的展示名统计
_BRANCH_SUFFIXES = ("总店", "分店", "旗舰店", "创始店", "一号店", "二店", "三店", "一店")
_BRACKET_RE = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")
_VERDICT_MERGE = "疑似同店误拆 → 建议归并"
_VERDICT_BRANCH = "疑似真分店 → 建议分店实体化"
_VERDICT_GRAY = "灰区 → 人工裁决"


def _strip_brackets(text: str) -> str:
    return _BRACKET_RE.sub("", text or "")


def _mention_counts(session) -> dict[int, int]:
    rows = session.execute(
        select(MentionRow.restaurant_id, func.count(MentionRow.id)).group_by(
            MentionRow.restaurant_id
        )
    ).all()
    return {rid: cnt for rid, cnt in rows if rid is not None}


def _group_verdict(shops: list[dict]) -> str:
    """按组内地址两两重合度 + is_chain 给处置建议（参考值，最终人工裁决）。"""
    sims = [
        address_similarity(a["address"], b["address"])
        for a, b in combinations(shops, 2)
        if a["address"] and b["address"]
    ]
    chain = any(s["is_chain"] for s in shops)
    if sims and all(s is not None and s >= 0.5 for s in sims):
        return _VERDICT_MERGE
    if sims and all(s is not None and s < 0.3 for s in sims):
        return _VERDICT_BRANCH if chain else _VERDICT_GRAY
    return _VERDICT_GRAY


def scan(session) -> dict:
    settings = get_settings()
    _ = settings  # 占位：阈值后续从 settings 读取
    restaurants = session.scalars(
        select(Restaurant)
        .options()
        .where(Restaurant.status == "active")
    ).all()
    cities = {c.id: c.name for c in session.scalars(select(City)).all()}
    mention_counts = _mention_counts(session)

    rows = [
        {
            "id": r.id,
            "city": cities.get(r.city_id, "?"),
            "name": r.name,
            "name_norm": r.name_norm,
            "address": r.address,
            "is_chain": bool(r.is_chain),
            "has_poi": bool(r.poi_id),
            "mentions": mention_counts.get(r.id, 0),
            "liveness": r.liveness,
        }
        for r in restaurants
    ]

    # ---- 清单 1：同名同城组 ----
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["city"], row["name_norm"])].append(row)
    dup_groups = [
        {"key": k, "shops": sorted(v, key=lambda s: -s["mentions"])}
        for k, v in groups.items()
        if len(v) > 1
    ]
    for g in dup_groups:
        g["verdict"] = _group_verdict(g["shops"])

    # ---- 清单 2：展示名分店后缀残留（剥括号后仍含后缀词）----
    suffix_hits = [
        r
        for r in rows
        if any(s in _strip_brackets(r["name"]) for s in _BRANCH_SUFFIXES)
    ]

    # ---- 清单 3：alias_norm 冲突（同城需裁决 / 跨城无害）----
    alias_rows = session.execute(
        select(ShopAlias.alias_norm, ShopAlias.restaurant_id)
    ).all()
    alias_owner: dict[str, set[int]] = defaultdict(set)
    for norm, rid in alias_rows:
        alias_owner[norm].add(rid)
    city_of = {r["id"]: r["city"] for r in rows}
    alias_same_city: dict[str, list[int]] = {}
    alias_cross_city: dict[str, list[int]] = {}
    for norm, rids in alias_owner.items():
        if len(rids) < 2:
            continue
        ordered = sorted(rids)
        owner_cities = {city_of.get(rid, "?") for rid in ordered}
        if len(owner_cities) > 1:
            alias_cross_city[norm] = ordered
        else:
            alias_same_city[norm] = ordered

    # ---- 清单 4：同品牌多店组（同城 brand_stem ≥2，is_chain 回填数据源）----
    brand_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        brand_groups[(row["city"], brand_stem(row["name_norm"]))].append(row)
    brand_multi = [
        {"key": k, "shops": sorted(v, key=lambda s: -s["mentions"])}
        for k, v in brand_groups.items()
        if len(v) > 1
    ]

    chain_total = sum(1 for r in rows if r["is_chain"])
    return {
        "total": len(rows),
        "chain_total": chain_total,
        "dup_groups": dup_groups,
        "suffix_hits": suffix_hits,
        "alias_same_city": alias_same_city,
        "alias_cross_city": alias_cross_city,
        "brand_multi": brand_multi,
    }


def _fmt_shop(s: dict) -> str:
    return (
        f"#{s['id']} {s['name']}（{s['city']}，地址: {s['address'] or '无'}，"
        f"连锁: {'是' if s['is_chain'] else '否'}，POI: {'有' if s['has_poi'] else '无'}，"
        f"mention {s['mentions']}，{s['liveness']}）"
    )


def render(result: dict) -> str:
    lines = [
        "# 同名店归并扫描报告",
        "",
        f"- 活跃店总数：{result['total']}（其中 is_chain=True {result['chain_total']} 家）",
        f"- 同名同城重复组：{len(result['dup_groups'])} 组",
        f"- 展示名分店后缀残留（剥括号口径）：{len(result['suffix_hits'])} 家",
        f"- 别名冲突：同城 {len(result['alias_same_city'])} 个（需裁决），"
        f"跨城 {len(result['alias_cross_city'])} 个（无害）",
        f"- 同品牌多店组（同城 brand_stem ≥2）：{len(result['brand_multi'])} 组",
        "",
        "## 1. 同名同城组",
    ]
    if not result["dup_groups"]:
        lines.append("（无）")
    for g in result["dup_groups"]:
        city, norm = g["key"]
        lines.append(f"\n### [{city}] {norm} —— {g['verdict']}")
        for s in g["shops"]:
            lines.append(f"- {_fmt_shop(s)}")
    lines.append("\n## 2. 展示名分店后缀残留（剥括号口径，数据卫生观察项）")
    if not result["suffix_hits"]:
        lines.append("（无）")
    for s in result["suffix_hits"][:50]:
        lines.append(f"- {_fmt_shop(s)}")
    lines.append("\n## 3. alias_norm 冲突")
    lines.append("\n### 3.1 同城冲突（后续 mention 归并目标歧义，需人工裁决）")
    if not result["alias_same_city"]:
        lines.append("（无）")
    for norm, rids in list(result["alias_same_city"].items())[:50]:
        lines.append(f"- {norm} → restaurants {rids}")
    lines.append("\n### 3.2 跨城同名品牌（对齐按城隔离，无害，观察即可）")
    if not result["alias_cross_city"]:
        lines.append("（无）")
    for norm, rids in list(result["alias_cross_city"].items())[:50]:
        lines.append(f"- {norm} → restaurants {rids}")
    lines.append("\n## 4. 同品牌多店组（is_chain 回填数据源）")
    if not result["brand_multi"]:
        lines.append("（无）")
    for g in result["brand_multi"][:50]:
        city, stem = g["key"]
        lines.append(f"\n### [{city}] {stem}（{len(g['shops'])} 家）")
        for s in g["shops"]:
            lines.append(f"- {_fmt_shop(s)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同名店归并扫描（只读）")
    parser.add_argument("--out", help="Markdown 报告输出路径（可选）")
    args = parser.parse_args(argv)

    init_db()
    with SessionLocal() as session:
        result = scan(session)
        report = render(result)
        print(report)
        if args.out:
            Path(args.out).write_text(report, encoding="utf-8")
            print(f"\n报告已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
