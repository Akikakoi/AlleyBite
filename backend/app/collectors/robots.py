"""robots.txt 合规闸门（文档 4.3 / 11.1）：抓取前必须检查，按 host 缓存。

策略：遵守 robots.txt；401/403 视为全站禁止；404 视为放行；网络异常 **fail-open + 告警**
（宁可少拦也不因目标站临时故障阻塞投放，且失败会留痕告警）。
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .core import HttpFetcher


class RobotsGate:
    def __init__(
        self,
        http: HttpFetcher,
        user_agent: str,
        *,
        alert: Callable[[str, dict], None] | None = None,
        enabled: bool = True,
    ):
        self._http = http
        self._user_agent = user_agent
        self._alert = alert
        self._enabled = enabled
        self._cache: dict[str, RobotFileParser | None] = {}

    def allows(self, url: str) -> bool:
        if not self._enabled:
            return True
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        base = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._parser_for(base)
        if parser is None:  # 网络异常 → fail-open
            if self._alert:
                self._alert("robots_fetch_failed", {"host": parsed.netloc})
            return True
        return parser.can_fetch(self._user_agent, url)

    def _parser_for(self, base: str) -> RobotFileParser | None:
        if base in self._cache:
            return self._cache[base]

        parser: RobotFileParser | None
        result = self._http.get(f"{base}/robots.txt")
        if result.status_code == 200:
            parser = RobotFileParser()
            parser.parse(result.text.splitlines())
        elif result.status_code in (401, 403):
            parser = RobotFileParser()
            parser.disallow_all = True  # 无权访问 → 全站禁止
        elif result.status_code >= 400:
            parser = RobotFileParser()
            parser.allow_all = True  # 无 robots.txt → 放行
        else:
            parser = None  # 网络异常/无法判定 → fail-open

        self._cache[base] = parser
        return parser