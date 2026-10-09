"""第三方平台外链（方向二「到店最后一公里」）：拼搜索直达 URL，不做任何爬取。

大众点评 web 端支持关键词搜索，URL 形如
    https://www.dianping.com/search/keyword/{cityId}/0_{关键词}
cityId 为点评站内城市编码（官方对接文档口径，已与城市页 cityId 实测互证）。
美团无通用 web 店铺页/搜索页，故仅提供点评入口。
"""

from __future__ import annotations

from urllib.parse import quote

# 本项目已开通城市 → 大众点评 cityId（顺德归佛山）
DIANPING_CITY_IDS: dict[str, int] = {
    "上海": 1,
    "北京": 2,
    "杭州": 3,
    "广州": 4,
    "南京": 5,
    "深圳": 7,
    "成都": 8,
    "重庆": 9,
    "扬州": 12,
    "福州": 14,
    "厦门": 15,
    "武汉": 16,
    "西安": 17,
    "长沙": 344,
    "泉州": 129,
    "佛山": 208,
    "顺德": 208,
}

DIANPING_SEARCH_URL = "https://www.dianping.com/search/keyword/{city_id}/0_{keyword}"


def build_dianping_search_url(city_name: str | None, shop_name: str) -> str | None:
    """拼大众点评搜索直达链接；城市未收录或店名为空 → None。"""
    shop_name = (shop_name or "").strip()
    if not city_name or not shop_name:
        return None
    city_id = DIANPING_CITY_IDS.get(city_name.strip())
    if city_id is None:
        return None
    return DIANPING_SEARCH_URL.format(city_id=city_id, keyword=quote(shop_name))
