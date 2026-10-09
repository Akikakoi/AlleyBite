"""第三方外链测试：大众点评搜索直达（方向二），仅拼 URL 不发起网络请求。"""

from app.services.external_links import build_dianping_search_url


def test_dianping_search_url_known_cities():
    assert build_dianping_search_url("成都", "明婷饭店") == (
        "https://www.dianping.com/search/keyword/8/0_%E6%98%8E%E5%A9%B7%E9%A5%AD%E5%BA%97"
    )
    assert "/search/keyword/129/0_" in build_dianping_search_url("泉州", "某店")
    assert "/search/keyword/208/0_" in build_dianping_search_url("顺德", "某店")


def test_dianping_search_url_fallbacks():
    # 未收录城市 / 缺参 → None，前端不展示按钮
    assert build_dianping_search_url("北京五环外", "某店") is None
    assert build_dianping_search_url("成都", "") is None
    assert build_dianping_search_url(None, "某店") is None
