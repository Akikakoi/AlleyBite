"""轻量调度器（文档 4.4）：常驻容器按时间触发既有脚本，替代 Celery beat。

本项目不引入 Celery（部署骨架阶段），改用此常驻进程按文档 4.4 的时间表调用
现成脚本，任务幂等性由脚本内部的 job_run 与内容唯一约束保证：

    ├─ 每日 03:00  全量刷新各城市 POI 基础数据（高德 amap 官方 API）
    ├─ 每 6 小时   增量抓取文本源（seed/rss/html_list）
    └─ 每日 05:00  抽取 → 对齐 → 打分 → 榜单快照（run_pipeline.py）

用法：
    python scripts/scheduler.py --list          # 打印计划与城市
    python scripts/scheduler.py --run <name>     # 立即执行一次并退出（联调用）
    python scripts/scheduler.py                  # 常驻（容器入口）

环境变量：
    SCHEDULE_ENABLED=true|false        # 关闭后仅常驻不触发（默认 true）
    SCHEDULE_CITIES=成都,重庆          # 留空则由数据库自动读取城市列表
    SCHEDULE_TICK_SECONDS=30           # 检查周期（秒）
    SCHEDULE_AMAP_HOUR=3               # 每日 POI 全量刷新时刻（小时）
    SCHEDULE_TEXT_INTERVAL_HOURS=6     # 文本源增量抓取间隔（小时）
    SCHEDULE_PIPELINE_HOUR=5           # 每日流水线时刻（小时）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services import list_city_names  # noqa: E402


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


def _schedule_cities() -> list[str]:
    """调度用城市列表：显式配置优先，否则取库内有内容线索的城市。"""
    raw = os.environ.get("SCHEDULE_CITIES", "").replace(";", ",")
    cities = [c.strip() for c in raw.split(",") if c.strip()]
    if cities:
        return cities
    with SessionLocal() as session:
        return list_city_names(session)


def _run_script(script: str, args: list[str], label: str) -> None:
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    print(f"[scheduler] {datetime.now():%F %T} 触发 {label}：{' '.join(cmd)}", flush=True)
    try:
        subprocess.run(cmd, cwd=str(ROOT), check=False)
    except Exception as exc:  # noqa: BLE001  调度不得因单次失败退出
        print(f"[scheduler] {label} 执行异常：{exc}", flush=True)


def _job_amap() -> None:
    """官方地图 API 全量刷新 POI：--force 忽略夜间暂停（官方接口，非页面抓取）。"""
    for city in _schedule_cities():
        _run_script(
            "run_crawl.py",
            ["--city", city, "--sources", "amap", "--mode", "full", "--force"],
            f"amap POI 全量[{city}]",
        )


def _job_text() -> None:
    _run_script(
        "run_crawl.py",
        ["--sources", "seed,rss,html_list"],
        "文本源增量抓取",
    )


def _job_pipeline() -> None:
    _run_script("run_pipeline.py", [], "抽取→对齐→打分→榜单")


def _plan() -> list[dict]:
    return [
        {"name": "amap", "kind": "daily", "hour": _env_int("SCHEDULE_AMAP_HOUR", 3), "job": _job_amap},
        {"name": "text", "kind": "interval", "hours": _env_int("SCHEDULE_TEXT_INTERVAL_HOURS", 6), "job": _job_text},
        {"name": "pipeline", "kind": "daily", "hour": _env_int("SCHEDULE_PIPELINE_HOUR", 5), "job": _job_pipeline},
    ]


def _list_plan() -> None:
    print("调度计划：")
    for task in _plan():
        when = (
            f"每日 {task['hour']:02d}:00"
            if task["kind"] == "daily"
            else f"每 {task['hours']} 小时（整点）"
        )
        print(f"  - {task['name']:<9} {when}")
    cities = _schedule_cities()
    print(f"城市（{len(cities)}）：{', '.join(cities) if cities else '（暂无，库内无内容）'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AlleyBite 轻量调度器（文档 4.4）")
    parser.add_argument("--list", action="store_true", help="打印计划与城市")
    parser.add_argument("--run", default=None, help="立即执行指定任务（amap/text/pipeline）后退出")
    args = parser.parse_args(argv)

    if args.list:
        _list_plan()
        return 0

    if args.run:
        tasks = {t["name"]: t for t in _plan()}
        task = tasks.get(args.run)
        if task is None:
            print(f"未知任务：{args.run}（可选：{', '.join(tasks)}）")
            return 2
        task["job"]()
        return 0

    enabled = _env_bool("SCHEDULE_ENABLED", True)
    tick = max(5, _env_int("SCHEDULE_TICK_SECONDS", 30))
    plan = _plan()
    fired: dict[str, str] = {}  # 任务名 → 已触发的日期/小时标记，防重复

    _list_plan()
    print(f"[scheduler] 启动常驻循环，tick={tick}s，enabled={enabled}", flush=True)

    while True:
        now = datetime.now()
        for task in plan:
            stamp = f"{now:%Y-%m-%d}" if task["kind"] == "daily" else f"{now:%Y-%m-%d-%H}"
            if fired.get(task["name"]) == stamp:
                continue
            due = (
                now.hour == task["hour"] and now.minute == 0
                if task["kind"] == "daily"
                else now.hour % task["hours"] == 0 and now.minute == 0
            )
            if not due:
                continue
            fired[task["name"]] = stamp
            if enabled:
                # 后台线程执行，避免长任务阻塞下一次 tick
                threading.Thread(target=task["job"], daemon=True).start()
            else:
                print(f"[scheduler] 已禁用，跳过 {task['name']}", flush=True)
        time.sleep(tick)


if __name__ == "__main__":
    raise SystemExit(main())