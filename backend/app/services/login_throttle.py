"""登录失败限速（进程内）：同 用户名+IP 短窗口内连续失败即临时锁定。

防在线爆破的减速带：uvicorn 单进程部署，内存态即生效；重启清零属可接受代价。
两端口令登录（管理后台 / C 端）共用同一实现，scope 区分命名空间。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class LoginThrottled(Exception):
    """触发登录限速；args[0] 为建议等待秒数。"""


class LoginThrottle:
    """滑动窗口失败计数 + 超限临时锁定。线程安全（FastAPI 同步端点跑线程池）。"""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: float = 900.0,
        lockout_seconds: float = 900.0,
        now: Callable[[], float] = time.monotonic,
    ):
        self.max_failures = max(1, max_failures)
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._now = now
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}

    @staticmethod
    def key(scope: str, username: str, ip: str | None) -> str:
        return f"{scope}:{(username or '').strip().lower()}:{ip or '-'}"

    def _prune(self, now: float) -> None:
        for key, dq in list(self._failures.items()):
            while dq and now - dq[0] > self.window_seconds:
                dq.popleft()
            if not dq and self._locked_until.get(key, 0.0) <= now:
                self._failures.pop(key, None)
        for key, until in list(self._locked_until.items()):
            if until <= now:
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)

    def check(self, key: str) -> None:
        """锁定期内抛 LoginThrottled（剩余秒数）。"""
        now = self._now()
        with self._lock:
            self._prune(now)
            until = self._locked_until.get(key)
            if until is not None and until > now:
                raise LoginThrottled(int(until - now) + 1)

    def record_failure(self, key: str) -> None:
        """记一次失败；窗口内失败达 max_failures 即进入锁定。"""
        now = self._now()
        with self._lock:
            self._prune(now)
            failures = self._failures[key]
            failures.append(now)
            if len(failures) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_seconds
                self._failures.pop(key, None)

    def reset(self, key: str) -> None:
        """登录成功后清除计数。"""
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)
