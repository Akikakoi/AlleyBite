"""营业时间解析（方向二「到店最后一公里」）：从高德 opentime 文本算当前是否营业。

高德 opentime2 形如「周一至周日 11:00-21:00」「11:00-14:00,17:00-21:30」，
格式繁杂：解析策略是提取文本中所有 HH:MM-HH:MM 时段，能提取才算得准；
提不出（如「全年无休」「随缘开门」）返回 None，前端展示原文即可，不猜。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .scoring import to_utc

_RANGE_RE = re.compile(
    r"(\d{1,2})[:：](\d{2})\s*[-–—~至]\s*(\d{1,2})[:：](\d{2})"
)
# 北京时间的本地时钟：高德数据是国内店铺
_CN_TZ = timezone(timedelta(hours=8))


def parse_open_ranges(text: str | None) -> list[tuple[int, int]]:
    """提取所有 HH:MM-HH:MM 时段（分钟数）；无匹配 → []。"""
    if not text:
        return []
    ranges: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(text):
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = int(m.group(3)) * 60 + int(m.group(4))
        if start == end:
            # 0:00-0:00 视为 24 小时
            ranges.append((0, 24 * 60))
        else:
            ranges.append((start, end))
    return ranges


def is_open_now(open_hours: str | None, now: datetime | None = None) -> bool | None:
    """按北京时间判断当前是否在营业时段内；解析不出 → None。

    跨零点时段（如 21:00-02:00）按「end < start 即跨天」处理。
    """
    ranges = parse_open_ranges(open_hours)
    if not ranges:
        return None
    now = to_utc(now) or datetime.now(timezone.utc)
    local = now.astimezone(_CN_TZ)
    minutes = local.hour * 60 + local.minute
    for start, end in ranges:
        if start < end:
            if start <= minutes < end:
                return True
        elif end < start:
            # 跨零点：当前在 [start, 24:00) 或 [0, end)
            if minutes >= start or minutes < end:
                return True
    return False
