"""熔断（文档 4.4）：403/429 达阈值 → 记入 job_run，下一轮跳过该源。"""

import httpx
import pytest

from app.collectors.runner import run_crawl
from collectors_fakes import make_client, make_deps, make_settings, memory_session


@pytest.fixture
def session():
    with memory_session() as s:
        yield s


def _rss_settings():
    return make_settings(
        crawl_rss_urls="http://feed.test/rss.xml",
        crawl_breaker_fail_threshold=1,
        crawl_breaker_cooldown_minutes=60,
    )


def _forbidden_handler(counter: dict | None = None):
    """robots.txt 放行（404），feed 返回 403 → 触发 http_403 计数。"""

    def handler(request):
        if counter is not None:
            counter["n"] = counter.get("n", 0) + 1
        if str(request.url).endswith("/robots.txt"):
            return httpx.Response(404, text="")
        return httpx.Response(403, text="")

    return handler


def test_breaker_trips_after_threshold(session):
    alerts: list[tuple[str, dict]] = []
    settings = _rss_settings()
    deps = make_deps(make_client(_forbidden_handler()), alerts=alerts)

    job = run_crawl(session, settings, sources=["rss"], deps=deps)

    stats = job.stats["sources"]["rss"]
    assert stats["http_403"] == 1
    assert stats["tripped_until"] is not None
    assert stats["detail"]["breaker"] == "tripped"
    assert "rss" in job.stats["breaker"]["tripped"]
    assert "breaker_trip" in [event for event, _ in alerts]


def test_breaker_skips_next_run(session):
    settings = _rss_settings()
    counter: dict = {}
    handler = _forbidden_handler(counter)

    deps = make_deps(make_client(handler))
    run_crawl(session, settings, sources=["rss"], deps=deps)
    first_requests = counter["n"]

    alerts2: list[tuple[str, dict]] = []
    deps2 = make_deps(make_client(handler), alerts=alerts2)
    job2 = run_crawl(session, settings, sources=["rss"], deps=deps2)

    stats2 = job2.stats["sources"]["rss"]
    assert stats2["status"] == "breaker_open"
    assert "rss" in job2.stats["breaker"]["tripped"]
    assert "breaker_open" in [event for event, _ in alerts2]
    assert counter["n"] == first_requests  # 熔断期内不再发起请求


def test_below_threshold_does_not_trip(session):
    settings = make_settings(
        crawl_rss_urls="http://feed.test/rss.xml",
        crawl_breaker_fail_threshold=5,
    )
    deps = make_deps(make_client(_forbidden_handler()))
    job = run_crawl(session, settings, sources=["rss"], deps=deps)

    stats = job.stats["sources"]["rss"]
    assert stats["http_403"] == 1
    assert stats["tripped_until"] is None
    assert job.stats["breaker"]["tripped"] == []