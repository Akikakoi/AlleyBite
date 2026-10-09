# 苍蝇馆子美食发现器（AlleyBite）

用户输入一个城市名，系统自动从多个公开数据源采集并清洗信息，用大模型做 NER 与口碑判断，识别出被**频繁、真实、非商业**提及的小馆子，输出带**地址、推荐菜、口碑关键词**的榜单——把"靠营销火"的网红店与"靠味道火"的苍蝇馆子区分开。

## 功能特性

**C 端（移动优先响应式 Web）**

- 城市搜索与榜单：综合分排序，按菜系 / 人均区间 / 行政区 / 时间维度（近 90 天 / 全部）筛选
- 店铺详情：地址、推荐菜、口碑关键词、来源引用与原文摘要、一键导航
- 地图模式：高德 JS API（未配置 Key 时降级为带坐标列表 + 导航链接）
- 账号体系：用户名口令注册登录 + 邮箱验证码登录（验证码哈希落库一次性消费、限流）
- 收藏与个人清单、浏览历史、个性化推荐（"猜你想吃"）
- UGC 打卡：文字 + 图片（先审后显）、纠错反馈（限流 + IP 哈希加盐）
- 分享：链接 / Canvas 生成榜单与店铺海报 / OG 预览

**运营后台（独立入口）**

- 数据看板（ECharts）、店铺与评论管理、采集任务管理、UGC 打卡审核、纠错处理、用户管理

**数据流水线（多源采集 → 清洗 → 抽取 → 打分）**

- 采集源：高德 POI（双轮关键词）、RSS、HTML 列表页 / 单页、人工种子；遵守 robots.txt、限速、UA 报备
- 清洗与结构化：正文抽取、分块、LLM 抽取（OpenAI 兼容协议，未配 Key 自动走 mock 可离线跑通）、对齐归一
- 苍蝇馆子特征打分：低调 / 本地化 / 口碑驱动 / 价格亲民 / 稳定性 等特征加权，排除网红营销特征
- 榜单快照 + Redis 缓存；Celery worker + beat 定时调度，支持手动触发

**运维**

- Prometheus 指标（`/metrics`）、Alertmanager 告警（经飞书 webhook 桥接）、Grafana 面板（均仅回环 / 内网可达，经 nginx 子路径反代）
- PostgreSQL WAL 归档 + 每日逻辑全量 + 每周物理基线，支持 14 天内任意时间点恢复（PITR）

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python / FastAPI / SQLAlchemy 2 / Pydantic v2 / Celery（broker 复用 Redis） |
| 数据 | PostgreSQL 15（生产）/ SQLite（本地调试）、Redis 7 |
| AI | OpenAI 兼容协议接大模型（默认 DashScope qwen-plus，可换任意兼容厂商） |
| C 端前端 | Vue 3 + TypeScript + Vite + Vant 4 |
| 管理后台 | Vue 3 + TypeScript + Vite + Element Plus + ECharts |
| 部署 | Docker Compose / nginx（托管前端 + 反代 `/api`）+ Prometheus / Grafana / Alertmanager |

## 目录结构

```
├── backend/            FastAPI 后端（app/collectors 采集、app/services 流水线与业务、app/tasks.py Celery）
│   └── scripts/        采集 / 抽取 / 流水线 / 建管理员等命令行脚本
├── web/                C 端前端（home / rank / detail / map / mine / source-notice）
├── web-admin/          管理后台前端（Dashboard / Restaurants / Audit / Crawl / Ugc / Users ...）
├── deploy/             证书、备份脚本、监控告警配置、CI
├── docker-compose.yml  全栈编排（api / worker / beat / web / web-admin / pg / redis / 监控）
├── start.ps1           Windows 一键启动脚本
└── 苍蝇馆子美食发现器-开发文档.md   完整设计文档（需求 / 架构 / 打分模型 / API / 部署）
```

## 快速开始（Docker 一键启动）

环境要求：Docker Desktop（含 Compose v2）。Windows 下在仓库根目录执行：

```powershell
.\start.ps1
```

脚本会自动完成：检查 Docker → 从 `.env.example` 生成 `.env`（并写入随机密钥）→ 生成自签 HTTPS 证书 → 构建并启动全部服务 → 健康检查 → 打开首页。首次构建需下载依赖，可能耗时十几分钟。

Linux / macOS 手动启动：

```bash
cp .env.example .env        # 按需填写 LLM_API_KEY / AMAP_API_KEY 等
sh deploy/certs/gen-self-signed.sh
docker compose up -d --build
```

### 访问入口

| 入口 | 地址 |
| --- | --- |
| C 端首页 | https://127.0.0.1/ （自签证书浏览器会提示不受信任，属预期） |
| 接口文档（Swagger） | https://127.0.0.1/docs |
| 健康检查 | https://127.0.0.1/health |
| 管理后台 | https://127.0.0.1:8443/ （仅内网 / 白名单可达） |
| Grafana / 告警管理台 | https://127.0.0.1/grafana/ 、https://127.0.0.1/alertmanager/ （仅回环） |

管理后台账号：`.env` 中设置 `ADMIN_PASSWORD` 后自动建号（默认用户名 `admin`），或手动执行：

```bash
python backend/scripts/create_admin.py --username admin --password <口令>
```

## 环境变量

完整清单见 `.env.example`，关键项：

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` | 大模型 Key；留空走 mock 模式，可离线跑通全流程 |
| `AMAP_API_KEY` | 高德开放平台 Key，POI 实体基准；留空跳过地图源 |
| `CORS_ORIGINS` | 前端域名白名单（逗号分隔） |
| `CRAWL_CONTACT` | 真实联系方式，用于采集 UA 报备，上线前务必填写 |
| `CRAWL_RSS_URLS` / `CRAWL_HTML_LIST_URLS` / `CRAWL_HTML_PAGE_URLS` | 自定义采集源 |
| `SCHEDULE_ENABLED` / `SCHEDULE_CITIES` | 定时调度开关与城市（留空读库） |
| `FEISHU_WEBHOOK_URL` | 飞书告警机器人；留空仅打日志 |
| `FEEDBACK_IP_SALT` / `ADMIN_TOKEN_SECRET` | 生产（DEBUG=false）必须为 ≥16 位随机串，否则 API 拒绝启动 |
| `SMTP_USER` / `SMTP_PASSWORD` | 邮箱验证码发信账号；留空走 mock 日志模式 |

## 常用命令

```bash
docker compose logs -f api      # 查看后端日志
docker compose restart api      # 重启 API
docker compose down             # 停止全部服务

# 本地开发（后端，SQLite）
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# 本地开发（前端）
cd web && npm install && npm run dev        # C 端
cd web-admin && npm install && npm run dev  # 管理后台

# 测试（后端）
cd backend && pytest
```

## 安全与合规

- 生产模式（`DEBUG=false`）启用弱密钥强校验与 CORS 收紧；管理后台独立端口独立令牌受众，公网入口已屏蔽运营接口
- 采集遵守 robots.txt、限速并报备 UA；店铺信息保留来源引用，用户跳转来源原文前有合规提示页
- 纠错提交按 IP 限流，IP 以加盐哈希存储；邮箱验证码一次性消费
- 数据库每日自动备份（`deploy/backup/`），WAL 归档支持 PITR

详细设计见《苍蝇馆子美食发现器-开发文档.md》。
