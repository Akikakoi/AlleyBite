"""run_crawl 编排：多源汇总、单源失败隔离、job_run 落库、夜间跳过。"""

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.collectors import runner as runner_mod
from app.collectors.base import BaseCollector
from app.collectors.core import CrawlItem
from app.collectors.runner import run_crawl
from app.db.models import JobRun, RawContent
from collectors_fakes import make_client, make_deps, make_settings, memory_session

LONG = "成都苍蝇馆子锅气足价钱实惠老板热情。" * 8


class _FakeCollector(BaseCollector):
    """可注入的假采集器：payload 为条目列表、异常或 "not-ready"。"""

    def __init__(self, source, payload, *, settings, deps):
        self.source = source
        self._payload = payload
        super().__init__(settings=settings, deps=deps)

    @property
    def is_ready(self):
        return self._payload != "not-ready"

    def fetch(self, since=None):
        if isinstance(self._payload, Exception):
            raise self._payload
        return list(self._payload)


@pytest.fixture
def session():
    with memory_session() as s:
        yield s


def _patch_builders(monkeypatch, mapping):
    def build(name, settings, *, city_hint=None, deps=None, seed_path=None, **kwargs):
        if name not in mapping:
            raise ValueError(f"未知数据源：{name}")
        return mapping[name]

    monkeypatch.setattr(runner_mod, "build_collector", build)


def _deps():
    return make_deps(make_client(lambda r: httpx.Response(200, text="")))


def test_run_crawl_new_and_failure_isolation(session, monkeypatch):
    settings = make_settings()
    deps = _deps()
    ok = _FakeCollector(
        "alpha", [CrawlItem(source="alpha", raw_text=LONG, city_hint="成都")],
        settings=settings, deps=deps,
    )
    boom = _FakeCollector("beta", RuntimeError("源崩溃"), settings=settings, deps=deps)
    _patch_builders(monkeypatch, {"alpha": ok, "beta": boom})

    job = run_crawl(session, settings, sources=["alpha", "beta"], deps=deps)

    stats = job.stats["sources"]
    assert stats["alpha"]["status"] == "ok"
    assert stats["alpha"]["new"] == 1
    assert stats["beta"]["status"] == "failed"
    assert "源崩溃" in stats["beta"]["error"]
    # 单源失败不阻断：alpha 数据已提交入库
    assert session.scalar(select(func.count()).select_from(RawContent)) == 1
    assert job.status == "success"
    assert job.finished_at is not None


def test_run_crawl_all_failed_marks_job_failed(session, monkeypatch):
    settings = make_settings()
    deps = _deps()
    _patch_builders(
        monkeypatch,
        {
            "alpha": _FakeCollector("alpha", RuntimeError("a 崩"), settings=settings, deps=deps),
            "beta": _FakeCollector("beta", RuntimeError("b 崩"), settings=settings, deps=deps),
        },
    )
    job = run_crawl(session, settings, sources=["alpha", "beta"], deps=deps)

    assert job.status == "failed"
    assert job.error
    assert job.stats["totals"]["ok"] == 0


def test_run_crawl_skips_not_ready_source(session, monkeypatch):
    settings = make_settings()
    deps = _deps()
    fake = _FakeCollector("alpha", "not-ready", settings=settings, deps=deps)
    _patch_builders(monkeypatch, {"alpha": fake})

    job = run_crawl(session, settings, sources=["alpha"], deps=deps)

    assert job.stats["sources"]["alpha"]["status"] == "skipped"
    assert job.stats["totals"]["skipped"] == 1
    assert job.status == "success"


def test_run_crawl_unknown_source_records_failure(session):
    settings = make_settings()
    deps = _deps()
    job = run_crawl(session, settings, sources=["dianping"], deps=deps)

    stats = job.stats["sources"]["dianping"]
    assert stats["status"] == "failed"
    assert "未实现" in stats["error"]
    assert job.status == "failed"


def test_run_crawl_persists_job_run(session, monkeypatch):
    settings = make_settings()
    deps = _deps()
    fake = _FakeCollector(
        "alpha", [CrawlItem(source="alpha", raw_text=LONG, city_hint="成都")],
        settings=settings, deps=deps,
    )
    _patch_builders(monkeypatch, {"alpha": fake})

    run_crawl(session, settings, sources=["alpha"], deps=deps)

    row = session.scalar(
        select(JobRun).where(JobRun.job_type == "crawl").order_by(JobRun.id.desc())
    )
    assert row is not None
    assert row.status == "success"
    assert row.stats["totals"]["sources"] == 1
    assert row.stats["totals"]["new"] == 1


def test_run_crawl_night_skip(session):
    settings = make_settings(crawl_night_pause=True, crawl_night_start=0, crawl_night_end=24)
    deps = _deps()
    job = run_crawl(session, settings, sources=["seed"], deps=deps)

    assert job.status == "success"
    assert job.stats["skipped"] == "night"
    assert job.stats["sources"] == {}


def test_run_crawl_force_bypasses_night(session, tmp_path):
    seed = tmp_path / "s.jsonl"
    seed.write_text(json.dumps({"raw_text": LONG, "city_hint": "成都"}), encoding="utf-8")
    settings = make_settings(crawl_night_pause=True, crawl_night_start=0, crawl_night_end=24)
    deps = _deps()

    job = run_crawl(
        session, settings, sources=["seed"], deps=deps, seed_path=str(seed), force=True
    )

    assert job.stats.get("skipped") is None
    assert job.stats["sources"]["seed"]["new"] == 1