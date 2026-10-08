import json
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
    # LLM 请求附加参数（JSON 对象字符串，默认空）；用于按厂商传开关，
    # 如关闭推理模型思考链（可显著提速）：{"chat_template_kwargs": {"enable_thinking": false}}
    llm_extra_body: str = ""

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
    # 连锁品牌黑名单（逗号分隔）：命中店铺名即剔除。默认收录全国性连锁品牌
    chain_brand_blacklist: str = (
        "点都德,广州酒家,太平馆,遇见小面,摩打食堂,文通冰室,蜀大侠,马旺子,大碗先生,"
        "吃饭皇帝大,十八梯邓凳面,海底捞,西贝,肯德基,麦当劳,星巴克,瑞幸,喜茶,蜜雪冰城"
    )
    # 商场/写字楼内店铺：POI 地址命中以下关键词即剔除（1.5 定义明确排除"商场店"）
    # 口径说明：只收录**具名商业体**，不泛化匹配"中心"——泛化会误杀
    #   "体育东路39号(体育中心地铁站D3口步行450米)"（地铁站名）
    #   "麓景路151号九阳阳光服务中心隔壁"（隔邻单位）
    #   "凤城七路旭辉中心底商"（底商仍属街边）等合法街边店。
    score_mall_address_keywords: str = (
        "购物中心,广场,大厦,商场,百货,太古里,SKP,悠方,合生汇,熙地港,花园城,大魔方,"
        "金融中心,城壹汇,五月花,星悦荟,捷登都,动漫星城,鎏嘉码头,"
        "环球中心,金融国际中心,华商中心,国金中心,华茂中心,时代地产中心,"
        "铂仁国际中心,荟聚中心,达美商业中心,泰达时代中心,唐延中心城"
    )

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
    crawl_html_list_urls: str = ""           # 公开列表页（列表页→跟进正文），逗号或换行分隔
    # 「单页即内容」静态页：页面正文整体作为一条内容，不跟进外链。
    # 每项格式 `城市=URL`（无前缀时用 --city）；逗号或换行分隔。适用于官方榜单/名单页。
    crawl_html_page_urls: str = ""
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

    # 纠错/举报（文档 9.3 / 14 章合规验收）：免登录提交，按 IP 哈希限流
    feedback_rate_limit_max: int = 5            # 单 IP 窗口内最多提交条数
    feedback_rate_limit_window_minutes: int = 60
    feedback_content_max_len: int = 500         # 纠错正文长度上限
    feedback_ip_salt: str = "alleybite"         # IP 哈希加盐，避免明文/裸哈希留存

    # 分享预览（文档 9.4 / 14 章验收）：爬虫 UA 分流后由此渲染动态 og 元信息
    share_base_url: str = ""                    # 对外访问基址；留空则由请求头推导
    share_og_image: str = "/og-default.jpg"     # 默认分享图；绝对 URL 原样输出
    share_site_name: str = "苍蝇馆子美食发现器"

    # 管理后台（文档 9.5）：独立登录 + 独立令牌签名，与 C 端鉴权隔离
    admin_username: str = "admin"
    admin_password: str = ""                    # 首次启动用于初始化超管；留空则不初始化
    admin_token_secret: str = ""                # 令牌签名密钥；留空时回退到 feedback_ip_salt
    admin_token_ttl_minutes: int = 120          # 令牌有效期（分钟）
    admin_password_iterations: int = 200_000    # PBKDF2 迭代次数

    # C 端账号（文档 10.2 / 12 章 V2.0）：用户名口令注册登录换 JWT 式令牌
    user_token_secret: str = ""                 # 令牌签名密钥；留空回退 feedback_ip_salt（受众不同，不会与后台令牌串签）
    user_token_ttl_minutes: int = 10_080        # 令牌有效期（分钟），默认 7 天
    user_password_iterations: int = 200_000     # PBKDF2 迭代次数

    @property
    def admin_token_key(self) -> str:
        """管理后台令牌签名密钥：显式配置优先，否则回退到既有加盐串。"""
        return self.admin_token_secret or self.feedback_ip_salt

    @property
    def user_token_key(self) -> str:
        """C 端令牌签名密钥：显式配置优先，否则回退到既有加盐串。"""
        return self.user_token_secret or self.feedback_ip_salt

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
    def llm_extra_body_dict(self) -> dict:
        """解析 LLM_EXTRA_BODY 为 dict，透传给 OpenAI 兼容客户端的 extra_body。

        留空时返回空 dict（不影响请求）；非法 JSON 或非对象时抛错，避免静默失效。
        """
        raw = self.llm_extra_body.strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM_EXTRA_BODY 不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("LLM_EXTRA_BODY 必须是 JSON 对象")
        return data

    @property
    def chain_brand_list(self) -> list[str]:
        return [s.strip() for s in self.chain_brand_blacklist.split(",") if s.strip()]

    @property
    def chain_category_list(self) -> list[str]:
        return [s.strip() for s in self.score_chain_categories.split(",") if s.strip()]

    @property
    def mall_address_list(self) -> list[str]:
        return [s.strip() for s in self.score_mall_address_keywords.split(",") if s.strip()]

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
    def html_page_url_list(self) -> list[str]:
        """「单页即内容」配置项原始列表；`城市=URL` 前缀解析见 html_list.parse_page_specs。"""
        return _split_urls(self.crawl_html_page_urls)

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


# 上线前必须替换的弱默认密钥（文档 11 章 安全与合规）：生产环境命中即拒绝启动。
WEAK_SECRET_VALUES = {"", "alleybite", "change-me", "changeme", "secret", "test-secret"}
MIN_SECRET_LEN = 16


class InsecureConfigError(RuntimeError):
    """生产环境使用弱默认密钥，拒绝启动（文档 11 章）。"""


def _is_weak_secret(value: str) -> bool:
    value = value.strip()
    return value.lower() in WEAK_SECRET_VALUES or len(value) < MIN_SECRET_LEN


def assert_secure_secrets(settings: Settings) -> None:
    """生产环境（debug=False）强校验关键密钥，命中弱默认值则拒绝启动。

    - FEEDBACK_IP_SALT：IP 哈希加盐，同时是后台/C 端令牌密钥留空时的回退签名密钥
    - ADMIN_TOKEN_SECRET：管理后台令牌签名密钥（留空则回退到上面的加盐串）
    - USER_TOKEN_SECRET：C 端令牌签名密钥（留空则回退到上面的加盐串；显式配置时校验）
    """
    if settings.debug:
        return
    checks = [
        ("FEEDBACK_IP_SALT", settings.feedback_ip_salt),
        ("ADMIN_TOKEN_SECRET", settings.admin_token_key),
    ]
    if settings.user_token_secret.strip():
        checks.append(("USER_TOKEN_SECRET", settings.user_token_secret))
    problems = [name for name, value in checks if _is_weak_secret(value)]
    if problems:
        raise InsecureConfigError(
            "生产环境（DEBUG=false）检测到弱默认密钥："
            + "、".join(problems)
            + f"。请在 .env 中改为长度 ≥ {MIN_SECRET_LEN} 的随机串后再启动。"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()