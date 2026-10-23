"""方向四：老店档案 + 存活监测（设计方案 2026-10-14，第 1/2 批）。

- 老店档案：first/last_mentioned_at 从 mention→raw_content 聚合回填；
  仅 published_at 支撑时才视为可靠证据（first_evidence_confident=True），
  退化到 crawled_at 的记录既不展示年限也不参与打分。
- 存活监测：POI 刷新命中即刷 last_seen_at / 清零 miss_count（suspect 回活）；
  本城未命中的 POI 底座店铺 miss_count += 1。liveness 状态机的推进
  （suspect/closed 阈值判定）属第 3 批，本模块暂不迁移状态。
- 打卡补证（2026-10-23 数据产品路线）：审核通过的 UGC 打卡是第一手在店证据，
  强于 POI 关键词命中——POI 命中只回活 suspect（mark_seen），打卡可回活 closed；
  relocated 为人工处置态，两者都不越过。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..db.models import City, Mention, RawContent, Restaurant, UgcPost
from .scoring import to_utc

# suspect 及以上的店铺再次命中 POI 时的回活规则：suspect 直接回 active；
# closed 是终端态，命中只更新 last_seen_at/miss_count，reopen 由第 3 批状态机处理。
_REVIVABLE = {"suspect"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def backfill_first_last_mentioned(session: Session) -> dict:
    """从 mention→raw_content 聚合回填 first/last_mentioned_at（幂等，全量覆盖）。

    口径（设计方案 2.2）：
    - first_mentioned_at = MIN(published_at)；该店所有提及都缺发布时间时
      退化用 MIN(crawled_at)，此时 first_evidence_confident=False。
    - last_mentioned_at = MAX(coalesce(published_at, crawled_at))。
    """
    rows = session.execute(
        select(
            Mention.restaurant_id,
            func.min(RawContent.published_at),
            func.min(RawContent.crawled_at),
            func.max(func.coalesce(RawContent.published_at, RawContent.crawled_at)),
        )
        .join(RawContent, RawContent.id == Mention.raw_content_id)
        .where(Mention.restaurant_id.is_not(None))
        .group_by(Mention.restaurant_id)
    ).all()

    stats = {
        "restaurants_with_mention": 0,
        "confident": 0,
        "crawl_only": 0,
        "missing_restaurant": 0,
    }
    for rid, min_pub, min_crawl, last in rows:
        restaurant = session.get(Restaurant, rid)
        if restaurant is None:  # mention 挂靠后被物理删除的残留
            stats["missing_restaurant"] += 1
            continue
        if min_pub is not None:
            first, confident = min_pub, True
        else:
            first, confident = min_crawl, False
        restaurant.first_mentioned_at = to_utc(first) if first else None
        restaurant.first_evidence_confident = confident
        restaurant.last_mentioned_at = to_utc(last) if last else None
        stats["restaurants_with_mention"] += 1
        stats["confident" if confident else "crawl_only"] += 1
    session.flush()
    return stats


def backfill_last_checkin(session: Session) -> dict:
    """从历史 approved 打卡回填 last_checkin_at（幂等，全量覆盖）。

    仅取 approved：pending/rejected 不构成存活证据（先审后显是信任边界）。
    """
    rows = session.execute(
        select(UgcPost.restaurant_id, func.max(UgcPost.created_at))
        .where(UgcPost.status == "approved")
        .group_by(UgcPost.restaurant_id)
    ).all()
    stats = {"restaurants_with_checkin": 0, "missing_restaurant": 0}
    for rid, last in rows:
        restaurant = session.get(Restaurant, rid)
        if restaurant is None:  # 挂靠后被物理删除的残留
            stats["missing_restaurant"] += 1
            continue
        restaurant.last_checkin_at = to_utc(last)
        stats["restaurants_with_checkin"] += 1
    session.flush()
    return stats


def mark_seen(restaurant: Restaurant, seen_at: datetime) -> None:
    """POI 刷新命中：刷 last_seen_at、清零 miss_count；suspect 回活。"""
    restaurant.last_seen_at = seen_at
    restaurant.miss_count = 0
    if restaurant.liveness in _REVIVABLE:
        restaurant.liveness = "active"


# 打卡补证可回活的状态：approved 打卡是人工审核过的第一手在店证据，
# 强于 POI 关键词召回（可能撞名），故 closed 也可回活；relocated 是人工处置态。
_REVIVABLE_BY_EVIDENCE = {"active", "suspect", "closed"}


def apply_user_evidence(restaurant: Restaurant, at: datetime) -> None:
    """审核通过的打卡落地为存活证据（ugc_service.review_ugc 在通过时调用）。

    - last_checkin_at 记证据时间（取打卡创建时刻，即用户到店时间，非审核时刻）；
    - miss_count 清零：此后 POI 轮次从 0 重新累计，证据随轮次自然衰减，
      无需额外的保鲜期参数；
    - suspect/closed 回活为 active（closed 同时清 closed_at，避免陈旧闭店时间残留）；
      relocated 不动。
    """
    restaurant.last_checkin_at = to_utc(at)
    restaurant.miss_count = 0
    if restaurant.liveness in _REVIVABLE_BY_EVIDENCE:
        if restaurant.liveness == "closed":
            restaurant.closed_at = None
        restaurant.liveness = "active"


def mark_city_misses(session: Session, city_name: str, *, before: datetime) -> int:
    """对本城本轮 POI 刷新未命中的底座店铺 miss_count += 1（第 2 批）。

    判定口径：last_seen_at < before——命中店在 upsert 时已把 last_seen_at 刷到
    before 当刻，天然被排除。

    **仅统计曾经命中过的店**（last_seen_at 非空）：固定关键词检索存在天然盲区
    （2026-10-15 实测全局约 35% 的底座店从未被召回，老城达 50%），盲区店没有
    「失联」概念——若对它们累计 miss，3 天后就会被整批误杀降权。只有先见过、
    后丢失才是真失联信号。

    仅处理有 POI 底座（poi_id 非空）的店；closed 为终端态不再累计。
    """
    city = session.scalar(select(City).where(City.name == city_name))
    if city is None:
        return 0
    rows = session.scalars(
        select(Restaurant).where(
            Restaurant.city_id == city.id,
            Restaurant.poi_id.is_not(None),
            Restaurant.liveness != "closed",
            Restaurant.last_seen_at.is_not(None),
            Restaurant.last_seen_at < before,
        )
    ).all()
    for restaurant in rows:
        restaurant.miss_count = (restaurant.miss_count or 0) + 1
    session.flush()
    return len(rows)


def evaluate_liveness(
    session: Session,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> dict:
    """状态机推进（方向四第 3 批）：按连续未命中次数迁移 liveness。幂等。

    迁移规则（阈值走 Settings，保守默认 5/10，可用环境变量调宽）：
        active/suspect --miss_count ≥ liveness_suspect_misses--> suspect
        active/suspect --miss_count ≥ liveness_closed_misses --> closed（记 closed_at）

    - relocated 为人工处置态，不参与自动迁移；
    - suspect → active 的「回活」由 mark_seen 在 POI 命中时承担；
    - closed 是自动链路终端态：重新命中仅刷 last_seen_at/miss_count，
      人工确认 reopen 由后台操作承接（后续批次）。
    """
    settings = settings or get_settings()
    now = to_utc(now) or datetime.now(timezone.utc)
    suspect_misses = max(1, settings.liveness_suspect_misses)
    closed_misses = max(suspect_misses, settings.liveness_closed_misses)

    to_suspect = session.execute(
        update(Restaurant)
        .where(
            Restaurant.liveness == "active",
            Restaurant.miss_count >= suspect_misses,
            Restaurant.miss_count < closed_misses,
        )
        .values(liveness="suspect")
        .execution_options(synchronize_session=False)
    ).rowcount

    to_closed = session.execute(
        update(Restaurant)
        .where(
            Restaurant.liveness.in_(["active", "suspect"]),
            Restaurant.miss_count >= closed_misses,
        )
        .values(liveness="closed", closed_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount

    # 批量 UPDATE 不经身份映射：过期会话内对象，保证调用方随后读到新状态
    session.expire_all()
    session.flush()
    return {"to_suspect": to_suspect, "to_closed": to_closed}
