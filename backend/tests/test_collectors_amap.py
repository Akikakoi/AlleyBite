"""高德 POI：解析、无 key 跳过、分页检索、直写 restaurant 且幂等。"""

import httpx
import pytest
from sqlalchemy import func, select

from app.collectors.amap import POI_SOURCE, AmapPoiCollector, PoiRecord, parse_poi, upsert_pois
from app.collectors.runner import run_crawl, run_source
from app.db.models import Restaurant
from app.services.alignment import get_or_create_city
from app.services.normalize import normalize_shop_name
from collectors_fakes import make_client, make_deps, make_settings, memory_session

POI_JSON = {
    "status": "1",
    "pois": [
        {
            "id": "B001",
            "name": "陈麻婆豆腐",
            "address": "西玉龙街197号",
            "adname": "青羊区",
            "location": "104.06,30.67",
            "typecode": "050100",
        }
    ],
}


@pytest.fixture
def session():
    with memory_session() as s:
        yield s


# --- 解析 -------------------------------------------------------------------

def test_parse_poi_extracts_fields():
    record = parse_poi(POI_JSON["pois"][0])
    assert record is not None
    assert record.poi_id == "B001"
    assert record.longitude == 104.06
    assert record.latitude == 30.67
    assert record.area == "青羊区"


def test_parse_poi_rejects_incomplete_rows():
    assert parse_poi({"name": "无名"}) is None
    assert parse_poi({"id": "x"}) is None


# --- 采集器就绪与检索 -------------------------------------------------------

def test_not_ready_without_key():
    collector = AmapPoiCollector(
        settings=make_settings(),
        deps=make_deps(make_client(lambda r: httpx.Response(200, text=""))),
        city_hint="成都",
    )
    assert collector.is_ready is False
    assert collector.search() == []


def test_search_paginates_and_stops_on_short_page():
    def handler(request):
        page = int(request.url.params.get("page", "1"))
        pois = [
            {"id": f"P{page}-1", "name": f"店{page}-1", "location": "104.0,30.0"},
            {"id": f"P{page}-2", "name": f"店{page}-2", "location": "104.1,30.1"},
        ]
        if page == 2:
            pois = pois[:1]  # 不足一页 → 停止翻页
        return httpx.Response(200, json={"status": "1", "pois": pois})

    settings = make_settings(amap_api_key="test-key", amap_page_size=2, amap_max_pages=3)
    collector = AmapPoiCollector(
        settings=settings, deps=make_deps(make_client(handler)), city_hint="成都"
    )
    records = collector.search()

    assert [r.poi_id for r in records] == ["P1-1", "P1-2", "P2-1"]


def test_search_reports_api_error():
    alerts: list[tuple[str, dict]] = []
    handler = lambda request: httpx.Response(200, json={"status": "0", "info": "INVALID_USER_KEY"})  # noqa: E731
    settings = make_settings(amap_api_key="bad")
    collector = AmapPoiCollector(
        settings=settings, deps=make_deps(make_client(handler), alerts=alerts), city_hint="成都"
    )
    assert collector.search() == []
    assert "amap_error" in [event for event, _ in alerts]


# --- upsert 直写 ------------------------------------------------------------

def test_upsert_pois_idempotent(session):
    records = [
        PoiRecord(
            poi_id="B001",
            name="陈麻婆豆腐",
            address="西玉龙街197号",
            area="青羊区",
            latitude=30.67,
            longitude=104.06,
        )
    ]
    assert upsert_pois(session, "成都", records) == (1, 0)
    session.commit()

    assert upsert_pois(session, "成都", records) == (0, 1)
    session.commit()

    assert session.scalar(select(func.count()).select_from(Restaurant)) == 1
    row = session.scalar(select(Restaurant))
    assert row.poi_source == POI_SOURCE
    assert row.poi_id == "B001"
    assert row.latitude == 30.67
    assert row.name_norm == "陈麻婆豆腐"


def test_upsert_matches_existing_by_name_norm(session):
    city = get_or_create_city(session, "成都")
    existing = Restaurant(
        city_id=city.id,
        name="陈麻婆豆腐(总店)",
        name_norm=normalize_shop_name("陈麻婆豆腐(总店)"),
    )
    session.add(existing)
    session.flush()

    created, matched = upsert_pois(
        session, "成都", [PoiRecord(poi_id="B9", name="陈麻婆豆腐", address="青羊区")]
    )
    session.commit()

    assert (created, matched) == (0, 1)
    assert existing.poi_id == "B9"
    assert existing.address == "青羊区"
    assert existing.name_norm == normalize_shop_name("陈麻婆豆腐(总店)")  # 原值不被覆盖


# --- 编排集成 ---------------------------------------------------------------

def test_poi_source_without_city_is_skipped(session):
    settings = make_settings(amap_api_key="test-key")
    collector = AmapPoiCollector(
        settings=settings,
        deps=make_deps(make_client(lambda r: httpx.Response(200, text=""))),
        city_hint=None,
    )
    result = run_source(session, collector, settings=settings)
    assert result.status == "skipped"
    assert "city_hint" in result.error


def test_run_crawl_skips_amap_without_key(session):
    settings = make_settings()
    deps = make_deps(make_client(lambda r: httpx.Response(200, text="")))
    job = run_crawl(session, settings, sources=["amap"], deps=deps, city_hint="成都")
    assert job.stats["sources"]["amap"]["status"] == "skipped"


def test_run_crawl_amap_writes_poi_restaurants(session):
    handler = lambda request: httpx.Response(200, json=POI_JSON)  # noqa: E731
    settings = make_settings(amap_api_key="test-key", amap_page_size=20)
    deps = make_deps(make_client(handler))

    job = run_crawl(session, settings, sources=["amap"], deps=deps, city_hint="成都")

    stats = job.stats["sources"]["amap"]
    assert stats["status"] == "ok"
    assert stats["poi_written"] == 1

    row = session.scalar(select(Restaurant))
    assert row is not None
    assert row.poi_source == POI_SOURCE
    assert row.name == "陈麻婆豆腐"
    assert row.latitude == 30.67

# --- 按城市覆盖关键词（文档 4.1 城市扩张）-----------------------------------


def test_city_keywords_map_parsing():
    settings = make_settings(
        amap_keywords_by_city="福州=佛跳墙,肉燕,鱼丸;厦门=沙茶面,海蛎煎\n泉州=面线糊"
    )
    assert settings.amap_city_keywords_map == {
        "福州": ["佛跳墙", "肉燕", "鱼丸"],
        "厦门": ["沙茶面", "海蛎煎"],
        "泉州": ["面线糊"],
    }
    # 空/非法条目跳过
    settings = make_settings(amap_keywords_by_city="福州=;=无城,词;厦门=")
    assert settings.amap_city_keywords_map == {}


def test_collector_prefers_city_keywords_over_global():
    settings = make_settings(
        amap_api_key="test-key",
        amap_keywords="美食,川菜",
        amap_keywords_by_city="福州=佛跳墙,肉燕",
    )
    deps = make_deps(make_client(lambda r: httpx.Response(200, text="")))

    fuzhou = AmapPoiCollector(settings=settings, deps=deps, city_hint="福州")
    assert fuzhou.keywords == ["佛跳墙", "肉燕"]

    chengdu = AmapPoiCollector(settings=settings, deps=deps, city_hint="成都")
    assert chengdu.keywords == ["美食", "川菜"]

    # 显式传参优先级最高（run_crawl.py --keywords 通道）
    explicit = AmapPoiCollector(
        settings=settings, deps=deps, city_hint="福州", keywords="小吃"
    )
    assert explicit.keywords == ["小吃"]

    no_city = AmapPoiCollector(settings=settings, deps=deps, city_hint=None)
    assert no_city.keywords == ["美食", "川菜"]
