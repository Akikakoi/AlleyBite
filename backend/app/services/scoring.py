"""「苍蝇馆子」识别与打分模型（文档第 6 章）。

本模块是**纯计算内核**：输入店铺聚合信号 ``ShopSignals``，输出 ``ShopScore``，
不直接访问数据库（ORM 聚合见 scoring_service.py），便于单测与后续替换数据源。

公式（6.2）：
    score = 5 × ( Σ wᵢ·fᵢ − Σ penaltyⱼ ) × time_decay × confidence_factor

说明：
- fᵢ / penaltyⱼ 均归一化到 [0,1]，权重见 FEATURE_WEIGHTS。
- 文档未给出各惩罚项的权重，故 penaltyⱼ 按文档字面直接求和（各自已是 [0,1]）。
- 信号缺失时用中性值兜底（如价格未知取 0.5），待实体对齐 / 地图 POI 落地后填充。
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from ..core.config import Settings, get_settings
from .normalize import normalize_shop_name

# --- 正面特征权重（6.1，合计 1.00） -----------------------------------------
FEATURE_WEIGHTS: dict[str, float] = {
    "real_mention_volume": 0.20,   # 真实提及量（去重后独立来源数）
    "sentiment_strength": 0.18,    # 口碑强度
    "locality": 0.15,              # 本地化程度
    "price_friendliness": 0.12,    # 价格亲民
    "tenure": 0.10,                # 沉淀时长
    "non_chain": 0.10,             # 非连锁
    "uniqueness": 0.08,            # 独特性
    "paradox_good": 0.07,          # 环境一般但味道好
}

# 情感极性 → 强度（用于"平均情感强度"，可调）
SENTIMENT_INTENSITY: dict[str, float] = {
    "positive": 1.0,
    "mixed": 0.5,
    "neutral": 0.2,
    "negative": 0.0,
}

# --- 关键词库 ---------------------------------------------------------------
INFLUENCER_KEYWORDS = ("网红", "打卡", "ins风", "INS风", "必吃榜", "拔草", "爆款", "排队王")
UNIQUENESS_KEYWORDS = (
    "只有本地人", "本地人才知道", "本地人推荐", "本地人常去", "带朋友",
    "老饕", "藏在小巷", "巷子里", "苍蝇馆子", "开了很多年",
)
PARADOX_ENV_KEYWORDS = (
    "环境一般", "环境很一般", "环境差", "环境简陋", "环境嘈杂", "环境不怎么样",
    "没装修", "苍蝇馆子", "苍蝇馆", "店面不起眼", "门头不起眼",
)
HYGIENE_KEYWORDS = ("卫生", "不干净", "脏", "苍蝇乱飞")
CHAIN_KEYWORDS = ("连锁", "分店", "加盟", "旗舰店")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _saturate(value: float, target: float) -> float:
    """把计数类信号饱和到 [0,1]：value/target，封顶 1。"""
    if target <= 0:
        return 1.0 if value > 0 else 0.0
    return _clamp01(value / target)


def to_utc(dt: datetime | None) -> datetime | None:
    """统一为带时区的 UTC；SQLite 读回的 datetime 通常是 naive。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --- 输入信号 ---------------------------------------------------------------

@dataclass(frozen=True)
class MentionFact:
    """一条 mention 的扁平化事实，解耦 ORM，便于构造与测试。"""

    content_id: int
    platform: str
    source_url: str | None
    city_hint: str | None
    author_city: str | None
    at: datetime | None            # 事件时间：published_at → crawled_at → created_at
    low_trust: bool
    shop_name_raw: str
    sentiment: str
    is_recommendation: bool
    praise_keywords: tuple[str, ...] = ()
    complaints: tuple[str, ...] = ()
    evidence_span: str | None = None

    @property
    def text_blob(self) -> str:
        return " ".join((*self.praise_keywords, *self.complaints, self.evidence_span or ""))


@dataclass
class ShopSignals:
    """一家店聚合后的原始信号，供特征/惩罚计算。"""

    shop_key: str
    display_name: str
    city_hint: str | None = None
    mention_count: int = 0
    independent_source_count: int = 0
    positive_count: int = 0
    intensity_sum: float = 0.0
    local_known_count: int = 0
    local_count: int = 0
    avg_price: float | None = None
    earliest_at: datetime | None = None
    latest_at: datetime | None = None
    chain_flag: bool = False
    uniqueness_hits: int = 0
    paradox_good_count: int = 0
    ad_mention_count: int = 0
    influencer_hits: int = 0
    hygiene_count: int = 0
    burst: bool = False
    map_category: str | None = None
    address: str | None = None
    facts: list[MentionFact] = field(default_factory=list)


# --- 输出模型 ---------------------------------------------------------------

class FeatureScore(BaseModel):
    key: str
    value: float                 # 归一化 [0,1]
    weight: float
    contribution: float          # value × weight


class PenaltyScore(BaseModel):
    key: str
    value: float                 # 归一化 [0,1]


class ShopScore(BaseModel):
    shop_key: str
    shop_name: str
    city_hint: str | None = None
    restaurant_id: int | None = None   # 实体对齐后回填，供榜单快照引用
    mention_count: int = 0
    score: float = 0.0               # 最终分（0–5，5 分制）
    base_score: float = 0.0          # Σ wᵢfᵢ − Σ penaltyⱼ
    time_decay: float = 1.0
    confidence_factor: float = 0.0
    features: list[FeatureScore] = Field(default_factory=list)
    penalties: list[PenaltyScore] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)   # 「为什么上榜」人话理由
    excluded: bool = False
    exclude_reason: str | None = None
    penalty_multiplier: float = 1.0
    rank: int | None = None


class HardRuleResult(BaseModel):
    excluded: bool = False
    reason: str | None = None
    multiplier: float = 1.0


# --- 硬规则（6.3） ----------------------------------------------------------

def is_mall_address(address: str | None, settings: Settings) -> bool:
    """POI 地址是否指向商场/写字楼内店铺（1.5 定义中"非商场店"的反例）。"""
    if not address:
        return False
    return any(kw in address for kw in settings.mall_address_list)


def apply_hard_rules(signals: ShopSignals, settings: Settings) -> HardRuleResult:
    ad_ratio = (
        signals.ad_mention_count / signals.mention_count if signals.mention_count else 0.0
    )
    if ad_ratio >= settings.score_ad_exclude_ratio:
        return HardRuleResult(excluded=True, reason="广告/软文话术占比过高")

    for brand in settings.chain_brand_list:
        if brand and brand in signals.display_name:
            return HardRuleResult(excluded=True, reason=f"命中连锁品牌黑名单：{brand}")

    if is_mall_address(signals.address, settings):
        return HardRuleResult(excluded=True, reason="POI 地址为商场/写字楼内店铺")

    if signals.map_category and signals.map_category in settings.chain_category_list:
        return HardRuleResult(excluded=True, reason=f"地图分类为{signals.map_category}")

    multiplier = 0.5 if signals.burst else 1.0
    return HardRuleResult(multiplier=multiplier)


def _has_burst(facts: list[MentionFact], settings: Settings) -> bool:
    """硬规则 2：单一平台 ≤N 天内 ≥M 条同质化好评 → 判刷评。"""
    by_platform: dict[str, list[datetime]] = {}
    for fact in facts:
        if fact.sentiment != "positive" or fact.at is None:
            continue
        by_platform.setdefault(fact.platform, []).append(fact.at)

    window = settings.score_burst_window_days
    need = settings.score_burst_min_mentions
    for times in by_platform.values():
        times.sort()
        left = 0
        for right, t in enumerate(times):
            while (t - times[left]).days > window:
                left += 1
            if right - left + 1 >= need:
                return True
    return False


# --- 特征 / 惩罚 / 因子 -----------------------------------------------------

def compute_features(
    signals: ShopSignals, settings: Settings, now: datetime
) -> list[FeatureScore]:
    n = signals.mention_count
    positive_ratio = signals.positive_count / n if n else 0.0
    avg_intensity = signals.intensity_sum / n if n else 0.0

    if signals.local_known_count:
        locality = signals.local_count / signals.local_known_count
    else:
        locality = 0.5  # 作者城市未知 → 中性兜底

    if signals.avg_price is None:
        price = 0.5
    else:
        price = _clamp01(1 - signals.avg_price / settings.score_price_ceiling)

    span_days = (now - to_utc(signals.earliest_at)).days if signals.earliest_at else 0

    values = {
        "real_mention_volume": _saturate(
            signals.independent_source_count, settings.score_mention_target
        ),
        "sentiment_strength": _clamp01(positive_ratio * avg_intensity),
        "locality": _clamp01(locality),
        "price_friendliness": price,
        "tenure": _saturate(span_days, settings.score_span_target_days),
        "non_chain": 0.0 if signals.chain_flag else 1.0,
        "uniqueness": _saturate(signals.uniqueness_hits, settings.score_uniqueness_target),
        "paradox_good": _saturate(signals.paradox_good_count, settings.score_paradox_target),
    }
    return [
        FeatureScore(
            key=key,
            value=round(values[key], 4),
            weight=FEATURE_WEIGHTS[key],
            contribution=round(values[key] * FEATURE_WEIGHTS[key], 4),
        )
        for key in FEATURE_WEIGHTS
    ]


def compute_penalties(signals: ShopSignals, settings: Settings) -> list[PenaltyScore]:
    n = signals.mention_count
    ad_ratio = signals.ad_mention_count / n if n else 0.0
    marketing = _clamp01(ad_ratio / settings.score_ad_exclude_ratio) if ad_ratio else 0.0

    if signals.avg_price is None:
        price_inflation = 0.0
    else:
        price_inflation = _clamp01(
            (signals.avg_price - settings.score_price_ceiling) / settings.score_price_ceiling
        )

    values = {
        "marketing_trace": marketing,
        "influencer_label": _saturate(signals.influencer_hits, settings.score_influencer_target),
        "chain_attribute": 1.0 if signals.chain_flag else 0.0,
        "price_inflation": price_inflation,
        "hygiene_risk": _saturate(signals.hygiene_count, settings.score_hygiene_target),
    }
    return [PenaltyScore(key=key, value=round(values[key], 4)) for key in values]


def compute_time_decay(latest_at: datetime | None, now: datetime, half_life_days: int) -> float:
    """time_decay = 0.5 ^ (Δdays / half_life)；无时间信息时不衰减。"""
    if latest_at is None or half_life_days <= 0:
        return 1.0
    delta_days = max(0, (now - to_utc(latest_at)).days)
    return round(0.5 ** (delta_days / half_life_days), 4)


def compute_confidence_factor(mention_count: int, target: int) -> float:
    """证据充分度：mention 数越少越接近 0，防止"一条评论就上榜"。"""
    return round(_saturate(mention_count, target), 4)


def build_rank_reasons(signals: ShopSignals, *, now: datetime) -> list[str]:
    """把聚合信号翻译成「为什么上榜」的人话理由（详情页证据链，方向一）。

    只描述已观测到的信号，不做主观修饰；降权/风险类提示照实说明。
    """
    reasons: list[str] = []

    if signals.independent_source_count >= 2:
        reasons.append(f"{signals.independent_source_count} 家独立来源分别提及")
    elif signals.mention_count > 0:
        reasons.append(f"{signals.mention_count} 条公开提及记录")

    if signals.positive_count > 0:
        reasons.append(f"正面口碑提及 {signals.positive_count} 次")

    if (
        signals.local_known_count > 0
        and signals.local_count / signals.local_known_count >= 0.5
    ):
        reasons.append(
            f"本地人认可（{signals.local_count}/{signals.local_known_count} 条提及来自本地作者）"
        )

    if signals.avg_price is not None and signals.avg_price > 0:
        reasons.append(f"人均约 ¥{int(signals.avg_price)}")

    if signals.earliest_at is not None:
        days = max(0, (now - to_utc(signals.earliest_at)).days)
        if days >= 365:
            reasons.append(f"可追溯报道约 {round(days / 365)} 年")
        elif days >= 90:
            reasons.append(f"可追溯报道约 {round(days / 30)} 个月")

    if signals.uniqueness_hits > 0:
        reasons.append("有「本地人私藏 / 巷子小店」类独特语境")

    if signals.paradox_good_count > 0:
        reasons.append("存在「环境一般但味道惊艳」型好评")

    # 风险/降权类提示照实披露
    if signals.burst:
        reasons.append("近 7 天疑似集中刷评，已降权")
    if signals.influencer_hits > 0:
        reasons.append("含网红探店话术，已降权")
    if signals.hygiene_count > 0:
        reasons.append("有卫生相关负面提及，请注意")

    return reasons[:8]


def score_shop(
    signals: ShopSignals,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ShopScore:
    settings = settings or get_settings()
    now = to_utc(now) or datetime.now(timezone.utc)

    features = compute_features(signals, settings, now)
    penalties = compute_penalties(signals, settings)
    base_score = round(
        sum(f.contribution for f in features) - sum(p.value for p in penalties), 4
    )

    rules = apply_hard_rules(signals, settings)
    decay = compute_time_decay(signals.latest_at, now, settings.score_time_half_life_days)
    confidence = compute_confidence_factor(
        signals.mention_count, settings.score_confidence_target
    )

    if rules.excluded:
        score = 0.0
    else:
        # 未平滑质量分（5 分制）：置信处理改由聚合层贝叶斯平均承担（见
        # apply_bayesian_smooth），单店分数不再被 mention 数线性压低
        score = 5 * max(0.0, base_score) * decay * rules.multiplier
        score = round(_clamp01(score / 5) * 5, 1)

    return ShopScore(
        shop_key=signals.shop_key,
        shop_name=signals.display_name,
        city_hint=signals.city_hint,
        mention_count=signals.mention_count,
        score=score,
        base_score=base_score,
        time_decay=decay,
        confidence_factor=confidence,
        features=features,
        penalties=penalties,
        reasons=[] if rules.excluded else build_rank_reasons(signals, now=now),
        excluded=rules.excluded,
        exclude_reason=rules.reason,
        penalty_multiplier=rules.multiplier,
    )


# --- 聚合 -------------------------------------------------------------------

def _count_hits(blob: str, keywords: tuple[str, ...]) -> int:
    return sum(blob.count(kw) for kw in keywords)


def aggregate_shop(
    shop_key: str,
    facts: list[MentionFact],
    settings: Settings,
    *,
    avg_price: float | None = None,
    map_category: str | None = None,
    display_name: str | None = None,
    city_hint: str | None = None,
    address: str | None = None,
) -> ShopSignals:
    """把同一店铺的 mention 事实聚合为信号。

    display_name / city_hint 传入时优先使用（实体对齐后取 restaurant 的规范名与城市）。
    """
    names = Counter(f.shop_name_raw for f in facts)
    resolved_name = display_name or (names.most_common(1)[0][0] if names else shop_key)

    cities = Counter(f.city_hint for f in facts if f.city_hint)
    resolved_city = city_hint or (cities.most_common(1)[0][0] if cities else None)

    independent = {
        f.source_url or f"{f.platform}#{f.content_id}" for f in facts
    }
    times = [f.at for f in facts if f.at is not None]

    chain_flag = any(kw in resolved_name for kw in CHAIN_KEYWORDS)
    uniqueness_hits = 0
    paradox_good_count = 0
    influencer_hits = 0
    hygiene_count = 0
    local_count = 0
    local_known = 0
    ad_mention_count = 0

    for fact in facts:
        blob = fact.text_blob
        if not chain_flag and any(kw in blob for kw in CHAIN_KEYWORDS):
            chain_flag = True
        uniqueness_hits += _count_hits(blob, UNIQUENESS_KEYWORDS)
        influencer_hits += _count_hits(blob, INFLUENCER_KEYWORDS)

        has_env_issue = any(kw in blob for kw in PARADOX_ENV_KEYWORDS)
        if has_env_issue and (fact.is_recommendation or fact.sentiment in ("positive", "mixed")):
            paradox_good_count += 1
        if any(kw in blob for kw in HYGIENE_KEYWORDS):
            hygiene_count += 1

        if fact.author_city:
            local_known += 1
            if fact.city_hint and fact.author_city == fact.city_hint:
                local_count += 1

        if fact.low_trust:
            ad_mention_count += 1

    return ShopSignals(
        shop_key=shop_key,
        display_name=resolved_name,
        city_hint=resolved_city,
        mention_count=len(facts),
        independent_source_count=len(independent),
        positive_count=sum(1 for f in facts if f.sentiment == "positive"),
        intensity_sum=sum(SENTIMENT_INTENSITY.get(f.sentiment, 0.0) for f in facts),
        local_known_count=local_known,
        local_count=local_count,
        avg_price=avg_price,
        earliest_at=min(times) if times else None,
        latest_at=max(times) if times else None,
        chain_flag=chain_flag,
        uniqueness_hits=uniqueness_hits,
        paradox_good_count=paradox_good_count,
        ad_mention_count=ad_mention_count,
        influencer_hits=influencer_hits,
        hygiene_count=hygiene_count,
        burst=_has_burst(facts, settings),
        map_category=map_category,
        address=address,
        facts=list(facts),
    )

# --- 贝叶斯平均（替代线性置信因子，文档 6.2 演进） ---------------------------

def apply_bayesian_smooth(
    scores: list[ShopScore], prior_strength: float
) -> list[ShopScore]:
    """对同一批（同城）候选店做贝叶斯平均，就地更新 score 字段并返回。

    final = (C × prior + n × quality) / (C + n)
    - prior：本批未剔除店铺质量分的均值（先验：'该城市普通苍蝇馆子水平'）
    - C：先验强度（score_bayes_prior），等效于给每家店垫 C 条'城市平均水平'证据
    - n：mention_count；证据越少越收敛到先验，越多越尊重自身质量分

    单店或全被剔除时无可估计先验，保持原分不变。
    """
    valid = [s for s in scores if not s.excluded and s.mention_count > 0]
    if prior_strength <= 0 or len(valid) < 2:
        return scores
    prior = sum(s.score for s in valid) / len(valid)
    for s in valid:
        n = s.mention_count
        smoothed = (prior_strength * prior + n * s.score) / (prior_strength + n)
        s.score = round(max(0.0, min(5.0, smoothed)), 1)
    return scores
