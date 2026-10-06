"""公开 RSS/Atom：stdlib 解析、增量游标、上限、异常告警。"""

from datetime import datetime, timezone

from app.collectors.rss import RssCollector, parse_rss
from collectors_fakes import make_client, make_deps, make_settings, map_handler

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>美食</title>
  <item>
    <title>成都探店</title>
    <link>http://feed.test/p/1</link>
    <description>&lt;p&gt;锅气足，&lt;b&gt;好吃&lt;/b&gt;&lt;/p&gt;</description>
    <pubDate>Mon, 01 Jun 2026 08:00:00 GMT</pubDate>
  </item>
  <item>
    <title>重庆小面</title>
    <link>http://feed.test/p/2</link>
    <description>麻辣鲜香</description>
    <pubDate>Mon, 01 Jan 2024 08:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom 条目</title>
    <link href="/post/9" rel="alternate"/>
    <summary>摘要内容</summary>
    <updated>2026-02-03T04:05:06Z</updated>
  </entry>
</feed>"""


def _collector(handler, *, feed_urls, settings=None, alerts=None):
    settings = settings or make_settings(crawl_rss_urls=",".join(feed_urls))
    deps = make_deps(make_client(handler), alerts=alerts)
    return RssCollector(
        settings=settings, deps=deps, feed_urls=feed_urls, city_hint="成都"
    )


# --- 解析 -------------------------------------------------------------------

def test_parse_rss_20():
    entries = parse_rss(RSS.encode("utf-8"), base_url="http://feed.test/rss.xml")
    assert len(entries) == 2
    assert entries[0].title == "成都探店"
    assert entries[0].link == "http://feed.test/p/1"
    assert entries[0].summary == "锅气足， 好吃"
    assert entries[0].published_at == datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)


def test_parse_atom_with_href_link():
    entries = parse_rss(ATOM.encode("utf-8"), base_url="http://a.test/feed")
    assert len(entries) == 1
    assert entries[0].link == "http://a.test/post/9"
    assert entries[0].summary == "摘要内容"
    assert entries[0].published_at == datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)


# --- 采集器 -----------------------------------------------------------------

def test_fetch_maps_entries_and_applies_cursor():
    handler = map_handler({"http://feed.test/rss.xml": (200, RSS)})
    collector = _collector(handler, feed_urls=["http://feed.test/rss.xml"])

    items = collector.fetch()
    assert [i.raw_text for i in items] == ["锅气足， 好吃", "麻辣鲜香"]
    assert items[0].source_url == "http://feed.test/p/1"
    assert items[0].city_hint == "成都"

    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert [i.raw_text for i in collector.fetch(since=since)] == ["锅气足， 好吃"]


def test_fetch_respects_max_items():
    handler = map_handler({"http://feed.test/rss.xml": (200, RSS)})
    settings = make_settings(crawl_max_items_per_source=1)
    collector = _collector(handler, feed_urls=["http://feed.test/rss.xml"], settings=settings)
    assert len(collector.fetch()) == 1


def test_malformed_feed_alerts_and_returns_empty():
    alerts: list[tuple[str, dict]] = []
    handler = map_handler({"http://feed.test/bad.xml": (200, "<rss><channel>")})
    collector = _collector(handler, feed_urls=["http://feed.test/bad.xml"], alerts=alerts)

    assert collector.fetch() == []
    assert "rss_parse_error" in [event for event, _ in alerts]


def test_not_ready_without_urls():
    collector = _collector(map_handler({}), feed_urls=[])
    assert collector.is_ready is False