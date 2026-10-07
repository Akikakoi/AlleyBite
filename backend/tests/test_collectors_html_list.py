"""公开列表页：标量解析（links/title/text）+ 列表页→详情页采集 + 单页即内容模式。"""

from app.collectors.html_list import (
    HtmlListCollector,
    extract_links,
    extract_text,
    extract_title,
    parse_page_specs,
    strip_html,
)
from collectors_fakes import make_client, make_deps, make_settings, map_handler

LIST = """<html><head><title>成都美食列表</title></head><body>
  <a href="/p/1">店一</a>
  <a href="/p/2">店二</a>
  <a href="http://other.test/p/3">外链</a>
  <a href="/list">自身</a>
  <a href="mailto:x@y.com">邮件</a>
</body></html>"""

DETAIL_1 = "<html><head><title>店一</title></head><body><p>锅气足，排队长。</p><script>var x=1;</script></body></html>"
DETAIL_2 = "<html><head><title>店二</title></head><body><p>味道一般。</p></body></html>"


def _collector(handler, *, list_urls, settings=None):
    settings = settings or make_settings(crawl_html_list_urls=",".join(list_urls))
    deps = make_deps(make_client(handler))
    return HtmlListCollector(
        settings=settings, deps=deps, list_urls=list_urls, city_hint="成都"
    )


# --- 解析 -------------------------------------------------------------------

def test_extract_links_same_page_dedupe_and_order():
    assert extract_links(LIST, "http://site.test/list") == [
        "http://site.test/p/1",
        "http://site.test/p/2",
        "http://other.test/p/3",
        "http://site.test/list",
    ]


def test_strip_html_skips_script_and_style():
    html = "<div>正文<script>bad()</script><style>.a{}</style>尾</div>"
    assert strip_html(html) == "正文 尾"


def test_extract_text_truncates_to_limit():
    assert len(extract_text("<p>" + "字" * 8000 + "</p>")) == 6000


def test_extract_title():
    assert extract_title(DETAIL_1) == "店一"
    assert extract_title("<html></html>") is None


# --- 采集器 -----------------------------------------------------------------

def test_fetches_same_host_detail_pages_only():
    handler = map_handler(
        {
            "http://site.test/list": (200, LIST),
            "http://site.test/p/1": (200, DETAIL_1),
            "http://site.test/p/2": (200, DETAIL_2),
        }
    )
    collector = _collector(handler, list_urls=["http://site.test/list"])
    items = collector.fetch()

    assert [i.source_url for i in items] == ["http://site.test/p/1", "http://site.test/p/2"]
    assert items[0].raw_title == "店一"
    assert "锅气足，排队长。" in items[0].raw_text


def test_respects_max_items():
    handler = map_handler(
        {
            "http://site.test/list": (200, LIST),
            "http://site.test/p/1": (200, DETAIL_1),
            "http://site.test/p/2": (200, DETAIL_2),
        }
    )
    settings = make_settings(crawl_max_items_per_source=1)
    collector = _collector(handler, list_urls=["http://site.test/list"], settings=settings)
    assert len(collector.fetch()) == 1


def test_not_ready_without_urls():
    collector = _collector(map_handler({}), list_urls=[])
    assert collector.is_ready is False


# --- 单页即内容模式 ----------------------------------------------------------

PAGE_GZ = (
    "<html><head><title>广州老字号名单</title></head><body>"
    "<p>点都德 广州市越秀区惠福东路1号</p>"
    "<p>广州酒家 广州市荔湾区文昌南路2号</p>"
    "<a href=\"/other\">无关外链</a>"
    "</body></html>"
)
PAGE_CS = (
    "<html><head><title>长沙名店名单</title></head><body>"
    "<p>火宫殿 长沙市天心区坡子街1号</p></body></html>"
)


def _page_collector(handler, *, page_urls, city_hint=None):
    settings = make_settings()
    deps = make_deps(make_client(handler))
    return HtmlListCollector(
        settings=settings, deps=deps, page_urls=page_urls, city_hint=city_hint
    )


def test_parse_page_specs_city_prefix_and_default():
    assert parse_page_specs(["广州=http://a.test/x", "http://b.test/y"], "长沙") == [
        ("广州", "http://a.test/x"),
        ("长沙", "http://b.test/y"),
    ]
    assert parse_page_specs(["", "not-a-url", "广州=ftp://x", "=http://c.test/z"], "成都") == [
        ("成都", "http://c.test/z")
    ]


def test_single_page_mode_takes_whole_page_without_following_links():
    handler = map_handler({"http://gz.test/list": (200, PAGE_GZ)})
    collector = _page_collector(handler, page_urls=["广州=http://gz.test/list"])
    items = collector.fetch()

    assert len(items) == 1
    assert items[0].source_url == "http://gz.test/list"
    assert items[0].city_hint == "广州"
    assert items[0].raw_title == "广州老字号名单"
    assert "点都德" in items[0].raw_text
    assert collector.counters["requests"] == 1  # 不跟进页面内任何外链


def test_single_page_mode_filters_by_city_hint():
    handler = map_handler(
        {"http://gz.test/list": (200, PAGE_GZ), "http://cs.test/list": (200, PAGE_CS)}
    )
    collector = _page_collector(
        handler,
        page_urls=["广州=http://gz.test/list", "长沙=http://cs.test/list"],
        city_hint="广州",
    )
    items = collector.fetch()

    assert [i.source_url for i in items] == ["http://gz.test/list"]
    assert collector.counters["requests"] == 1


def test_ready_with_only_page_urls():
    collector = _page_collector(map_handler({}), page_urls=["广州=http://gz.test/list"])
    assert collector.is_ready is True


def test_page_and_list_modes_coexist():
    handler = map_handler(
        {
            "http://gz.test/list": (200, PAGE_GZ),
            "http://site.test/list": (200, LIST),
            "http://site.test/p/1": (200, DETAIL_1),
            "http://site.test/p/2": (200, DETAIL_2),
        }
    )
    settings = make_settings()
    deps = make_deps(make_client(handler))
    collector = HtmlListCollector(
        settings=settings,
        deps=deps,
        list_urls=["http://site.test/list"],
        page_urls=["广州=http://gz.test/list"],
    )
    items = collector.fetch()

    assert [i.source_url for i in items] == [
        "http://gz.test/list",
        "http://site.test/p/1",
        "http://site.test/p/2",
    ]