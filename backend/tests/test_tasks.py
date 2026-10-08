"""Celery 调度模块测试（文档 4.4 / 12 章 M4）：配置纯函数与任务注册，不跑真实 broker。"""

from app.tasks import _TASK_NAMES, _broker_url, _build_beat_schedule, app


def test_broker_url_switches_redis_db0_to_db1():
    assert _broker_url("redis://redis:6379/0") == "redis://redis:6379/1"


def test_broker_url_keeps_non_default_db():
    assert _broker_url("redis://redis:6379/3") == "redis://redis:6379/3"


def test_broker_url_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker:6379/9")
    assert _broker_url("redis://redis:6379/0") == "redis://broker:6379/9"


def test_beat_schedule_full_plan():
    schedule = _build_beat_schedule(
        enabled=True, amap_hour=3, text_interval_hours=6, pipeline_hour=5, weekly_report=True
    )
    assert set(schedule) == {
        "refresh-poi-daily",
        "crawl-text-interval",
        "pipeline-daily",
        "quality-weekly",
    }
    assert str(schedule["refresh-poi-daily"]["schedule"]) == "<crontab: 0 3 * * * (m/h/dM/MY/d)>"
    assert (
        str(schedule["crawl-text-interval"]["schedule"]) == "<crontab: 0 */6 * * * (m/h/dM/MY/d)>"
    )
    assert str(schedule["pipeline-daily"]["schedule"]) == "<crontab: 0 5 * * * (m/h/dM/MY/d)>"
    assert str(schedule["quality-weekly"]["schedule"]) == "<crontab: 0 6 * * 1 (m/h/dM/MY/d)>"


def test_beat_schedule_without_weekly_and_disabled():
    schedule = _build_beat_schedule(
        enabled=True, amap_hour=4, text_interval_hours=8, pipeline_hour=6, weekly_report=False
    )
    assert set(schedule) == {"refresh-poi-daily", "crawl-text-interval", "pipeline-daily"}

    assert (
        _build_beat_schedule(
            enabled=False, amap_hour=3, text_interval_hours=6, pipeline_hour=5, weekly_report=True
        )
        == {}
    )


def test_all_tasks_registered():
    assert set(_TASK_NAMES).issubset(set(app.tasks.keys()))
