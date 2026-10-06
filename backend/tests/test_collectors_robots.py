"""RobotsGate：Allow / Disallow / 404 放行 / 403 全禁 / 网络异常 fail-open。"""

import httpx

from app.collectors.robots import RobotsGate
from collectors_fakes import make_client, make_deps, map_handler

ROBOTS = "User-agent: *\nDisallow: /private/\n"


def _gate(handler, *, alerts=None, enabled=True) -> RobotsGate:
    deps = make_deps(make_client(handler), alerts=alerts)
    return RobotsGate(deps.http, "AlleyBiteBot/0.1", alert=deps.alert, enabled=enabled)


def test_robots_allows_and_disallows_by_rules():
    gate = _gate(map_handler({"http://site.test/robots.txt": (200, ROBOTS)}))
    assert gate.allows("http://site.test/public") is True
    assert gate.allows("http://site.test/private/x") is False


def test_missing_robots_allows_everything():
    gate = _gate(map_handler({}))  # 一切 URL → 404
    assert gate.allows("http://site.test/anything") is True


def test_forbidden_robots_disallows_everything():
    gate = _gate(map_handler({"http://site.test/robots.txt": (403, "")}))
    assert gate.allows("http://site.test/anything") is False


def test_network_error_fails_open_and_alerts():
    def handler(request):
        raise httpx.ConnectError("boom")

    events: list[str] = []
    gate = RobotsGate(
        make_deps(make_client(handler)).http,
        "bot",
        alert=lambda event, payload: events.append(event),
    )
    assert gate.allows("http://site.test/x") is True
    assert events == ["robots_fetch_failed"]


def test_parser_cached_per_host():
    count = {"robots": 0}

    def handler(request):
        count["robots"] += 1
        return httpx.Response(200, text=ROBOTS)

    gate = _gate(handler)
    gate.allows("http://site.test/a")
    gate.allows("http://site.test/b")
    assert count["robots"] == 1


def test_disabled_gate_skips_network():
    count = {"n": 0}

    def handler(request):
        count["n"] += 1
        return httpx.Response(200, text=ROBOTS)

    gate = _gate(handler, enabled=False)
    assert gate.allows("http://site.test/private/x") is True
    assert count["n"] == 0


def test_invalid_url_rejected():
    gate = _gate(map_handler({}))
    assert gate.allows("ftp://site.test/x") is False
    assert gate.allows("not-a-url") is False