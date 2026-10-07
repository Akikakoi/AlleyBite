"""数据采集层（开发文档第 4 章）。

对外导出采集编排入口、注册表与基础类型。
"""

from .amap import AmapPoiCollector, PoiRecord, parse_poi, upsert_pois
from .base import BaseCollector
from .core import (
    CollectorDeps,
    CrawlItem,
    FetchResult,
    HttpFetcher,
    RetryPolicy,
    SourceResult,
    make_default_deps,
)
from .html_list import (
    HtmlListCollector,
    extract_links,
    extract_text,
    extract_title,
    parse_page_specs,
    strip_html,
)
from .ratelimit import DomainRateLimiter
from .registry import PLANNED_SOURCES, P0_SOURCES, build_collector, describe_sources
from .robots import RobotsGate
from .rss import FeedEntry, RssCollector, parse_rss
from .runner import run_crawl, run_source
from .seed import DEFAULT_SEED_PATH, SeedCollector, parse_seed_records
from .snapshots import SnapshotStore

__all__ = [
    "AmapPoiCollector",
    "BaseCollector",
    "CollectorDeps",
    "CrawlItem",
    "DEFAULT_SEED_PATH",
    "DomainRateLimiter",
    "FetchResult",
    "FeedEntry",
    "HtmlListCollector",
    "HttpFetcher",
    "PLANNED_SOURCES",
    "P0_SOURCES",
    "PoiRecord",
    "RetryPolicy",
    "RobotsGate",
    "RssCollector",
    "SeedCollector",
    "SnapshotStore",
    "SourceResult",
    "build_collector",
    "describe_sources",
    "extract_links",
    "extract_text",
    "extract_title",
    "make_default_deps",
    "parse_page_specs",
    "parse_poi",
    "parse_rss",
    "parse_seed_records",
    "run_crawl",
    "run_source",
    "strip_html",
    "upsert_pois",
]