"""DomainRateLimiter：单域名 QPS ≤ 1 + 抖动（零等待）。"""

from datetime import datetime, timezone

from app.collectors.ratelimit import DomainRateLimiter

FIXED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _limiter(slept: list[float], *, rand: float = 0.0, **kwargs) -> DomainRateLimiter:
    return DomainRateLimiter(
        sleep=slept.append,
        now=lambda: FIXED,
        rand=lambda: rand,
        **kwargs,
    )


def test_first_request_only_sleeps_jitter():
    slept: list[float] = []
    limiter = _limiter(slept, jitter_min=0.5, jitter_max=2.0)
    assert limiter.acquire("a.com") == 0.5


def test_same_host_enforces_min_interval():
    slept: list[float] = []
    limiter = _limiter(slept, qps=1.0, jitter_min=0.5, jitter_max=2.0)
    limiter.acquire("a.com")
    # 下一可用时间 = now + 1.0 + 抖动(0.5)；本次 wait=1.5，再加抖动 0.5
    second = limiter.acquire("a.com")
    assert second == 2.0
    assert second - 0.5 >= limiter.min_interval


def test_different_hosts_are_independent():
    slept: list[float] = []
    limiter = _limiter(slept)
    limiter.acquire("a.com")
    assert limiter.acquire("b.com") == 0.5


def test_jitter_bounds_follow_rand():
    assert _limiter([], rand=1.0).acquire("a.com") == 2.0  # 抖动上限
    assert _limiter([], rand=0.0).acquire("a.com") == 0.5  # 抖动下限


def test_jitter_min_equals_max_is_constant():
    assert _limiter([], rand=0.9, jitter_min=1.0, jitter_max=1.0).acquire("a.com") == 1.0


def test_min_interval_derived_from_qps():
    assert DomainRateLimiter(qps=2.0).min_interval == 0.5
    assert DomainRateLimiter(qps=1.0).min_interval == 1.0
    assert DomainRateLimiter(qps=0.0).min_interval == 0.0