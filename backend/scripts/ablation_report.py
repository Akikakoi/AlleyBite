"""榜单评分消融实验（只读，不动库、不写快照）。

用法（backend 目录下 / 容器内 /app）：
    python scripts/ablation_report.py                # 打印摘要
    python scripts/ablation_report.py --out 报告.md  # 落 markdown

做法：把评分模型（score-v3）的每个组件逐个关闭，在**完全相同的数据与 now**
下重算各城榜单，与基线对比排序稳定性：
- 特征消融：FEATURE_WEIGHTS 单项权重置 0（8 项）
- 组件消融：monkeypatch 关闭 time_decay / 惩罚项 / 硬规则 / 刷评降权 /
  老店加成；Settings 覆盖关闭贝叶斯平均 / suspect 降权；全特征均权

指标（对每城、对全城均值）：
- Spearman ρ：消融前后各店最终分的名次相关性（共同候选集）
- Top10 / Top30 重合率：|∩|/k
- 进 / 出 Top30 店数；最大名次上升 / 下降；平均分变化

可信度校验：
1. 复现校验：脚本内"一次加载 + 内存重算" vs 线上 collect_shop_scores
   逐城比对（同 now 同 settings，必须逐位一致）；
2. 快照比对：内存基线（build_rank_snapshot 同口径，按最终分排序）vs 库内
   最新快照的排序相关性（快照非当日生成，允许时衰引起的偏差，仅作参考）。
"""

import argparse
import copy
import math
import sys
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import Settings, get_settings
from app.db import SessionLocal, init_db
from app.db.models import City, RankSnapshot, Restaurant
from app.db.models import Mention as MentionRow
from app.services import scoring as S
from app.services.normalize import normalize_shop_name
from app.services.scoring import (
    aggregate_shop,
    apply_bayesian_smooth,
    apply_liveness_and_tenure,
    score_shop,
)
from app.services.scoring_service import _fact_from_row, collect_shop_scores
from sqlalchemy import select
from sqlalchemy.orm import joinedload

FEATURE_LABELS = {
    "real_mention_volume": "真实提及量(0.20)",
    "sentiment_strength": "口碑强度(0.18)",
    "locality": "本地化程度(0.15)",
    "price_friendliness": "价格亲民(0.12)",
    "tenure": "沉淀时长(0.10)",
    "non_chain": "非连锁(0.10)",
    "uniqueness": "独特性(0.08)",
    "paradox_good": "矛盾型好评(0.07)",
}


# --- 数据加载（一次全量，内存分桶） -------------------------------------------

def load_city_data(session):
    """按 raw_content.city_hint 分桶复刻 collect_shop_scores 的聚合口径。

    返回 ({city: bucket}, {restaurant_id: (name, city)})；
    bucket = {aligned: {rid: [MentionFact]}, meta: {rid: dict},
              addr: {rid: str}, unaligned: {norm: [MentionFact]}}
    """
    stmt = (
        select(MentionRow)
        .options(
            joinedload(MentionRow.raw_content),
            joinedload(MentionRow.restaurant).joinedload(Restaurant.city),
        )
        .order_by(MentionRow.id)
    )
    data: dict[str, dict] = defaultdict(lambda: {
        "aligned": defaultdict(list), "meta": {}, "addr": {}, "unaligned": defaultdict(list),
    })
    names: dict[int, tuple[str, str]] = {}
    n_rows = 0
    for row in session.scalars(stmt).all():
        n_rows += 1
        content = row.raw_content
        city = content.city_hint if content else None
        if not city:
            continue
        bucket = data[city]
        r = row.restaurant
        if r is not None:
            # 与 collect_shop_scores 逐行同口径：非 active / closed / 连锁不入桶
            if r.status != "active":
                continue
            if r.liveness == "closed":
                continue
            if r.is_chain:
                continue
            bucket["aligned"].setdefault(r.id, []).append(_fact_from_row(row))
            bucket["meta"][r.id] = {
                "name": r.name,
                "avg_price": r.avg_price,
                "city_name": r.city.name if r.city else None,
                "address": r.address or None,
                "liveness": r.liveness or "active",
            }
            names[r.id] = (r.name, city)
            if not bucket["addr"].get(r.id):
                addr = (row.address_text or row.area or "").strip()
                if addr:
                    bucket["addr"][r.id] = addr
        else:
            bucket["unaligned"].setdefault(
                normalize_shop_name(row.shop_name_raw), []
            ).append(_fact_from_row(row))
    print(f"加载 mention {n_rows} 条，覆盖 {len(data)} 个 city_hint")
    return dict(data), names


# --- 打分（复刻 build_rank_snapshot 管线，不落库） ----------------------------

def compute_city_scores(cd, settings, now):
    """返回按 as-built 口径（score-v3.1：平滑/加成后按最终分重排）排好序的 scores。"""
    scores = []
    for rid, facts in cd["aligned"].items():
        m = cd["meta"][rid]
        signals = aggregate_shop(
            f"restaurant:{rid}", facts, settings,
            avg_price=m["avg_price"], display_name=m["name"],
            city_hint=m["city_name"],
            address=m["address"] or cd["addr"].get(rid) or None,
            liveness=m["liveness"],
        )
        result = score_shop(signals, settings=settings, now=now)
        result.restaurant_id = rid
        scores.append(result)
    for norm, facts in cd["unaligned"].items():
        scores.append(
            score_shop(aggregate_shop(f"name:{norm}", facts, settings), settings=settings, now=now)
        )
    apply_bayesian_smooth(scores, settings.score_bayes_prior)
    apply_liveness_and_tenure(scores, settings=settings)
    # v3.1：按最终分重排（与 build_rank_snapshot 一致）
    scores.sort(key=lambda s: (-s.score, s.shop_name))
    return scores


def final_ranking(scores):
    """按最终分（平滑+加成后）排序——这是消融实验评估的"应然榜单"。"""
    ranked = [s for s in scores if s.restaurant_id is not None and not s.excluded]
    ranked.sort(key=lambda s: (-s.score, s.shop_name))
    return ranked


# --- 统计工具 -----------------------------------------------------------------

def _avg_ranks(vals):
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _avg_ranks(list(xs)), _avg_ranks(list(ys))
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _fmt_rho(rho):
    return f"{rho:.3f}" if rho is not None else "-"


# --- 变体定义与执行 ------------------------------------------------------------

@contextmanager
def patched(obj, attr, value):
    """同时支持模块属性与 dict 键（FEATURE_WEIGHTS 是模块级 dict）。"""
    is_dict = isinstance(obj, dict)
    old = obj[attr] if is_dict else getattr(obj, attr)
    if is_dict:
        obj[attr] = value
    else:
        setattr(obj, attr, value)
    try:
        yield
    finally:
        if is_dict:
            obj[attr] = old
        else:
            setattr(obj, attr, old)


def copy_settings(settings: Settings, **updates) -> Settings:
    try:
        return settings.model_copy(update=updates)
    except AttributeError:  # pydantic v1 兜底
        s2 = copy.deepcopy(settings)
        for k, v in updates.items():
            setattr(s2, k, v)
        return s2


def build_variants() -> list[dict]:
    variants = []

    def patch_weight(key):
        return lambda stack: stack.enter_context(patched(S.FEATURE_WEIGHTS, key, 0.0))

    def patch_uniform():
        def make(stack):
            for k in list(S.FEATURE_WEIGHTS):
                stack.enter_context(patched(S.FEATURE_WEIGHTS, k, 0.125))
        return make

    for key, label in FEATURE_LABELS.items():
        variants.append({
            "id": f"-{key}",
            "desc": f"去掉特征：{label}",
            "patches": [patch_weight(key)],
        })

    variants.append({
        "id": "uniform_weights",
        "desc": "八特征均权 0.125（权重信息量消融）",
        "patches": [patch_uniform()],
    })
    variants.append({
        "id": "-time_decay",
        "desc": "关闭时效衰减（time_decay 恒 1）",
        "patches": [lambda stack: stack.enter_context(
            patched(S, "compute_time_decay", lambda latest, now, hl: 1.0))],
    })
    variants.append({
        "id": "-bayesian_smooth",
        "desc": "关闭贝叶斯平均（score_bayes_prior=0）",
        "settings": {"score_bayes_prior": 0.0},
    })
    variants.append({
        "id": "-penalties",
        "desc": "关闭全部惩罚项（营销/网红/连锁/高价/卫生）",
        "patches": [lambda stack: stack.enter_context(
            patched(S, "compute_penalties", lambda signals, settings: []))],
    })
    variants.append({
        "id": "-hard_rules",
        "desc": "关闭硬规则剔除（广告占比/连锁黑名单/商场店/地图分类）",
        "patches": [lambda stack: stack.enter_context(
            patched(S, "apply_hard_rules",
                    lambda signals, settings: S.HardRuleResult()))],
    })
    variants.append({
        "id": "-burst_rule",
        "desc": "关闭刷评降权（burst 乘数恒 1）",
        "patches": [lambda stack: stack.enter_context(
            patched(S, "_has_burst", lambda facts, settings: False))],
    })
    variants.append({
        "id": "-tenure_bonus",
        "desc": "关闭老店加成（方向四 +0.15/+0.08）",
        "patches": [lambda stack: stack.enter_context(
            patched(S, "compute_tenure_bonus",
                    lambda first, now, settings: 0.0))],
    })
    variants.append({
        "id": "-suspect_downweight",
        "desc": "关闭 suspect 存活降权（乘数恒 1）",
        "settings": {"score_suspect_multiplier": 1.0},
    })
    return variants


def run_variant(vd, data, base_settings, now) -> dict[str, list]:
    settings = copy_settings(base_settings, **(vd.get("settings") or {}))
    out: dict[str, list] = {}
    with ExitStack() as stack:
        for make in vd.get("patches", []):
            make(stack)
        for city, cd in data.items():
            out[city] = compute_city_scores(cd, settings, now)
    return out


# --- 指标 ----------------------------------------------------------------------

def city_metrics(base_scores, var_scores):
    base_ranked = final_ranking(base_scores)
    var_ranked = final_ranking(var_scores)
    base_rank = {s.restaurant_id: i for i, s in enumerate(base_ranked, 1)}
    var_rank = {s.restaurant_id: i for i, s in enumerate(var_ranked, 1)}
    base_score = {s.restaurant_id: s.score for s in base_ranked}
    var_score = {s.restaurant_id: s.score for s in var_ranked}

    k = min(10, len(base_ranked))
    top10_b = {r.restaurant_id for r in base_ranked[:k]}
    top10_v = {r.restaurant_id for r in var_ranked[:k]}
    k30 = min(30, len(base_ranked))
    top30_b = {r.restaurant_id for r in base_ranked[:k30]}
    top30_v = {r.restaurant_id for r in var_ranked[:k30]}

    common = sorted(set(base_rank) & set(var_rank))
    rho = spearman([base_score[r] for r in common], [var_score[r] for r in common])
    deltas = [var_rank[r] - base_rank[r] for r in common]
    return {
        "n_base": len(base_ranked),
        "n_var": len(var_ranked),
        "rho": rho,
        "top10": len(top10_b & top10_v) / k if k else None,
        "top30": len(top30_b & top30_v) / k30 if k30 else None,
        "enter": len(top30_v - top30_b),
        "leave": len(top30_b - top30_v),
        "max_up": max((-d for d in deltas), default=0),   # 名次上升（变小）最大值
        "max_down": max((d for d in deltas), default=0),
        "dscore": (sum(var_score[r] - base_score[r] for r in common) / len(common))
        if common else 0.0,
        "top1_changed": (
            bool(base_ranked) and bool(var_ranked)
            and base_ranked[0].restaurant_id != var_ranked[0].restaurant_id
        ),
        "movers": sorted(
            ((abs(var_rank[r] - base_rank[r]), base_rank[r], var_rank[r], r)
             for r in common), reverse=True,
        )[:5],
    }


# --- 校验 ----------------------------------------------------------------------

def validate_replication(session, cities, data, settings, now):
    """内存复算 vs collect_shop_scores：同 now 同 settings，候选集与分数必须一致。

    v3.1 起榜单按平滑/加成后的最终分排序，而 collect_shop_scores 返回的是
    平滑前的排序，故此处比对候选集合与逐店分数（排序正确性由快照比对覆盖）。
    """
    bad = []
    for city in cities:
        if city not in data:
            continue
        ref = collect_shop_scores(session, city_hint=city, settings=settings, now=now)
        apply_bayesian_smooth(ref, settings.score_bayes_prior)
        apply_liveness_and_tenure(ref, settings=settings)
        ref_ids = [s.restaurant_id for s in ref if s.restaurant_id is not None and not s.excluded]
        ref_sc = {s.restaurant_id: s.score for s in ref if s.restaurant_id is not None}
        mine = compute_city_scores(data[city], settings, now)
        mine_ids = [s.restaurant_id for s in mine if s.restaurant_id is not None and not s.excluded]
        mine_sc = {s.restaurant_id: s.score for s in mine if s.restaurant_id is not None}
        score_ok = all(
            abs(ref_sc[r] - mine_sc.get(r, float("nan"))) < 1e-6 for r in ref_sc
        ) and set(ref_sc) == set(mine_sc)
        if set(ref_ids) != set(mine_ids) or not score_ok:
            bad.append(city)
    return bad


def compare_snapshot(session, cities, baseline, names):
    """内存基线（as-built 口径）vs 库内最新快照。快照非当日生成，偏差仅作参考。"""
    rows = []
    for city in cities:
        snap = session.scalar(
            select(RankSnapshot).join(City, RankSnapshot.city_id == City.id)
            .where(City.name == city)
            .order_by(RankSnapshot.generated_at.desc(), RankSnapshot.id.desc())
        )
        if snap is None or not snap.items:
            continue
        scores = baseline.get(city)
        if scores is None:
            continue
        asb = [s.restaurant_id for s in scores if s.restaurant_id is not None and not s.excluded]
        snap_ids = [i.get("restaurant_id") for i in snap.items if i.get("restaurant_id")]
        common = [r for r in asb if r in set(snap_ids)]
        if len(common) < 3:
            rows.append((city, snap.generated_at, len(snap_ids), "-", "-", "-"))
            continue
        snap_pos = {rid: i for i, rid in enumerate(snap_ids)}
        asb_pos = {rid: i for i, rid in enumerate(asb)}
        rho = spearman([asb_pos[r] for r in common], [snap_pos[r] for r in common])
        k30 = min(30, len(snap_ids))
        ov = len(set(snap_ids[:k30]) & set(asb[:k30])) / k30
        rows.append((
            city, snap.generated_at.strftime("%m-%d %H:%M"), len(snap_ids),
            _fmt_rho(rho), f"{ov:.0%}", f"{len(common)}",
        ))
    return rows


# --- 主流程 --------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="榜单评分消融实验（只读）")
    parser.add_argument("--out", default="", help="markdown 输出路径（缺省打印 stdout）")
    args = parser.parse_args()

    init_db()
    now = datetime.now(timezone.utc)
    settings = get_settings()

    lines: list[str] = []
    lines.append("# 榜单评分消融实验（score-v3）\n")
    lines.append(f"- 运行时间：{now:%Y-%m-%d %H:%M} UTC；数据 = 生产库全量 mention（只读）")
    lines.append("- 口径：变体关闭单个组件后重算各城榜单，与基线按**最终分排序**对比；")
    lines.append("  Top-K 重合率 = |∩|/k（k 按基线候选数截断）；ρ = 共同候选集名次 Spearman\n")

    with SessionLocal() as session:
        city_rows = session.execute(select(City.name)).all()
        cities = [c for (c,) in city_rows]
        data, names = load_city_data(session)

        # 1) 复现校验
        bad = validate_replication(session, cities, data, settings, now)
        used_cities = [c for c in cities if c in data]
        lines.append("## 复现校验\n")
        lines.append(f"- 内存复算 vs collect_shop_scores（逐城同 now 比对）："
                     f"{len(used_cities) - len(bad)}/{len(used_cities)} 城一致"
                     + (f"；**不一致：{bad}**（消融读数对这几城不可信）" if bad else "，全部逐位一致 ✅"))
        lines.append("")

        # 2) 基线（as-built = 最终分排序，v3.1 与 build_rank_snapshot 一致）
        baseline = run_variant({}, data, settings, now)
        lines.append("## 口径说明\n")
        lines.append("- score-v3.1 起 build_rank_snapshot 平滑/加成后按最终分重排，"
                     "复算口径与其一致；排序正确性由下方快照比对覆盖（ρ 应≈1.0）\n")

        # 3) 快照比对
        snap_rows = compare_snapshot(session, used_cities, baseline, names)
        lines.append("## 基线 vs 库内最新快照（参考）\n")
        lines.append("快照非当日生成，时衰会造成偏差；ρ 明显偏低的城市提示快照陈旧或复算偏差。\n")
        lines.append("| 城市 | 快照生成 | 条数 | ρ | Top30 重合 | 共同候选 |")
        lines.append("|---|---|---|---|---|---|")
        for row in snap_rows:
            lines.append("| " + " | ".join(str(x) for x in row) + " |")
        lines.append("")

        # 4) 消融
        variants = build_variants()
        results = []
        for vd in variants:
            res = run_variant(vd, data, settings, now)
            metrics = {c: city_metrics(baseline[c], res[c]) for c in used_cities}
            results.append((vd, metrics))
            print(f"done: {vd['id']}")

    # 汇总表（按 ρ 升序 = 影响从大到小）
    def agg(metrics):
        rhos = [m["rho"] for m in metrics.values() if m["rho"] is not None]
        t10 = [m["top10"] for m in metrics.values() if m["top10"] is not None]
        t30 = [m["top30"] for m in metrics.values() if m["top30"] is not None]
        return {
            "rho": sum(rhos) / len(rhos) if rhos else None,
            "top10": sum(t10) / len(t10) if t10 else None,
            "top30": sum(t30) / len(t30) if t30 else None,
            "enter": sum(m["enter"] for m in metrics.values()),
            "leave": sum(m["leave"] for m in metrics.values()),
            "top1": sum(1 for m in metrics.values() if m["top1_changed"]),
        }

    lines.append("## 消融总表（按影响从大到小）\n")
    lines.append("ρ 越低 / 重合率越低 / 进出 Top30 越多 → 该组件对榜单影响越大。\n")
    lines.append("| 变体 | 说明 | ρ均值 | Top10重合 | Top30重合 | 进Top30 | 出Top30 | Top1换人城数 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    summary = sorted(results, key=lambda x: agg(x[1])["rho"] if agg(x[1])["rho"] is not None else 1)
    for vd, metrics in summary:
        a = agg(metrics)
        lines.append(
            f"| `{vd['id']}` | {vd['desc']} | {_fmt_rho(a['rho'])} "
            f"| {a['top10']:.0%} | {a['top30']:.0%} | {a['enter']} | {a['leave']} | {a['top1']} |"
        )
    lines.append("")

    # 每变体分城表 + movers（只给有影响的）
    for vd, metrics in results:
        a = agg(metrics)
        quiet = a["rho"] is not None and a["rho"] > 0.995 and a["enter"] == 0 and a["leave"] == 0
        lines.append(f"### `{vd['id']}` —— {vd['desc']}\n")
        if quiet:
            lines.append("（各城名次基本不变，分城明细省略）\n")
            continue
        lines.append("| 城市 | 基线候选 | ρ | Top10重合 | Top30重合 | 进/出Top30 | 最大↑ | 最大↓ | Δ均分 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for city in used_cities:
            m = metrics.get(city)
            if m is None:
                continue
            lines.append(
                f"| {city} | {m['n_base']} | {_fmt_rho(m['rho'])} "
                f"| {m['top10']:.0%} | {m['top30']:.0%} | {m['enter']}/{m['leave']} "
                f"| {m['max_up']} | {m['max_down']} | {m['dscore']:+.2f} |"
            )
        all_movers = sorted(
            ((city, *mv) for city, ms in metrics.items() for mv in ms["movers"]),
            key=lambda x: -x[1],
        )[:5]
        if all_movers:
            lines.append("")
            lines.append("名次变化最大的店（基线名次 → 消融后名次）：\n")
            lines.append("| 城市 | 店名 | 基线 | 消融后 | 变化 |")
            lines.append("|---|---|---|---|---|")
            for city, _, b_rank, v_rank, rid in all_movers:
                nm = names.get(rid, ("?", city))
                lines.append(f"| {city} | {nm[0]} | {b_rank} | {v_rank} | {b_rank - v_rank:+d} |")
        lines.append("")

    report = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"已写出 {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
