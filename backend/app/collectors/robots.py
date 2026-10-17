"""robots.txt 合规闸门（文档 4.3 / 11.1）：抓取前必须检查，按 host 缓存（带 TTL）。

策略：遵守 robots.txt；401/403 视为全站禁止；404 视为放行；网络异常/5xx **fail-open + 告警**
（宁可少拦也不因目标站临时故障阻塞投放，且失败会留痕告警）。
缓存按 host 保留 ROBOTS_TTL_SECONDS（默认 24h），过期重新拉取，避免长驻进程
永远用旧规则（站点改了 robots.txt 无人知晓）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .core import HttpFetcher

ROBOTS_TTL_SECONDS = 24 * 3600.0


class RobotsGate:
    def __init__(
        self,
        http: HttpFetcher,
        user_agent: str,
        *,
        alert: Callable[[str, dict], None] | None = None,
        enabled: bool = True,
        ttl_seconds: float = ROBOTS_TTL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ):
        self._http = http
        self._user_agent = user_agent
        self._alert = alert
        self._enabled = enabled
        self._ttl_seconds = ttl_seconds
        self._now = now
        self._cache: dict[str, tuple[RobotFileParser | None, float]] = {}

    def allows(self, url: str) -> bool:
        if not self._enabled:
            return True
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        base = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._parser_for(base)
        if parser is None:  # 网络异常/5xx → fail-open
            if self._alert:
                self._alert("robots_fetch_failed", {"host": parsed.netloc})
            return True
        return parser.can_fetch(self._user_agent, url)

    def _parser_for(self, base: str) -> RobotFileParser | None:
        cached = self._cache.get(base)
        if cached is not None:
            parser, fetched_at = cached
            if self._now() - fetched_at < self._ttl_seconds:
                return parser

        parser: RobotFileParser | None
        result = self._http.get(f"{base}/robots.txt")
        if result.status_code == 200:
            parser = RobotFileParser()
            parser.parse(result.text.splitlines())
        elif result.status_code in (401, 403):
            parser = RobotFileParser()
            parser.disallow_all = True  # 无权访问 → 全站禁止
        elif 400 <= result.status_code < 500:
            parser = RobotFileParser()
            parser.allow_all = True  # 404=无 robots.txt；其余 4xx 按放行（宁可少拦）
        else:
            parser = None  # 5xx/网络异常/无法判定 → fail-open + 告警（不缓存，下轮重试）

        if parser is not None:
            self._cache[base] = (parser, self._now())
        else:
            # 失败结果不写入缓存：下一轮采集立即重试，避免一次故障顶旿24小时
            self._cache.pop(base, None)
        return parser