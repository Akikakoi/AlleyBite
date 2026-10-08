"""Celery 任务定义（文档 4.4 / 12 章 M4：调度平台化）。

替代 scripts/scheduler.py 常驻进程（该脚本保留，供本地/应急使用）：

    ├─ 每日 03:00  全量刷新各城市 POI 基础数据（高德 amap 官方 API）
    ├─ 每 6 小时   增量抓取文本源（seed/rss/html_list）
    ├─ 每日 05:00  抽取 → 对齐 → 打分 → 榜单快照（run_pipeline.py）
    └─ 每周一 06:00 数据质量巡检（data_quality_report.py）

设计要点：
- broker / result backend 复用 Redis（REDIS_URL），自动从 db0 换到 db1，
  与榜单缓存（db0）隔离键空间
- 任务体通过 subprocess 调既有脚本：job_run 记录、幂等约束、快照留存
  全部沿用脚本内已验证的实现，Celery 只负责"何时触发"
- SCHEDULE_ENABLED=false 时 beat 不注册任何计划（worker 仍可被 API 手动触发）
- 时刻/间隔沿用 SCHEDULE_* 环境变量，与轻量调度器同源

容器入口（docker-compose）：
    celery -A app.tasks worker --loglevel=info
    celery -A app.tasks beat   --loglevel=info
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from celery import Celery
from celery.schedules import crontab

ROOT = Path(__file__).resolve().parents[1]

_TASK_NAMES = ("tasks.refresh_poi", "tasks.crawl_text", "tasks.run_pipeline", "tasks.quality_report")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _broker_url(redis_url: str | None = None) -> str:
    """Celery broker：优先 CELERY_BROKER_URL，否则取 REDIS_URL 并切到 db1（与缓存隔离）。"""
    explicit = os.environ.get("CELERY_BROKER_URL")
    if explicit:
        return explicit
    redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    if redis_url.rstrip("/").endswith("/0"):
        return redis_url[:-1] + "1"
    return redis_url


app = Celery("alleybite", broker=_broker_url(), backend=_broker_url())
app.conf.update(
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=7 * 24 * 3600,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule_filename=os.environ.get("CELERY_BEAT_SCHEDULE_FILE", "/tmp/celerybeat-schedule"),
)


def _build_beat_schedule(
    *,
    enabled: bool,
    amap_hour: int,
    text_interval_hours: int,
    pipeline_hour: int,
    weekly_report: bool,
) -> dict:
    """beat 计划（纯函数，便于测试）。时间均为 Asia/Shanghai 本地时区。"""
    if not enabled:
        return {}
    schedule: dict = {
        "refresh-poi-daily": {
            "task": "tasks.refresh_poi",
            "schedule": crontab(hour=amap_hour, minute=0),
        },
        "crawl-text-interval": {
            "task": "tasks.crawl_text",
            "schedule": crontab(minute=0, hour=f"*/{max(1, text_interval_hours)}"),
        },
        "pipeline-daily": {
            "task": "tasks.run_pipeline",
            "schedule": crontab(hour=pipeline_hour, minute=0),
        },
    }
    if weekly_report:
        schedule["quality-weekly"] = {
            "task": "tasks.quality_report",
            "schedule": crontab(day_of_week=1, hour=6, minute=0),
        }
    return schedule


app.conf.beat_schedule = _build_beat_schedule(
    enabled=_env_bool("SCHEDULE_ENABLED", True),
    amap_hour=_env_int("SCHEDULE_AMAP_HOUR", 3),
    text_interval_hours=_env_int("SCHEDULE_TEXT_INTERVAL_HOURS", 6),
    pipeline_hour=_env_int("SCHEDULE_PIPELINE_HOUR", 5),
    weekly_report=True,
)


def _schedule_cities() -> list[str]:
    """调度用城市列表：显式配置优先，否则取库内有内容线索的城市（与轻量调度器同源）。"""
    raw = os.environ.get("SCHEDULE_CITIES", "").replace(";", ",")
    cities = [c.strip() for c in raw.split(",") if c.strip()]
    if cities:
        return cities
    sys.path.insert(0, str(ROOT))
    from app.db import SessionLocal
    from app.services import list_city_names

    with SessionLocal() as session:
        return list_city_names(session)


def _run_script(script: str, args: list[str], label: str) -> str:
    """执行仓库内脚本；失败抛异常让 Celery 记 FAILURE（job_run 已由脚本落库）。"""
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    print(f"[celery] 触发 {label}：{' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"{label} 失败（rc={proc.returncode}）：{tail}")
    return f"{label} 完成"


@app.task(name="tasks.refresh_poi")
def refresh_poi() -> list[str]:
    """每日 POI 全量刷新：官方地图 API，--force 忽略夜间暂停（非页面抓取）。"""
    out = []
    for city in _schedule_cities():
        out.append(
            _run_script(
                "run_crawl.py",
                ["--city", city, "--sources", "amap", "--mode", "full", "--force"],
                f"amap POI 全量[{city}]",
            )
        )
    return out


@app.task(name="tasks.crawl_text")
def crawl_text() -> str:
    """文本源增量抓取（seed/rss/html_list），脚本内部限流与熔断。"""
    return _run_script("run_crawl.py", ["--sources", "seed,rss,html_list"], "文本源增量抓取")


@app.task(name="tasks.run_pipeline")
def run_pipeline() -> str:
    """清洗 → 抽取 → 对齐 → 打分 → 榜单快照（全部有内容的城市）。"""
    return _run_script("run_pipeline.py", [], "抽取→对齐→打分→榜单")


@app.task(name="tasks.quality_report")
def quality_report() -> str:
    """每周数据质量巡检：去重率 / 抽取完整率 / 网红店误入率报表（输出进 worker 日志）。"""
    return _run_script("data_quality_report.py", [], "数据质量巡检")
