"""营业时间解析测试（方向二）：高德 opentime 文本 → 当前是否营业。"""

from datetime import datetime, timezone

from app.services.open_hours import is_open_now, parse_open_ranges

# 2026-10-09 15:00 北京时间（周四下午）
NOON = datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)          # 15:00 北京
DEEP_NIGHT = datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc)   # 01:00 北京（次日凌晨）


def test_parse_open_ranges_extracts_all():
    assert parse_open_ranges("周一至周日 11:00-21:00") == [(660, 1260)]
    assert parse_open_ranges("11:00-14:00,17:00-21:30") == [(660, 840), (1020, 1290)]
    # 全角冒号与多种连接符
    assert parse_open_ranges("09：30–13：00") == [(570, 780)]
    assert parse_open_ranges("") == []
    assert parse_open_ranges("全年无休") == []


def test_is_open_now_basic_and_overnight():
    # 15:00 北京时间
    assert is_open_now("周一至周日 11:00-21:00", NOON) is True
    assert is_open_now("周一至周五 11:00-14:00", NOON) is False
    # 跨零点：21:00-02:00，01:00 仍营业；15:00 已打烊
    assert is_open_now("周一至周日 21:00-02:00", DEEP_NIGHT) is True
    assert is_open_now("周一至周日 21:00-02:00", NOON) is False
    # 多时段任一命中即营业
    assert is_open_now("11:00-14:00,17:00-21:30", NOON) is False


def test_is_open_now_unparseable_returns_none():
    assert is_open_now("全年无休") is None
    assert is_open_now(None) is None
    assert is_open_now("") is None
