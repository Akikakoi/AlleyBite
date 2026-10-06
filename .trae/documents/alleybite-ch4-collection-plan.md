# 第 4 章 数据采集层 实施方案

## Context

当前项目已完成：采集落库之后的全部链路（清洗切块 5.2、LLM 抽取 5.3、实体对齐 5.5、打分 6、榜单快照 6.4）与前端 MVP。唯一缺口是**数据从哪来**——现在只有 `POST /api/v1/ingest` 手动喂数据、`samples/*.jsonl` 合成样本，没有第 4 章的采集层，因此榜单跑的是 demo 数据。

本方案补齐第 4 章 P0 骨架：**合规的采集框架 + 人工种子 + 公开 RSS/列表页 + 高德 POI 实体基准**，让采集能按调度自动把真实内容灌进既有 `ingest_raw_content` 链路，并从地图 API 建立权威店铺底座。

### 硬约束（不可违背，来自文档第 4/11 章）

- 官方 API 优先；页面抓取遵守 `robots.txt` 与用户协议；**不绕过登录/验证码/加密参数**；不采个人隐私。
- 只做**摘要引用 + 来源标注 + 跳转原平台**，不整篇转载（采集只落文本到 `raw_content`，来源超链接由前端溯源页承载）。
- 单域名 QPS ≤ 1、随机抖动 0.5–2s、夜间不采集；真实可联系 UA；失败指数退避最多 3 次、**4xx 不重试（429 除外）**；403/429 过多自动熔断暂停该源并告警。

### 本轮明确不做

Playwright 动态渲染；大众点评/小红书等**反爬页面的抓取器**（仅留适配位与 `source` 枚举）；Celery/内置调度器（用系统定时任务）；`bs4/lxml/feedparser`（用 stdlib 解析）；个人信息字段；地图 POI 走 LLM 二次抽取。

---

## 一、新增包 `app/collectors/`

依赖注入统一走 `CollectorDeps`，避免全局单例，保证测试零联网。

| 文件 | 职责与关键签名 |
| --- | --- |
| `core.py` | `CrawlItem`(source/source_url/raw_title/raw_text/published_at/raw_ref/city_hint)、`SourceResult`、`CollectorDeps`(`http`/`sleep`/`now`/`snapshot`/`alert`)、`HttpFetcher.get(url)->FetchResult`（httpx 包装，可注入 `MockTransport`）、`RetryPolicy.decide(status, attempt)->float\|None`（4xx→None 不重试；429/5xx→指数退避秒数） |
| `ratelimit.py` | `DomainRateLimiter(qps, jitter_min, jitter_max, sleep, now).acquire(host)`——按 host 保证间隔 ≥ 1/qps，并叠加随机抖动 |
| `robots.py` | `RobotsGate(http, user_agent).allows(url)->bool`——`urllib.robotparser`，按 host 缓存；拉取失败 **fail-open + 告警**（可配） |
| `snapshots.py` | `SnapshotStore(dir, ttl_days, now).save(source, url, body, ext)->Path`、`.purge_expired()`——本地快照目录替代文档里的 OSS，保留 90 天 |
| `base.py` | `BaseCollector`：构造注入 `deps + settings + city`；`fetch(since=None)->list[CrawlItem]`；统一走 限速→robots→重试→快照→解析；`source` 类属性 |
| `seed.py` | `SeedCollector(path)`（人工种子，JSON/JSONL）；解析纯函数 `parse_seed(data)->list[CrawlItem]` |
| `rss.py` | `parse_rss(xml_bytes, base_url)->list[FeedEntry]`（stdlib `xml.etree`）+ `RssCollector` |
| `html_list.py` | `extract_links(html, base_url)->list[str]`（stdlib `HTMLParser`）+ `HtmlListCollector`（公开列表页→正文摘要） |
| `amap.py` | `AmapPoiClient.search(city, page)->list[PoiRecord]`；`AmapPoiCollector` 无 key 时返回 `skipped`，**直写 restaurant 旁路**（见下） |
| `registry.py` | `P0_SOURCES` 常量 + `build_collector(name, settings, deps)->BaseCollector` |
| `runner.py` | `run_source(...)->SourceResult`、`run_crawl(session, settings, *, sources, mode, deps)->JobRun` |
| `__init__.py` | 照 `app/services/__init__.py` 风格显式 re-export + `__all__` |

**HTTP 抓取统一用 `HttpFetcher`**，禁止各采集器直接裸调 httpx，保证限速/UA/重试/快照只有一处实现。

---

## 二、数据流与既有链路的接入点

### 2.1 文本类源（seed / rss / html_list）→ 复用 `ingest_raw_content`

`runner` 对每个 `CrawlItem` 调用 [ingest_raw_content](file:///d:/AIProjects/AlleyBite/backend/app/services/ingest.py#L20-L90)（唯一入库出口），`(source, content_hash)` 的 `UNIQUE` 保证幂等。**不新增写库路径。**

### 2.2 高德 POI → 直写 `restaurant`（不走 LLM 抽取）

理由：POI 是权威**实体基准**（name/address/lat/lng/typecode），不是口碑文本；进抽取既烧 token 又会引入失真。且 `scoring_service` 完全由 mention 聚合驱动，**POI-only 店铺无 mention → 不进榜**，只作实体底座。

对齐兼容性（已核对 [alignment.py](file:///d:/AIProjects/AlleyBite/backend/app/services/alignment.py#L128-L157)）：`align_mentions` 按 `restaurants.name_norm` 建 `name_map`、按 `shop_alias` 建 `alias_map`，因此 POI 预写的 active restaurant 会被社媒 mention 自动命中；`_backfill_restaurant` 只在缺失时回填，**不会覆盖 POI 的权威地址**。这正好落地 alignment.py 头部注释里"地理约束待地图 POI 接入后补充"的 TODO。

实现：先按 `(city, poi_source="amap", poi_id)` 查，再按 `name_norm` 兜底 → upsert `Restaurant(poi_source, poi_id, latitude, longitude, address, area, name, name_norm=normalize_shop_name(name))` + `ShopAlias`。

### 2.3 增量游标：不新建表

`cursor = max(raw_content.published_at) WHERE source=? [AND city_hint=?]`（`source` 已有索引）。零迁移；`content_hash` 幂等是最终去重网，游标只负责"少拉"。时间统一归一到 UTC（SQLite 存 naive，需统一处理）。游标值写入 `job_run.stats` 便于排查。

### 2.4 熔断持久化：写 `job_run.stats`，不新建表

`job_run.stats["sources"][s]` 记 `http_403/http_429/fail_streak/tripped_until/error`。下次运行读最近一条 `job_type="crawl"`，`tripped_until > now` 则跳过该源并告警。满足"暂停+告警"要求且免迁移；局限（并发竞态、需至少一条历史行）写进注释。升级路径：轻量 `crawl_source_state` 表（本期不做）。

---

## 三、配置新增（`app/core/config.py` + `backend/.env.example`）

字段用 snake_case，自动映射文档附录 B 的 `CRAWL_*` 环境变量：

```ini
CRAWL_QPS_PER_DOMAIN=1
CRAWL_JITTER_MIN=0.5
CRAWL_JITTER_MAX=2
CRAWL_NIGHT_PAUSE=true
CRAWL_NIGHT_START=23
CRAWL_NIGHT_END=7
CRAWL_RETRY_MAX=3
CRAWL_TIMEOUT=20
CRAWL_CONTACT=            # 填联系方式，用于拼真实 UA
SNAPSHOT_DIR=./snapshots
CRAWL_SNAPSHOT_TTL_DAYS=90
CRAWL_MAX_ITEMS_PER_SOURCE=200
CRAWL_BREAKER_FAIL_THRESHOLD=5
CRAWL_BREAKER_COOLDOWN_MINUTES=360
AMAP_API_KEY=             # 留空则跳过高德源
```

配套：`crawl_user_agent` 由 `crawl_contact` 派生（property，形如 `AlleyBiteBot/0.1 (+联系方式)`）；`has_amap` property。`.env.example` 补上述项并加注释。**不改动现有 `.env`**（含真实 LLM key）。

---

## 四、脚本 `scripts/run_crawl.py`

仿 [run_pipeline.py](file:///d:/AIProjects/AlleyBite/backend/scripts/run_pipeline.py) 形态：`sys.path` 注入 + `init_db()` + `SessionLocal`，argparse：

```
python scripts/run_crawl.py --list                      # 列出可用源与是否就绪（如 amap 无 key）
python scripts/run_crawl.py --city 成都 --sources seed  # 跑指定源
python scripts/run_crawl.py --city 成都 --mode full
```

- 落一条 `job_run(job_type="crawl")`，`stats={mode, sources:{s:{status,fetched,new,duplicated,failed,http_403,http_429,cursor_at,error}}, totals:{...}, breaker:{tripped:[...]}}`。
- **每源 try/except + 每源结束即 commit**，单源失败不中断其余。
- docstring 写明系统定时任务建议（对齐文档 4.4）：03:00 POI 刷新、每 6h 增量、05:00 调 `run_pipeline.py`、周一巡检。**不实现调度器。**

新增一个示例种子文件 `samples/seeds.sample.jsonl` 供跑通用。

---

## 五、测试（`tests/`，沿用"每文件自带 fixture"约定，不建 conftest）

全程零联网：`httpx.MockTransport` + 注入 fake `http`/`sleep=lambda _:None`/固定 `now`。

- `test_collectors_ratelimit.py`：间隔 ≥ 1/qps、抖动落在区间
- `test_collectors_robots.py`：Allow / Disallow / 拉取失败 fail-open
- `test_collectors_retry.py`：500 重试、404 不重试、429 重试（断言调用次数与退避）
- `test_collectors_breaker.py`：连续 403/429 达阈值→tripped→下次跳过 + alert 被调用
- `test_collectors_seed.py`：重复导入条数不变（幂等）
- `test_collectors_rss.py` / `test_collectors_html_list.py`：内联 XML/HTML 解析
- `test_collectors_runner.py`：fake collector 编排、单源失败隔离、`job_run` 落库与 stats
- `test_collectors_amap.py`：无 key→skipped；MockTransport 返回 POI JSON→直写 restaurant，重跑幂等

---

## 六、验证方式

1. `cd backend && .\.venv\Scripts\python.exe -m pytest -q` —— 新增用例通过，**既有 85 个用例保持全绿**（不改动 5/6 章逻辑）。
2. `python scripts/run_crawl.py --list` —— 正确显示 seed/rss/html_list 就绪、amap 因无 key 显示未配置。
3. `python scripts/run_crawl.py --city 成都 --sources seed` —— 种子入库；重复执行 `new=0 / duplicated=N`（幂等）。
4. 随后 `python scripts/run_pipeline.py 成都` —— 新采集内容能被抽取、对齐、进榜，验证与既有链路贯通。
5. `.\.venv\Scripts\python.exe -c "import os;print(os.path.isdir('snapshots'))"` + 快照清理用例，确认留存目录与 TTL 生效。
6. 若提供 `AMAP_API_KEY`：`--sources amap --city 成都`，确认 restaurant 新增且带 `poi_source/poi_id/lat/lng`，重跑不重复。

---

## 七、最易出问题的点（实现时重点盯）

1. **时间/游标**：SQLite naive datetime 与 UTC 混用会导致增量漏采或重采 → 统一 UTC，`content_hash` 幂等兜底。
2. **POI 直写重复**：`ix_restaurant_city_norm` 非唯一、`poi_id` 无唯一约束 → 必须"先按 poi_id 查、再按 name_norm 兜底"的代码级 upsert，同源顺序执行。
3. **"假合规"**：robots 解析异常、429 误判、快照无限增长或落个人隐私 → fail-open + 告警、快照 90 天清理、只存页面正文不存作者身份信息。

## 八、涉及文件

**新增**：`app/collectors/`（12 个模块）、`scripts/run_crawl.py`、`samples/seeds.sample.jsonl`、`tests/test_collectors_*.py`（8 个）。

**修改**：`app/core/config.py`（新增配置与 property）、`backend/.env.example`（补 CRAWL_*/AMAP_* ）、`app/services/__init__.py`（如需对外暴露 runner 则补充导出）。

**不改动**：`ingest.py`、`alignment.py`、`scoring*`、`rank_service.py`、`main.py`、前端。