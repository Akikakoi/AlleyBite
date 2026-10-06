import fnmatch
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import RankSnapshot
from app.services.alignment import align_mentions
from app.services.cache import RankCache
from app.services.extract_pipeline import extract_raw_content
from app.services.ingest import ingest_raw_content
from app.services.rank_service import build_rank_snapshot, get_rank

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)

    def scan_iter(self, match=None):
        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


def make_settings(**overrides) -> Settings:
    base = dict(llm_mock=True, llm_api_key="")
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


def test_cache_disabled_without_url():
    cache = RankCache(url="")
    assert cache.enabled is False
    cache.set("k", {"a": 1})  # 空操作不报错
    assert cache.get("k") is None
    cache.invalidate_city("成都")


def test_cache_key_shape():
    key = RankCache.key("成都", 1, 20, "川菜", 20, 100, "青羊区")
    assert key == "rank:成都:1:20:川菜:20:100:青羊区"
    bare = RankCache.key("成都", 2, 10, None, None, None, None)
    assert bare == "rank:成都:2:10::::"


def test_cache_roundtrip_and_invalidate():
    cache = RankCache(url="", ttl=60, client=FakeRedis())
    assert cache.enabled is True

    cache.set("rank:成都:1", {"total": 2})
    cache.set("rank:重庆:1", {"total": 5})
    assert cache.get("rank:成都:1") == {"total": 2}

    cache.invalidate_city("成都")
    assert cache.get("rank:成都:1") is None
    assert cache.get("rank:重庆:1") == {"total": 5}  # 其他城市不受影响


def _seed_and_build(session, settings):
    for text in (
        "明婷饭店，藏在青羊区同心路的巷子里，脑花豆腐太香，人均 65。",
        "王妈手撕烤兔，开在玉林，兔头麻辣入味。",
    ):
        row = ingest_raw_content(
            session, source="forum", raw_text=text, city_hint="成都", settings=settings
        )
        session.commit()
        extract_raw_content(session, row.id, settings=settings)
        session.commit()
    align_mentions(session, settings=settings)
    session.commit()
    build_rank_snapshot(session, "成都", settings=settings, now=NOW)
    session.commit()


def test_get_rank_serves_from_cache(session):
    settings = make_settings()
    _seed_and_build(session, settings)
    cache = RankCache(url="", client=FakeRedis())

    first = get_rank(session, "成都", cache=cache)
    assert first["total"] == 2

    # 清掉库内快照：若仍能返回，说明命中了缓存
    session.execute(delete(RankSnapshot))
    session.commit()

    second = get_rank(session, "成都", cache=cache)
    assert second == first


def test_build_invalidates_city_cache(session):
    settings = make_settings()
    _seed_and_build(session, settings)
    cache = RankCache(url="", client=FakeRedis())

    get_rank(session, "成都", cache=cache)  # 写入缓存
    assert cache.get(RankCache.key("成都", 1, 20, None, None, None, None)) is not None

    build_rank_snapshot(session, "成都", settings=settings, now=NOW, cache=cache)
    session.commit()
    assert cache.get(RankCache.key("成都", 1, 20, None, None, None, None)) is None