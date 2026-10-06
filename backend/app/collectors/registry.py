"""采集器注册表：source 名 → 采集器实例（文档 4.1 优先级）。

P0 已实现：seed（人工种子）、rss（公开 feed）、html_list（公开列表页）、amap（地图 POI）。
大众点评 / 小红书等**反爬页面源仅保留适配位**：合规红线不允许绕过登录/验证码/加密，
故不提供抓取器；接入方式应为官方开放平台/合作，届时在此注册即可。
"""

from __future__ import annotations

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
            city_hint=city_hint,
        )
    if name == "amap":
        return AmapPoiCollector(settings=settings, deps=deps, city_hint=city_hint)

    if name in PLANNED_SOURCES:
        raise ValueError(f"数据源 '{name}' 本期未实现：{PLANNED_SOURCES[name]}")
    raise ValueError(f"未知数据源：{name}")


def describe_sources(settings: Settings) -> list[dict]:
    """供 `run_crawl.py --list` 展示各源就绪状态（不发起网络请求）。"""
    rows = []
    for name in P0_SOURCES:
        if name == "seed":
            from pathlib import Path

            ready = Path(DEFAULT_SEED_PATH).exists()
            note = DEFAULT_SEED_PATH
        elif name == "rss":
            ready = bool(settings.rss_url_list)
            note = f"{len(settings.rss_url_list)} 个 feed"
        elif name == "html_list":
            ready = bool(settings.html_list_url_list)
            note = f"{len(settings.html_list_url_list)} 个列表页"
        else:  # amap
            ready = settings.has_amap
            note = "已配置 key" if ready else "未配置 AMAP_API_KEY"
        rows.append({"source": name, "ready": ready, "note": note})
    for name, reason in PLANNED_SOURCES.items():
        rows.append({"source": name, "ready": False, "note": f"未实现：{reason}"})
    return rows