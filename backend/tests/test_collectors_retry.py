"""RetryPolicy 退避策略 + BaseCollector.fetch_url 的重试/计数/快照行为。"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.collectors.base import BaseCollector
from app.collectors.core import RetryPolicy
from app.collectors.snapshots import SnapshotStore
from collectors_fakes import make_client, make_deps, make_settings


class _Dummy(BaseCollector):
    source = "dummy"

    def fetch(self, since=None):
        return []


def _collector(handler, *, settings=None, alerts=None, snapshot=None, respect_robots=True):
    settings = settings or make_settings(crawl_retry_max=3)
    deps = make_deps(make_client(handler), alerts=alerts, snapshot=snapshot)
    return _Dummy(settings=settings, deps=deps, respect_robots=respect_robots)


# --- RetryPolicy 纯逻辑 -----------------------------------------------------

def test_4xx_not_retried_except_429():
    policy = RetryPolicy(retry_max=3)
    assert policy.decide(404, 1) is None
    assert policy.decide(403, 1) is None
    assert policy.decide(400, 2) is None
    assert policy.decide(429, 1) == 1.0


def test_5xx_and_network_use_exponential_backoff():
    policy = RetryPolicy(retry_max=3, base_delay=1.0)
    assert policy.decide(500, 1) == 1.0
    assert policy.decide(500, 2) == 2.0
    assert policy.decide(500, 3) is None  # 达到尝试上限
    assert policy.decide(0, 1) == 1.0  # 网络异常


def test_success_never_retried():
    assert RetryPolicy().decide(200, 1) is None


def test_retry_max_floor_is_one():
    assert RetryPolicy(retry_max=0).retry_max == 1


# --- fetch_url 行为 ---------------------------------------------------------

def test_retries_transient_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="<html>ok</html>")

    collector = _collector(handler)
    result = collector.fetch_url("http://site.test/a", respect_robots=False)
    assert result.ok and result.status_code == 200
    assert calls["n"] == 3
    assert collector.counters["failed"] == 0


def test_retries_exhausted_marks_failed():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, text="down")

    collector = _collector(handler, settings=make_settings(crawl_retry_max=3))
    result = collector.fetch_url("http://site.test/x", respect_robots=False)
    assert not result.ok
    assert calls["n"] == 3  # 最多 retry_max 次尝试
    assert collector.counters["failed"] == 1


def test_client_error_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, text="nope")

    collector = _collector(handler)
    result = collector.fetch_url("http://site.test/missing", respect_robots=False)
    assert not result.ok
    assert calls["n"] == 1
    assert collector.counters["failed"] == 1


def test_403_and_429_counted():
    collector = _collector(lambda request: httpx.Response(403, text=""))
    collector.fetch_url("http://site.test/x", respect_robots=False)
    assert collector.counters["http_403"] == 1

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow")
        return httpx.Response(200, text="ok")

    collector2 = _collector(handler)
    result = collector2.fetch_url("http://site.test/y", respect_robots=False)
    assert result.ok
    assert collector2.counters["http_429"] == 1
    assert calls["n"] == 2


def test_successful_fetch_saves_snapshot(tmp_path):
    handler = lambda request: httpx.Response(200, text="<html>hi</html>")  # noqa: E731
    collector = _collector(handler, snapshot=SnapshotStore(tmp_path, 90))
    result = collector.fetch_url("http://site.test/a", respect_robots=False)
    assert result.raw_ref is not None
    assert Path(result.raw_ref).exists()


def test_snapshot_path_layout_and_ttl_purge(tmp_path):
    store = SnapshotStore(
        tmp_path, ttl_days=90, now=lambda: datetime(2026, 6, 1, tzinfo=timezone.utc)
    )
    ref = Path(store.save("rss", "http://site.test/f", "<feed/>", "xml"))

    assert ref.parent.name == "20260601"  # <root>/<source>/<YYYYMMDD>/
    assert ref.parent.parent.name == "rss"

    old = time.time() - 100 * 86400
    os.utime(ref, (old, old))
    assert store.purge_expired() == 1
    assert not ref.exists()


def test_robots_disallow_blocks_fetch_before_request():
    def handler(request):
        if str(request.url).endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="should-not-happen")

    alerts: list[tuple[str, dict]] = []
    collector = _collector(handler, alerts=alerts)  # respect_robots=True
    result = collector.fetch_url("http://site.test/secret")
    assert result.status_code == -1
    assert result.error == "robots_disallow"
    assert collector.counters["failed"] == 1
    assert "robots_disallow" in [event for event, _ in alerts]