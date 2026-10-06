"""高德地图 POI 采集（文档 4.1 的"实体基准"，推荐且稳定合规）。

与文本类源不同：POI 是权威结构化实体，**不走 LLM 抽取**，直接 upsert 到 restaurant
表作为实体底座；POI-only 店铺没有 mention，因此不会进入榜单，只等社媒口碑挂靠。
无 AMAP_API_KEY 时采集器整体跳过。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..services.metrics import record_amap_call
from .base import BaseCollector
from .core import CollectorDeps, CrawlItem

POI_SOURCE = "amap"


@dataclass
class PoiRecord:
    poi_id: str
    name: str
    address: str | None = None
    area: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    typecode: str | None = None


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def parse_poi(raw: dict) -> PoiRecord | None:
    """高德 poi 对象 → PoiRecord；缺 id/name 视为无效。"""
    poi_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not poi_id or not name:
        return None
    longitude = latitude = None
    location = str(raw.get("location") or "")
    if "," in location:
        lng_str, _, lat_str = location.partition(",")
        longitude, latitude = _to_float(lng_str), _to_float(lat_str)
    return PoiRecord(
        poi_id=poi_id,
        name=name,
        address=(raw.get("address") or None),
        area=(raw.get("adname") or None),
        latitude=latitude,
        longitude=longitude,
        typecode=(raw.get("typecode") or None),
    )


class AmapPoiCollector(BaseCollector):
    source = POI_SOURCE
    kind = "poi"

    def __init__(
        self,
        *,
        settings,
        deps: CollectorDeps,
        city_hint: str | None = None,
        keywords: str | None = None,
    ):
        # 官方开放 API，不走 robots 闸门（robots.txt 面向页面抓取）
        super().__init__(
            settings=settings, deps=deps, city_hint=city_hint, respect_robots=False
        )
        raw = keywords if keywords is not None else settings.amap_keywords
        self.keywords = [k.strip() for k in (raw or "美食").split(",") if k.strip()]

    @property
    def is_ready(self) -> bool:
        return self.settings.has_amap and bool(self.city_hint)

    def fetch(self, since: datetime | None = None) -> list[CrawlItem]:
        """POI 走直写旁路，不产生文本入库条目。"""
        return []

    def search(self) -> list[PoiRecord]:
        if not self.is_ready:
            return []
        records: list[PoiRecord] = []
        seen: set[str] = set()
        page_size = self.settings.amap_page_size
        for keyword in self.keywords:
            if len(records) >= self.max_items:
                break
            for page in range(1, self.settings.amap_max_pages + 1):
                params = {
                    "key": self.settings.amap_api_key,
                    "keywords": keyword,
                    "city": self.city_hint,
                    "citylimit": "true",
                    "offset": page_size,
                    "page": page,
                    "extensions": "base",
                }
                url = f"{self.settings.amap_base_url}?{urlencode(params)}"
                result = self.fetch_url(url, ext="json", respect_robots=False)
                record_amap_call(ok=result.ok)
                if not result.ok:
                    break
                try:
                    data = json.loads(result.text)
                except json.JSONDecodeError:
                    self.deps.alert("amap_parse_error", {"source": self.source})
                    break

                if str(data.get("status")) != "1":
                    self.deps.alert(
                        "amap_error",
                        {"info": data.get("info"), "infocode": data.get("infocode")},
                    )
                    break

                pois = data.get("pois") or []
                for raw in pois:
                    record = parse_poi(raw)
                    if record is not None and record.poi_id not in seen:
                        seen.add(record.poi_id)
                        records.append(record)
                if len(pois) < page_size:
                    break

        return records[: self.max_items]


def upsert_pois(
    session: Session,
    city_name: str,
    records: list[PoiRecord],
) -> tuple[int, int]:
    """把 POI 写入 restaurant（代码级 upsert，无唯一约束约束下的先查后插）。

    匹配顺序：同城 (poi_source, poi_id) → 同城 name_norm 兜底。
    返回 (新建数, 命中/更新数)。
    """
    from ..db.models import Restaurant, ShopAlias
    from ..services.alignment import get_or_create_city
    from ..services.normalize import normalize_shop_name

    city = get_or_create_city(session, city_name)
    created = 0
    matched = 0

    for record in records:
        norm = normalize_shop_name(record.name)
        restaurant = session.scalar(
            select(Restaurant).where(
                Restaurant.city_id == city.id,
                Restaurant.poi_source == POI_SOURCE,
                Restaurant.poi_id == record.poi_id,
            )
        )
        if restaurant is None and norm:
            restaurant = session.scalar(
                select(Restaurant).where(
                    Restaurant.city_id == city.id, Restaurant.name_norm == norm
                )
            )

        if restaurant is None:
            restaurant = Restaurant(
                city_id=city.id,
                name=record.name,
                name_norm=norm or record.name,
                status="active",
            )
            session.add(restaurant)
            session.flush()
            created += 1
        else:
            matched += 1

        restaurant.poi_source = POI_SOURCE
        restaurant.poi_id = record.poi_id
        # POI 为权威地址：仅补齐缺失，不覆盖既有更精确的值
        if restaurant.address is None:
            restaurant.address = record.address
        if restaurant.area is None:
            restaurant.area = record.area
        if restaurant.latitude is None:
            restaurant.latitude = record.latitude
        if restaurant.longitude is None:
            restaurant.longitude = record.longitude

        if norm and norm != restaurant.name_norm:
            exists = session.scalar(
                select(ShopAlias).where(
                    ShopAlias.restaurant_id == restaurant.id,
                    ShopAlias.alias_norm == norm,
                )
            )
            if exists is None:
                session.add(
                    ShopAlias(
                        restaurant_id=restaurant.id, alias=record.name, alias_norm=norm
                    )
                )

    session.flush()
    return created, matched