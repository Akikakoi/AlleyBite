"""采集层单测共享的假依赖：全程零联网、零等待。

文件名不以 ``test_`` 开头，pytest 不会把它当作用例收集。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.collectors.core import CollectorDeps, HttpFetcher
from app.core.config import Settings
from app.db.base import Base

FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_settings(**overrides) -> Settings:
    """与 test_ingest 同款：禁用 .env，默认离线。"""
    base = dict(
        llm_mock=True,
        llm_api_key="",
        extract_max_tokens=64,
        chunk_overlap_tokens=0,
        min_chinese_ratio=0.5,
        crawl_night_pause=False,  # 单测避免依赖本机时区
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """把 handler 包成不发真实请求的 httpx.Client。"""
    return httpx.Client(transport=httpx.MockTransport(handler))


def map_handler(
    mapping: dict[str, object], *, default_status: int = 404
) -> Callable[[httpx.Request], httpx.Response]:
    """按 URL 精确匹配构造 handler；未命中的 URL 返回 default_status。

    值可以是 ``(status, text)``、``httpx.Response`` 或可调用对象。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        value = mapping.get(str(request.url))
        if value is None:
            return httpx.Response(default_status, text="")
        if callable(value):
            return value(request)  # type: ignore[return-value]
        if isinstance(value, httpx.Response):
            return value
        status, text = value  # type: ignore[misc]
        return httpx.Response(status, text=text, headers={"content-type": "text/html; charset=utf-8"})

    return handler


def make_deps(
    client: httpx.Client,
    *,
    snapshot=None,
    now: Callable[[], datetime] | None = None,
    rand: Callable[[], float] = lambda: 0.0,
    alerts: list[tuple[str, dict]] | None = None,
) -> CollectorDeps:
    """组装可注入依赖；sleep 直接吞掉，alert 收集到 alerts。"""
    if alerts is None:
        alert_fn: Callable[[str, dict], None] = lambda *_: None
    else:
        alert_fn = lambda event, payload: alerts.append((event, payload))  # noqa: E731

    return CollectorDeps(
        http=HttpFetcher(user_agent="AlleyBiteBot/test", client=client),
        snapshot=snapshot,
        sleep=lambda _seconds: None,
        now=now or (lambda: FIXED_NOW),
        rand=rand,
        alert=alert_fn,
    )


@contextmanager
def memory_session():
    """一次性内存 SQLite 会话（与既有测试同构）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session