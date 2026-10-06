"""公开列表页：标量解析（links/title/text）+ 列表页→详情页采集。"""

from app.collectors.html_list import (
    HtmlListCollector,
    extract_links,
    extract_text,
    extract_title,
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