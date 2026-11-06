"""同品牌口碑提示（产品定位 2026-10-23「证据不确定性显式化」，2026-11-06 落地）。

方向五生产实测结论：用户看到的"A店/B店"是 mention 级现象——无地址的分店提及
在实体对齐时归并进品牌条目，单店条目的口碑可能混有同城其他分店的声音，且这种
情况多数不伴随兄弟实体（分店没有独立 restaurant 行）。

本模块不改数据、不重算打分，只把这种不确定性显式化，两个渠道任一命中即提示：

A. 实体级：同城存在同 brand_stem 的兄弟实体 ≥1 家，且本店有未注明地址的口碑
  （口碑挂在哪条分店无法用地址消歧）。
B. mention 级：存在未注明地址、且提及名与本店归一名不同的分店式提及
  （如口碑说「乐三姐飘香馆二店」却归并进了「乐三姐飘香馆一店」条目）。
  分店词表与 normalize 的品牌编号词/位置性后缀同源，裸品牌简称（如「乐三姐」）
  刻意不触发——口语简称太常见，触发会造成横幅噪音。

所有口碑都带地址（对齐时可按地址消歧）时不提示，避免误报。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Mention as MentionRow, Restaurant
from .normalize import brand_stem, normalize_shop_name

# 分店式命名标记：提及名含任一词且与本店归一名不同 → 该口碑可能来自其他分店
_BRANCH_TOKENS = (
    "一号店",
    "一店",
    "二店",
    "三店",
    "总店",
    "分店",
    "旗舰店",
    "新店",
    "老店",
)


def _sibling_count(session: Session, restaurant: Restaurant) -> int:
    """同城同品牌兄弟实体数量（活跃店，不含本店）。"""
    stem = brand_stem(restaurant.name_norm)
    if not stem:
        return 0
    # brand_stem 是纯函数无法下推 SQL：只取同城活跃店的 id/name_norm 两列，内存过滤
    candidates = session.execute(
        select(Restaurant.id, Restaurant.name_norm).where(
            Restaurant.city_id == restaurant.city_id,
            Restaurant.status == "active",
            Restaurant.id != restaurant.id,
        )
    ).all()
    return sum(1 for _, norm in candidates if brand_stem(norm) == stem)


def _branch_mention(shop_name_raw: str, name_norm: str) -> bool:
    """提及名是否为指向其他分店的分店式命名（渠道 B）。"""
    if normalize_shop_name(shop_name_raw) == name_norm:
        return False  # 归一后与实体同名：按名精确对齐，无分店歧义
    return any(token in (shop_name_raw or "") for token in _BRANCH_TOKENS)


def build_brand_evidence_notice(session: Session, restaurant: Restaurant) -> dict | None:
    """同品牌口碑不确定性提示；不满足触发条件返回 None。"""
    if restaurant.city_id is None:
        return None

    rows = session.execute(
        select(MentionRow.shop_name_raw, MentionRow.address_text).where(
            MentionRow.restaurant_id == restaurant.id
        )
    ).all()
    if not rows:
        return None

    unlocated = [(name, addr) for name, addr in rows if not (addr or "").strip()]
    if not unlocated:
        return None  # 全部口碑带地址：对齐时可按地址消歧，不提示

    siblings = _sibling_count(session, restaurant)
    branch_mentions = sum(
        1 for name, _ in unlocated if _branch_mention(name, restaurant.name_norm)
    )
    if siblings == 0 and branch_mentions == 0:
        return None

    return {
        "sibling_count": siblings,
        "unlocated_mentions": len(unlocated),
        "branch_mentions": branch_mentions,
        "mention_count": len(rows),
    }
