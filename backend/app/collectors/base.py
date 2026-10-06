"""采集器基类：把 限速 → robots → 重试 → 快照 收敛到一处（文档 4.3）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from urllib.parse import urlparse

from ..core.config import Settings
from .core import CollectorDeps, CrawlItem, FetchResult, RetryPolicy
from .ratelimit import DomainRateLimiter
from .robots import RobotsGate


class BaseCollector(ABC):
    """所有采集器的父类。

    - ``source``：落到 raw_content.source 的标识
    - ``kind``：text（走 ingest 抽取链路）/ poi（直写 restaurant 实体基准）
    """

    source: str = "base"
    kind: str = "text"

    def __init__(
        self,
        *,
        settings: Settings,
        deps: CollectorDeps,
        city_hint: str | None = None,
        respect_robots: bool = True,
    ):
        self.settings = settings
        self.deps = deps
        self.city_hint = city_hint
        self._limiter = DomainRateLimiter(
            qps=settings.crawl_qps_per_domain,
            jitter_min=settings.crawl_jitter_min,
            jitter_max=settings.crawl_jitter_max,
            sleep=deps.sleep,
            now=deps.now,
            rand=deps.rand,
        )
        self._robots = RobotsGate(
            deps.http,
            settings.build_user_agent,
            alert=deps.alert,
            enabled=respect_robots,
        )
        self._retry = RetryPolicy(settings.crawl_retry_max)
        # requests：实际发起的 HTTP 次数（含重试），用于地图 API 配额监控（文档 10.4）
        self.counters = {"requests": 0, "http_403": 0, "http_429": 0, "failed": 0}

    @property
    def is_ready(self) -> bool:
        """配置是否就绪（如高德无 key）——未就绪时 runner 记 skipped。"""
        return True

    @abstractmethod
    def fetch(self, since: datetime | None = None) -> list[CrawlItem]:
        """拉取一批待入库条目；``since`` 为增量游标（发布时间水位）。"""

    def fetch_url(
        self,
        url: str,
        *,
        ext: str = "html",
        respect_robots: bool = True,
    ) -> FetchResult:
        """限速 → robots → 带退避重试地取回单个 URL，并留存快照。"""
        host = urlparse(url).netloc
        self._limiter.acquire(host)

        if respect_robots and not self._robots.allows(url):
            self.deps.alert("robots_disallow", {"source": self.source, "url": url})
            self.counters["failed"] += 1
            return FetchResult(url=url, status_code=-1, error="robots_disallow")

        attempt = 0
        result = FetchResult(url=url, status_code=0, error="no-attempt")
        while True:
            attempt += 1
            result = self.deps.http.get(url)
            self.counters["requests"] += 1
            if result.ok:
                self._save_snapshot(result, ext)
                return result

            self._count_status(result.status_code)
            delay = self._retry.decide(result.status_code, attempt)
            if delay is None:
                break
            self.deps.sleep(delay)

        self.counters["failed"] += 1
        return result

    def _count_status(self, status_code: int) -> None:
        if status_code == 403:
            self.counters["http_403"] += 1
        elif status_code == 429:
            self.counters["http_429"] += 1

    def _save_snapshot(self, result: FetchResult, ext: str) -> None:
        if self.deps.snapshot is None or not result.text:
            return
        result.raw_ref = self.deps.snapshot.save(
            self.source, result.url, result.text, ext
        )

    @property
    def max_items(self) -> int:
        return self.settings.crawl_max_items_per_source