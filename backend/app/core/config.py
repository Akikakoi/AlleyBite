from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_urls(raw: str) -> list[str]:
    """按逗号/分号/换行拆分 URL 列表，去空白与空项。"""
    parts = raw.replace("\r", "\n")
    for ch in ",;":
        parts = parts.replace(ch, "\n")
    return [item.strip() for item in parts.split("\n") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AlleyBite API"
    debug: bool = True
    # CORS 白名单（文档 10.2）；逗号/分号/换行分隔，仅放行前端域名。
    # 留空时：debug 全放行（本地联调），否则不放行任何跨域来源（同源部署无需 CORS）。
    cors_origins: str = ""

    # LLM
    llm_provider: str = "dashscope"
    llm_model: str = "qwen-plus"
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_mock: bool = False
    llm_timeout: float = 60.0
    llm_temperature: float = 0.1

    # 抽取
    extract_confidence_min: float = 0.6
    extract_json_retry: int = 3

    # 清洗与切块（文档 5.2）
    extract_max_tokens: int = 512
    chunk_overlap_tokens: int = 0
    min_chinese_ratio: float = 0.5

    # 打分模型（文档 6）
    score_time_half_life_days: int = 180      # 时效衰减半衰期（6.2）
    score_mention_target: int = 8             # 真实提及量饱和目标（独立来源数）
    score_confidence_target: int = 3          # 证据充分度饱和目标（mention 数）
    score_span_target_days: int = 365         # 沉淀时长饱和目标
    score_uniqueness_target: int = 2          # 独特性语境饱和目标
    score_paradox_target: int = 1             # 矛盾型好评饱和目标
    score_price_ceiling: float = 150.0        # 价格亲民参考上限（元）；后续由城市/菜系分位数替代
    score_influencer_target: int = 3          # 网红标签惩罚饱和目标
    score_hygiene_target: int = 3             # 卫生风险惩罚饱和目标
    # 硬规则（6.3）
    score_ad_exclude_ratio: float = 0.4       # 广告话术占比 ≥ 该值 → 剔除
    score_burst_window_days: int = 7          # 刷评窗口（天）
    score_burst_min_mentions: int = 20        # 同平台同质好评 ≥ 该条数 → 降权 50%
    score_chain_categories: str = "购物中心内,连锁快餐"  # 地图分类剔除名单（逗号分隔）
    chain_brand_blacklist: str = ""           # 连锁品牌黑名单（逗号分隔）

    # 实体对齐（文档 5.5）
    align_match_threshold: float = 0.85      # 综合相似度 ≥ 该值 → 归并为同一 restaurant
    align_review_threshold: float = 0.65     # 介于 [review, match) → 进人工审核队列
    align_name_weight: float = 0.7           # 综合分：名称相似度权重
    align_address_weight: float = 0.3        # 综合分：地址 token 重合度权重

    # 数据库（本地 SQLite，生产 PostgreSQL 15）
    database_url: str = "sqlite:///./alleybite.db"
    db_echo: bool = False

    # 榜单缓存（文档 6.4 第 6 步）；留空则降级为直读库内快照
    redis_url: str = ""
    rank_cache_ttl: int = 3600

    # 采集（文档第 4 章）；字段名自动映射 CRAWL_* 环境变量
    crawl_qps_per_domain: float = 1.0        # 单域名 QPS 上限
    crawl_jitter_min: float = 0.5            # 请求随机抖动下限（秒）
    crawl_jitter_max: float = 2.0            # 请求随机抖动上限（秒）
    crawl_night_pause: bool = True           # 夜间不采集
    crawl_night_start: int = 23              # 夜间起始小时（含）
    crawl_night_end: int = 7                 # 夜间结束小时（不含）
    crawl_retry_max: int = 3                 # 失败最大重试次数（4xx 不重试，429 除外）
    crawl_timeout: float = 20.0              # 单请求超时（秒）
    crawl_contact: str = ""                  # 联系方式，用于拼真实 User-Agent
    crawl_user_agent: str = ""               # 显式 UA；留空则由 crawl_contact 派生
    crawl_rss_urls: str = ""                 # 公开 RSS/Atom feed，逗号或换行分隔
    crawl_html_list_urls: str = ""           # 公开列表页，逗号或换行分隔
    crawl_max_items_per_source: int = 200    # 单源单次采集条目上限
    crawl_breaker_fail_threshold: int = 5    # 连续 403/429 达该值 → 熔断暂停该源
    crawl_breaker_cooldown_minutes: int = 360  # 熔断冷却时长（分钟）
    snapshot_dir: str = "./snapshots"        # 原始页面快照目录（替代文档中的 OSS）
    crawl_snapshot_ttl_days: int = 90        # 快照保留天数

    # 高德地图开放 API（文档 4.1 的 POI 实体基准）；留空则跳过高德源
    amap_api_key: str = ""
    amap_base_url: str = "https://restapi.amap.com/v3/place/text"
    amap_page_size: int = 20
    amap_max_pages: int = 3
    # 美食检索关键词（逗号分隔），逐个检索后按 poi_id 去重，用于扩量 POI 实体基准
    amap_keywords: str = "美食,川菜,火锅,面馆,烧烤,小吃,家常菜,串串"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS 白名单（文档 10.2）；debug 且未显式配置时全放行。"""
        origins = _split_urls(self.cors_origins)
        if origins:
            return origins
        return ["*"] if self.debug else []

    @property
    def use_mock(self) -> bool:
        """无 API Key 或显式开启 mock 时，走离线 mock，保证 demo/单测可跑。"""
        return self.llm_mock or not self.llm_api_key

    @property
    def chain_brand_list(self) -> list[str]:
        return [s.strip() for s in self.chain_brand_blacklist.split(",") if s.strip()]

    @property
    def chain_category_list(self) -> list[str]:
        return [s.strip() for s in self.score_chain_categories.split(",") if s.strip()]

    @property
    def has_amap(self) -> bool:
        return bool(self.amap_api_key)

    @property
    def rss_url_list(self) -> list[str]:
        return _split_urls(self.crawl_rss_urls)

    @property
    def html_list_url_list(self) -> list[str]:
        return _split_urls(self.crawl_html_list_urls)

    @property
    def build_user_agent(self) -> str:
        """真实可联系 User-Agent（文档 4.3）；显式配置优先。"""
        if self.crawl_user_agent:
            return self.crawl_user_agent
        contact = self.crawl_contact or "contact-not-configured"
        return f"AlleyBiteBot/0.1 (+{contact})"

    def is_night(self, hour: int) -> bool:
        """夜间时段判定（支持跨零点，如 23→7）。"""
        if not self.crawl_night_pause:
            return False
        if self.crawl_night_start <= self.crawl_night_end:
            return self.crawl_night_start <= hour < self.crawl_night_end
        return hour >= self.crawl_night_start or hour < self.crawl_night_end


@lru_cache
def get_settings() -> Settings:
    return Settings()