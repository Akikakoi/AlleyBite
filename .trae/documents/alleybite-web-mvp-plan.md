# 接入前端：MVP P0 四页（Vue3 + Vite + TS + Pinia + Vant）

## 1. 目标与范围

后端（FastAPI）已达「采集→清洗→切块→抽取→对齐→打分→榜单快照」全链路可用（79 tests passed），但**没有前端**，且缺少前端所需的 3 组读接口。本计划：

- **Part A（后端补接口）**：补齐文档 8.1 要求、前端详情/首页必需的接口，并把榜单条目的「mention 聚合逻辑」抽成可复用的私有函数。
- **Part B（前端 `web/`）**：按文档 9.1/9.2/9.3 落地 MVP P0 四页（首页 / 榜单 / 详情 / 来源提示）+ 分享。

**明确不做（本期）**：地图模式、深色模式、近 90 天排序、筛选以外的排序切换、收藏、纠错写接口、登录/JWT、SSR 预渲染、管理后台（9.5）、小程序。

## 2. 现状分析（已核实）

### 后端

- [main.py](file:///d:/AIProjects/AlleyBite/backend/app/main.py)：统一响应 `{"code":0,"message":"ok","data":...}`（`ok()` 辅助函数）；错误用 `HTTPException(404, detail=...)`。已有 `/health`、`/api/v1/extract`、`/ingest`、`/raw-contents/{id}`、`/raw-contents/{id}/extract`、`/scoring/shops`、`/alignment/run`、`/alignment/reviews`、`/restaurants`（列表）、`/rank`、`/rank/build`、`/jobs/run`、`/admin/jobs`。**缺 `GET /cities`、`/cities/search`、`/restaurants/{id}`、`/restaurants/{id}/sources`。**
- [rank_service.py](file:///d:/AIProjects/AlleyBite/backend/app/services/rank_service.py#L38-L92)：`_build_item(session, score, rank)` 内联了「mention→口碑关键词/推荐菜/来源摘要/last_mentioned_at/address 兜底」逻辑（常量 `_MAX_KEYWORDS=5`、`_MAX_DISHES=5`、`_MAX_SOURCES=3`、`_EXCERPT_LIMIT=120`）。榜单条目结构 = 文档 7.3。
- [scoring_service.py](file:///d:/AIProjects/AlleyBite/backend/app/services/scoring_service.py#L26-L107)：`_fact_from_row(row)` 由 mention 行构造 `MentionFact`；`collect_shop_scores(...)` 按 `restaurant_id` 聚合（`status != "active"` 跳过）。无「单店打分」函数。
- [models.py](file:///d:/AIProjects/AlleyBite/backend/app/db/models.py)：`Restaurant` 有 `name/area/address/cuisine/avg_price/latitude/longitude/status`；`Mention` 有 `praise_keywords/complaints/dishes/evidence_span/restaurant_id`；`City` 有 `name/code/status`；`RankSnapshot.items` 为 JSON。
- **测试惯例**：现有 10 个测试文件全部是「in-memory SQLite + 直接调 service」模式，**无 TestClient/httpx 用法**（见 [test_rank.py](file:///d:/AIProjects/AlleyBite/backend/tests/test_rank.py)、[test_pipeline.py](file:///d:/AIProjects/AlleyBite/backend/tests/test_pipeline.py)）。新增测试沿用该惯例（service 级），避免引入 TestClient 与全局 engine 的耦合。
- `City` 行是在对齐时**惰性创建**的（[pipeline.py](file:///d:/AIProjects/AlleyBite/backend/app/services/pipeline.py#L96-L108) 注释已说明），故 `/cities` 需与 `raw_content.city_hint` 取并集。

### 前端

- 工作区 `d:\AIProjects\AlleyBite` 下**只有 `backend/`**，无 `web/`（Glob 已确认）。
- Node v24.15.0 / npm 11.12.1 可用。
- 设计依据：文档 9.1（目录）、9.2（页面要点）、9.3（UI 规范：主色 `#FF6B35`、辅色 `#2F4F4F`、卡片圆角 16px、字号 17/14/12、断点 375/768/1200）。

## 3. Part A：后端补接口

### A1. [rank_service.py](file:///d:/AIProjects/AlleyBite/backend/app/services/rank_service.py) — 抽出可复用的 enrichment

**改法**：把 `_build_item` 中「按 restaurant_id 聚合 mention」的段落提为模块私有函数，供榜单与详情共用：

```python
def _collect_sources(mentions, limit: int) -> list[dict]:
    """按 raw_content 去重取来源引用；excerpt 取 evidence_span 前 _EXCERPT_LIMIT 字。"""
    # 返回 [{"source","source_url","title","excerpt","published_at"(iso|None)}]

def _enrich_from_mentions(session, restaurant_id: int) -> dict:
    """返回 {praise_keywords, complaints, recommended_dishes, mention_count,
             last_mentioned_at, address_text, location_hint, sources(默认 _MAX_SOURCES)}"""
```

`_build_item` 改为调用 `_enrich_from_mentions`，输出字段与现状**完全一致**（保持 `/rank` 响应不变），额外给 sources 条目补 `source_url`（详情页需要，榜单多一个字段无副作用）。

**新增**：

```python
MAX_DETAIL_SOURCES = 10

def build_restaurant_detail(session, restaurant_id: int, *,
                            settings=None, now=None) -> dict | None:
    """店铺详情（文档 9.2 detail / 8.1）。不存在 → None。"""
```

返回结构（在榜单条目基础上）：

```jsonc
{
  "restaurant_id": 12, "name": "明婷饭店", "area": "青羊区",
  "address": "青羊区同心路...", "location": {"lat":..,"lng":..}|null,
  "cuisine": "川菜", "avg_price": 65, "status": "active",
  "score": 76.5, "mention_count": 4, "last_mentioned_at": "2026-09-28",
  "praise_keywords": [...], "complaints": [...], "recommended_dishes": [...],
  "sources": [ {"source","source_url","title","excerpt","published_at"} ]  // 上限 10
}
```

打分来自 A2 的 `score_one_restaurant`；若该店被硬规则剔除，`score=0.0` 但仍返回详情（详情页展示 0 分与剔除原因字段 `exclude_reason`）。

```python
def build_restaurant_sources(session, restaurant_id: int, *, limit: int = 20) -> list[dict] | None:
    """店铺来源引用列表（文档 8.1 /restaurants/{id}/sources）。restaurant 不存在 → None。"""
```

### A2. [scoring_service.py](file:///d:/AIProjects/AlleyBite/backend/app/services/scoring_service.py) — 加单店打分

```python
def score_one_restaurant(session, restaurant_id: int, *,
                         settings=None, now=None) -> ShopScore | None:
    """对单个 restaurant 聚合其 mention 并打分；restaurant 不存在 → None。"""
```

实现：`session.get(Restaurant, restaurant_id)`；查 `MentionRow.restaurant_id == restaurant_id`（`joinedload(raw_content)`）；用现有 `_fact_from_row` 构造 facts；复用 `aggregate_shop` + `score_shop`，并 `result.restaurant_id = restaurant_id`。**允许** `status != "active"`（详情页仍可展示被合并前的分数），由调用方决定 404。

### A3. [pipeline.py](file:///d:/AIProjects/AlleyBite/backend/app/services/pipeline.py) — 城市列表（含状态）

```python
def list_cities(session) -> list[dict]:
    """城市列表（文档 8.1 /cities）：City 表 ∪ raw_content.city_hint 去重。

    返回 [{"name","code","status","has_rank"}]，按 name 排序。
    仅有内容线索、尚无 City 行的城市补 status="collecting"。
    """
```

`has_rank` = 该城市存在 `RankSnapshot`（用 `select(RankSnapshot.id).join(City)...exists()` 或一次性查询快照的 city_id 集合）。

### A4. [main.py](file:///d:/AIProjects/AlleyBite/backend/app/main.py) — 4 个端点

| 方法 | 路径 | 行为 | 异常 |
| --- | --- | --- | --- |
| GET | `/api/v1/cities` | `ok(list_cities(session))` | — |
| GET | `/api/v1/cities/search?q=` | `ok([c for c in list_cities(session) if q in c["name"]])`（`q` 空则返回全部） | — |
| GET | `/api/v1/restaurants/{id}` | `ok(build_restaurant_detail(...))` | 不存在 → `404`「店铺不存在」 |
| GET | `/api/v1/restaurants/{id}/sources` | `ok(build_restaurant_sources(...))` | 不存在 → `404`「店铺不存在」 |

放在现有 `list_restaurants` 之后、`/api/v1/rank` 之前。`imports` 追加 `build_restaurant_detail`、`build_restaurant_sources`、`list_cities`。

### A5. [services/\_\_init\_\_.py](file:///d:/AIProjects/AlleyBite/backend/app/services/__init__.py) — 导出

追加导出：`build_restaurant_detail`、`build_restaurant_sources`、`list_cities`、`score_one_restaurant`（同步登记到 `__all__`）。

### A6. 新增 `backend/tests/test_restaurant_detail.py`

按现有 service 级惯例（in-memory SQLite + `Base.metadata.create_all`）覆盖：

1. `test_score_one_restaurant_counts_mentions`：一店两条 mention（名称变体）经 `align_mentions` 归并后，`score_one_restaurant` 返回 `mention_count==2`、`restaurant_id` 正确。
2. `test_score_one_restaurant_missing_returns_none`。
3. `test_build_restaurant_detail_fields`：断言 `name/area/avg_price/praise_keywords/recommended_dishes/sources` 与 demo 样例一致；`sources[0]` 含 `source`/`source_url`/`excerpt`。
4. `test_build_restaurant_detail_missing_returns_none`。
5. `test_build_restaurant_sources_limit_and_dedup`：同一 raw_content 的多条 mention 只产生一条来源；`limit` 生效。
6. `test_list_cities_union_hint_and_city`：仅有 `raw_content.city_hint` 时 `status=="collecting"`；构建榜单快照后 `has_rank is True`。

复用 [test_scoring.py](file:///d:/AIProjects/AlleyBite/backend/tests/test_scoring.py#L31-L47) 的 `make_settings` / `session` fixture 写法。

## 4. Part B：前端 `web/`

### B1. 工程文件

| 文件 | 内容要点 |
| --- | --- |
| `web/package.json` | 依赖：`vue@^3.5`、`vue-router@^4.4`、`pinia@^2.2`、`vant@^4.9`、`axios@^1.7`；dev：`vite@^6`、`@vitejs/plugin-vue`、`typescript@~5.6`、`vue-tsc`。scripts：`dev` / `build`(`vue-tsc --noEmit && vite build`) / `preview` |
| `web/vite.config.ts` | `plugin-vue`；`resolve.alias { "@": /src }`；`server.proxy["/api"] → http://127.0.0.1:8000`（changeOrigin，不算 rewrite） |
| `web/tsconfig.json` + `tsconfig.node.json` | 标准 Vue+Vite TS 配置，`paths: {"@/*": ["./src/*"]}` |
| `web/index.html` | `<meta viewport>`、`<title>苍蝇馆子美食发现器</title>`、`<div id="app">` |
| `web/.env.development` | `VITE_API_BASE=/api` |
| `web/.env.production` | `VITE_API_BASE=/api`（生产由 nginx 反代；可改绝对域名） |
| `web/.gitignore` | `node_modules/`、`dist/`、`.env.local` |
| `web/src/env.d.ts` | `/// <reference types="vite/client" />` + `ImportMetaEnv.VITE_API_BASE` |

### B2. 基础设施

| 文件 | 内容要点 |
| --- | --- |
| `src/main.ts` | `createApp(App).use(router).use(createPinia()).mount('#app')`；`import 'vant/lib/index.css'`；`import '@/styles/theme.css'` |
| `src/App.vue` | 仅 `<router-view v-slot>` + `KeepAlive` 关闭；全局 `<van-toast>` 由 Vant 按需挂载（用 `showToast` 函数式 API，无需组件） |
| `src/config/index.ts` | `API_BASE = import.meta.env.VITE_API_BASE || '/api'`；`REQUEST_TIMEOUT = 8000` |
| `src/types/index.ts` | `City`、`RankItem`、`RankData`、`RestaurantDetail`、`SourceRef` 接口（字段对齐 A1/A3 返回） |

### B3. 请求与接口封装

| 文件 | 内容要点 |
| --- | --- |
| `src/api/request.ts` | axios 实例（`baseURL: API_BASE`, `timeout: 8000`）；响应拦截解包 `res.data.data`；`code !== 0` 抛 `ApiError`；错误拦截统一转 `ApiError{status,code,message}`（网络错误 message="网络异常，请重试"）。导出 `isNotFound(e)` 辅助（`e.status === 404`） |
| `src/api/cities.ts` | `getCities()`、`searchCities(q)` |
| `src/api/rank.ts` | `getRank({city, page=1, pageSize=20, cuisine?, priceMin?, priceMax?, area?})` → `RankData` |
| `src/api/restaurants.ts` | `getRestaurant(id)` → `RestaurantDetail`、`getRestaurantSources(id)` → `SourceRef[]` |

### B4. 状态与路由

| 文件 | 内容要点 |
| --- | --- |
| `src/store/city.ts` | Pinia `useCityStore`：`current: string`、`recent: string[]`（最多 6）；`setCity(name)` 写入并更新 recent；`loadFromStorage()` / 内部持久化到 `localStorage` key `alleybite.city`、`alleybite.recent` |
| `src/router/index.ts` | 路由：`/`→home、`/rank`→rank、`/detail/:id`→detail、`/source`→source-notice；全部 `() => import()` 懒加载；`scrollBehavior` 回顶；`afterEach` 按 `meta.title` 设置 `document.title` |

### B5. 视图（4 页，MVP P0）

| 文件 | 内容要点 |
| --- | --- |
| `src/views/home/HomeView.vue` | 顶部品牌 + 搜索框（`van-search`，回车/点击→`store.setCity` 并 `router.push({path:'/rank', query:{city}})`）；中部「热门城市」九宫格（`getCities()`，点击同上）；底部「最近浏览」（`store.recent`，点击跳榜单）；接口失败 → `EmptyState` 可重试 |
| `src/views/rank/RankView.vue` | 读 `route.query.city`（缺失则用 `store.current`，仍无则跳首页）；`getRank` 首屏 + 触底加载（`van-list`）；顶部城市名 + 分享按钮；`FilterBar`（菜系/人均/区域 → 重新请求 page=1，写回 query 便于分享）；`ShopCard` 列表；**404 或 total=0 → `EmptyState`「正在收录中，开城后通知你」**；分享用 `utils/share` |
| `src/views/detail/DetailView.vue` | `getRestaurant(route.params.id)`；头部（店名/区域/人均/综合分）；推荐菜标签云（`KeywordTag` neutral）；口碑关键词（positive 绿 / complaints 黄）；地址 + 「一键导航」（`https://uri.amap.com/search?keyword=<address>` 新窗口）；来源引用列表（点条目 → `/source?url=&source=&title=`）；底部固定声明「数据来源于公开信息，仅供参考，信息可能滞后」；「纠错」按钮 → `showToast('功能开发中')`；404 → `EmptyState` |
| `src/views/source-notice/SourceNoticeView.vue` | 读 query `url/source/title`；展示合规提示（「即将跳转第三方页面，内容由其负责…」）；「继续访问」→ `window.open(url)`（`rel=noopener`）；「返回」→ `router.back()`；无 url 时禁用按钮 |

### B6. 组件

| 文件 | 内容要点 |
| --- | --- |
| `src/components/ShopCard.vue` | props `item: RankItem`；排名徽标（1–3 特殊色 `#FF6B35`）、店名、`area`·`cuisine`、`formatPrice(avg_price)`、praise 标签（`KeywordTag`）、推荐菜（前 3，`hidden` 溢出）、`mention_count` 条口碑；点击 `emit('click')` 或内部 `router.push` |
| `src/components/KeywordTag.vue` | props `text: string`、`type: 'good'\|'warn'\|'neutral'`；胶囊样式 |
| `src/components/FilterBar.vue` | props 当前值；`van-dropdown-menu` 三项（菜系/人均区间/区域）；人均用预设档（`<30`、`30-60`、`60-100`、`>100` → 映射 `price_min/price_max`）；区域/菜系选项由父组件传入（来自当前榜单 items 去重）；`emit('change', filters)` |
| `src/components/EmptyState.vue` | props `title`、`desc?`、`icon?`、`showAction?`；slot `action` 放按钮；默认插画用内联 SVG（不引外部图） |

### B7. 工具与样式

| 文件 | 内容要点 |
| --- | --- |
| `src/utils/format.ts` | `formatPrice(v)` → `人均 ¥65` / `人均待补充`；`formatScore(v)` → `76.5`；`formatDate(iso)` → `9月28日` |
| `src/utils/share.ts` | `shareLink({title, text, url})`：`navigator.share` 存在则调用，否则 `navigator.clipboard.writeText` + `showToast('链接已复制')`；`navigator.share` 抛 `AbortError` 时静默 |
| `src/styles/theme.css` | `:root` 变量：`--color-primary:#FF6B35`、`--color-secondary:#2F4F4F`、`--radius-card:16px`、字号变量；移动优先布局，断点 `375/768/1200`（≥768 榜单两列、≥1200 三列，用 `grid-template-columns`）；卡片磨砂玻璃（`backdrop-filter: blur`）；基础 reset |

## 5. 假设与决策

1. **错误码映射**：后端现有端点对「无数据/不存在」均返回 HTTP 404 + `detail` 文案，**未**在 body 里实现 8.3 的 2001/2002。前端 `request.ts` 统一转 `ApiError{status}`，榜单页把 404 视为「正在收录」。不改造后端错误码体系（超出本次范围）。
2. **测试用 service 级**：沿用现有 10 个测试文件的惯例，不引入 TestClient/httpx 依赖。
3. **Vant 全量引入**（`vant/lib/index.css`），不做 unplugin 按需——MVP 简化，构建体积非当前瓶颈。
4. **不做 SSR/prerender**（文档 9.4 提及），Vite SPA 起步；分享链接为普通 URL + query，社交预览留待后续。
5. **不做排序切换**（综合分/近90天/人均）——后端 `/rank` 仅支持按快照顺序（综合分），排序属 V1.1。
6. **纠错入口占位**：`POST /api/v1/feedback` 需鉴权，属 P1，MVP 仅放按钮 + Toast。
7. **详情页 sources 上限 10**、千条 `excerpt` 沿用 `_EXCERPT_LIMIT=120`；`/restaurants/{id}/sources` 保留完整列表能力。
8. **本地联调**：前端 dev server 通过 Vite proxy 走 `/api` → `127.0.0.1:8000`，避免 CORS；后端 `debug=True` 时 CORS 已放开。

## 6. 执行步骤（按序）

1. A1 重构 `rank_service.py` 并跑 `pytest`（确保 79 项仍全绿，`/rank` 响应不回归）。
2. A2 `scoring_service.score_one_restaurant` + A3 `pipeline.list_cities`。
3. A4 `main.py` 4 端点 + A5 `__init__` 导出。
4. A6 新增 `test_restaurant_detail.py`；`pytest -q` 全绿。
5. B1–B7 创建 `web/`（工程 → 基础设施 → api/store/router → 组件 → 视图 → 样式）。
6. `npm install` → `npm run build`（类型检查 + 构建通过）。

## 7. 验证

**后端**

```powershell
cd d:\AIProjects\AlleyBite\backend
.\.venv\Scripts\python.exe -m pytest -q          # 期望：全绿（79 + 新增）
.\.venv\Scripts\python.exe scripts\demo_ingest.py # 造数据（幂等可重跑）
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

```powershell
curl "http://127.0.0.1:8000/api/v1/cities"
curl "http://127.0.0.1:8000/api/v1/cities/search?q=成"
curl "http://127.0.0.1:8000/api/v1/rank?city=成都&page=1&page_size=20"
curl "http://127.0.0.1:8000/api/v1/restaurants/1"
curl "http://127.0.0.1:8000/api/v1/restaurants/1/sources"
curl "http://127.0.0.1:8000/api/v1/restaurants/999999"   # 期望 404
```

**前端**

```powershell
cd d:\AIProjects\AlleyBite\web
npm install
npm run build        # vue-tsc 类型检查 + vite build 通过
npm run dev          # 打开 http://localhost:5173
```

**端到端**：首页搜索「成都」→ 榜单页出卡片（排名/人均/口碑关键词）→ 点卡片进详情（推荐菜/关键词/地址/来源）→ 点来源进提示页 → 点「继续访问」开新窗口。断网/无数据城市 → 显示「正在收录」空态。