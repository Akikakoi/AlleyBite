"""按域名限速（文档 4.3）：单域名 QPS ≤ 1，并叠加随机抖动 0.5–2s。"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import datetime, timezone


class DomainRateLimiter:
    """保证同一 host 相邻两次请求间隔 ≥ 1/qps，且每次额外 sleep 一段随机抖动。"""

    def __init__(
        self,
        *,
        qps: float = 1.0,
        jitter_min: float = 0.5,
        jitter_max: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        rand: Callable[[], float] = random.random,
    ):
        self.qps = qps
        self.jitter_min = jitter_min
        self.jitter_max = jitter_max
        self._sleep = sleep
        self._now = now
        self._rand = rand
        self._next_allowed: dict[str, float] = {}

    @property
    def min_interval(self) -> float:
        return 1.0 / self.qps if self.qps > 0 else 0.0

    def _jitter(self) -> float:
        if self.jitter_max <= self.jitter_min:
            return max(0.0, self.jitter_min)
        return self.jitter_min + (self.jitter_max - self.jitter_min) * self._rand()

    def acquire(self, host: str) -> float:
        """阻塞到可发起下一次请求；返回本次实际 sleep 的秒数（便于测试与观测）。"""
        now_ts = self._now().timestamp()
        jitter = self._jitter()
        earliest = self._next_allowed.get(host, 0.0)
        wait = max(0.0, earliest - now_ts)
        slept = wait + jitter
        self._sleep(slept)
        self._next_allowed[host] = max(now_ts, earliest) + self.min_interval + jitter
        return slept