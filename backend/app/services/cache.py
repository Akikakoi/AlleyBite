"""榜单读取缓存（文档 6.4 第 6 步）。

无 REDIS_URL 或未安装 redis-py 时自动降级为空操作，`get_rank` 直读库内快照。
Redis 不可用时不影响正确性，只是少了加速。
"""

import json

from ..core.config import Settings, get_settings


class RankCache:
    """榜单响应缓存封装。client 可注入（便于测试），未注入时按 url 懒连接。"""

    def __init__(self, url: str = "", ttl: int = 3600, client=None):
        self.ttl = ttl
        self._client = client
        if self._client is None and url:
            try:
                import redis

                candidate = redis.Redis.from_url(url, decode_responses=True)
                candidate.ping()
                self._client = candidate
            except Exception:
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @staticmethod
    def key(
        city: str,
        page: int,
        page_size: int,
        cuisine: str | None,
        price_min: float | None,
        price_max: float | None,
        area: str | None,
        days: int | None = None,
    ) -> str:
        parts = [
            city,
            page,
            page_size,
            cuisine or "",
            price_min or "",
            price_max or "",
            area or "",
            days or "",
        ]
        return "rank:" + ":".join(str(p) for p in parts)

    def get(self, key: str) -> dict | None:
        if self._client is None:
            return None
        try:
            raw = self._client.get(key)
        except Exception:
            return None
        return json.loads(raw) if raw else None

    def set(self, key: str, payload: dict) -> None:
        if self._client is None:
            return
        try:
            self._client.set(key, json.dumps(payload, ensure_ascii=False), ex=self.ttl)
        except Exception:
            pass

    def invalidate_city(self, city: str) -> None:
        if self._client is None:
            return
        try:
            for key in self._client.scan_iter(match=f"rank:{city}:*"):
                self._client.delete(key)
        except Exception:
            pass


def make_rank_cache(settings: Settings | None = None) -> RankCache:
    settings = settings or get_settings()
    return RankCache(url=settings.redis_url, ttl=settings.rank_cache_ttl)