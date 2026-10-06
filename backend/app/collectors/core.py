"""采集层基础类型与可注入依赖（开发文档第 4 章）。

设计要点：
- 所有外部交互（HTTP / sleep / 时钟 / 随机数 / 快照 / 告警）都通过 ``CollectorDeps``
  注入，测试时替换即可做到零联网、零等待。
- ``HttpFetcher`` 是全层唯一发请求的出口；采集器不得直接使用 httpx。
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from ..core.config import Settings
from .snapshots import SnapshotStore

logger = logging.getLogger("alleybite.collectors")


@dataclass
class CrawlItem:
    """一条待入库的采集结果（文本类源）。"""

    source: str
    raw_text: str
    source_url: str | None = None
    raw_title: str | None = None
    city_hint: str | None = None
    published_at: datetime | None = None
    raw_ref: str | None = None  # 原始页面快照引用


@dataclass
class FetchResult:
    """单次 HTTP 取回结果；status_code=0 表示网络层异常。"""

    url: str
    status_code: int
    text: str = ""
    content_type: str = ""
    raw_ref: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass
class SourceResult:
    """单个数据源的采集统计，最终写入 job_run.stats。"""

    source: str
    status: str = "ok"  # ok | skipped | failed | breaker_open
    fetched: int = 0
    new: int = 0
    duplicated: int = 0
    failed: int = 0
    poi_written: int = 0
    http_403: int = 0
    http_429: int = 0
    cursor_at: datetime | None = None
    tripped_until: datetime | None = None
    error: str | None = None
    detail: dict = field(default_factory=dict)

    def to_stats(self) -> dict:
        return {
            "status": self.status,
            "fetched": self.fetched,
            "new": self.new,
            "duplicated": self.duplicated,
            "failed": self.failed,
            "poi_written": self.poi_written,
            "http_403": self.http_403,
            "http_429": self.http_429,
            "cursor_at": self.cursor_at.isoformat() if self.cursor_at else None,
            "tripped_until": self.tripped_until.isoformat() if self.tripped_until else None,
            "error": self.error,
            "detail": self.detail,
        }


class HttpFetcher:
    """httpx 薄封装：统一 User-Agent / 超时，且不因 4xx/5xx 抛异常。

    测试可传入 ``client=httpx.Client(transport=httpx.MockTransport(...))``。
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    def get(self, url: str) -> FetchResult:
        try:
            resp = self.client.get(url)
        except httpx.HTTPError as exc:
            return FetchResult(url=url, status_code=0, text="", error=str(exc))
        return FetchResult(
            url=str(resp.url),
            status_code=resp.status_code,
            text=resp.text,
            content_type=resp.headers.get("content-type", ""),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


class RetryPolicy:
    """失败重试决策（文档 4.3）：指数退避，最多 retry_max 次尝试。

    - 4xx 不重试（429 除外）
    - 429 / 5xx / 网络异常（status_code=0）→ 指数退避
    """

    def __init__(self, retry_max: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
        self.retry_max = max(1, retry_max)
        self.base_delay = base_delay
        self.max_delay = max_delay

    def decide(self, status_code: int, attempt: int) -> float | None:
        """返回下次重试前的等待秒数；None 表示不再重试。attempt 为已尝试次数。"""
        if attempt >= self.retry_max:
            return None
        if 400 <= status_code < 500 and status_code != 429:
            return None
        if status_code == 429 or status_code == 0 or status_code >= 500:
            return min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        return None


def _default_alert(event: str, payload: dict) -> None:
    logger.warning("采集告警 %s: %s", event, payload)


@dataclass
class CollectorDeps:
    """采集器可注入依赖集合。"""

    http: HttpFetcher
    snapshot: SnapshotStore | None = None
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    rand: Callable[[], float] = random.random
    alert: Callable[[str, dict], None] = _default_alert

    def local_hour(self) -> int:
        return self.now().astimezone().hour


def make_default_deps(settings: Settings, *, client: httpx.Client | None = None) -> CollectorDeps:
    """按配置组装默认真实依赖（脚本/生产使用）。"""
    return CollectorDeps(
        http=HttpFetcher(
            user_agent=settings.build_user_agent,
            timeout=settings.crawl_timeout,
            client=client,
        ),
        snapshot=SnapshotStore(settings.snapshot_dir, settings.crawl_snapshot_ttl_days),
    )