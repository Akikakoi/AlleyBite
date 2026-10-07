"""采集器注册表：source 名 → 采集器实例（文档 4.1 优先级）。

P0 已实现：seed（人工种子）、rss（公开 feed）、html_list（公开列表页）、amap（地图 POI）。
大众点评 / 小红书等**反爬页面源仅保留适配位**：合规红线不允许绕过登录/验证码/加密，
故不提供抓取器；接入方式应为官方开放平台/合作，届时在此注册即可。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.config import Settings
from .amap import AmapPoiCollector
from .base import BaseCollector
from .core import CollectorDeps, make_default_deps
from .html_list import HtmlListCollector
from .rss import RssCollector
from .seed import DEFAULT_SEED_PATH, SeedCollector

# 本期实际可运行的源
P0_SOURCES = ("seed", "rss", "html_list", "amap")

# 规划中但受合规/合作限制、暂不实现的源（文档 4.1 P0/P1/P2 中的反爬页面源）
PLANNED_SOURCES = {
    "dianping": "需官方开放平台/合作，禁止绕过反爬",
    "xiaohongshu": "需官方开放平台/合作，禁止绕过反爬",
    "weibo": "需微博开放平台 API",
    "zhihu": "搜索结果聚合，需合规评估",
}


def build_collector(
    name: str,
    settings: Settings,
    *,
    city_hint: str | None = None,
    deps: CollectorDeps | None = None,
    seed_path: str = DEFAULT_SEED_PATH,
) -> BaseCollector:
    """按 source 名构建采集器；未实现/未规划的源抛 ValueError。"""
    deps = deps or make_default_deps(settings)
    if name == "seed":
        return SeedCollector(
            settings=settings, deps=deps, path=seed_path, city_hint=city_hint
        )
    if name == "rss":
        return RssCollector(
            settings=settings,
            deps=deps,
            feed_urls=settings.rss_url_list,
            city_hint=city_hint,
        )
    if name == "html_list":
        return HtmlListCollector(
            settings=settings,
            deps=deps,
            list_urls=settings.html_list_url_list,
            page_urls=settings.html_page_url_list,
            city_hint=city_hint,
        )
    if name == "amap":
        return AmapPoiCollector(settings=settings, deps=deps, city_hint=city_hint)

    if name in PLANNED_SOURCES:
        raise ValueError(f"数据源 '{name}' 本期未实现：{PLANNED_SOURCES[name]}")
    raise ValueError(f"未知数据源：{name}")


# 来源台账（文档 4.1 / 11.1）：记录每源的获取方式、合规结论与 robots 判定，
# 供 `run_crawl.py --list` 展示与合规审计。缺失时不影响运行，格式错误则明确报错。
DEFAULT_SOURCE_LEDGER = "samples/sources.json"


def load_source_ledger(path: str | Path = DEFAULT_SOURCE_LEDGER) -> dict[str, dict]:
    """读取来源台账 JSON，返回 ``source -> 条目`` 映射；文件不存在时返回空映射。"""
    ledger_path = Path(path)
    if not ledger_path.exists():
        return {}
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    rows = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"来源台账格式错误：{path} 需为列表或含 sources 列表的对象")
    ledger: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("source"):
            ledger[str(row["source"])] = row
    return ledger


def _readiness(name: str, settings: Settings) -> tuple[bool, str]:
    """各 P0 源的动态就绪判定（不发起网络请求）。"""
    if name == "seed":
        return Path(DEFAULT_SEED_PATH).exists(), DEFAULT_SEED_PATH
    if name == "rss":
        feed_count = len(settings.rss_url_list)
        return bool(feed_count), f"{feed_count} 个 feed"
    if name == "html_list":
        lists = settings.html_list_url_list
        pages = settings.html_page_url_list
        return bool(lists or pages), f"{len(lists)} 个列表页 + {len(pages)} 个单页"
    # amap
    ready = settings.has_amap
    return ready, "已配置 key" if ready else "未配置 AMAP_API_KEY"


def _ledger_meta(name: str, entry: dict, *, default_status: str) -> dict:
    """从台账条目提取展示字段，缺项给空值。"""
    return {
        "source": name,
        "status": entry.get("status", default_status),
        "type": entry.get("type", ""),
        "compliance": entry.get("compliance", ""),
        "robots": entry.get("robots", ""),
        "qps": entry.get("qps"),
    }


def describe_sources(
    settings: Settings, *, ledger_path: str | Path = DEFAULT_SOURCE_LEDGER
) -> list[dict]:
    """供 `run_crawl.py --list` 展示各源就绪状态与合规台账（不发起网络请求）。"""
    ledger = load_source_ledger(ledger_path)
    rows: list[dict] = []

    for name in P0_SOURCES:
        ready, note = _readiness(name, settings)
        row = _ledger_meta(name, ledger.get(name, {}), default_status="active")
        row.update(ready=ready, note=note)
        rows.append(row)

    for name, reason in PLANNED_SOURCES.items():
        entry = ledger.get(name, {})
        row = _ledger_meta(name, entry, default_status="planned")
        row["compliance"] = row["compliance"] or reason
        row.update(ready=False, note=entry.get("note") or f"未实现：{reason}")
        rows.append(row)

    known = set(P0_SOURCES) | set(PLANNED_SOURCES)
    for name, entry in ledger.items():
        if name in known:
            continue
        row = _ledger_meta(name, entry, default_status="planned")
        row.update(ready=False, note=entry.get("note") or row["compliance"])
        rows.append(row)
    return rows