"""原始页面快照留存（文档 4.3：原始 HTML/JSON 存 OSS 保留 90 天）。

本期用本地目录替代 OSS：``<snapshot_dir>/<source>/<YYYYMMDD>/<hash>.<ext>``。
只存页面正文，不落作者身份等个人信息（文档 11.2）。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path


class SnapshotStore:
    def __init__(
        self,
        root: str | Path,
        ttl_days: int = 90,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.root = Path(root)
        self.ttl_days = ttl_days
        self._now = now

    def save(self, source: str, url: str, body: str, ext: str = "html") -> str:
        """写入快照并返回相对引用路径（存入 raw_content.raw_ref）。"""
        digest = hashlib.sha256(f"{url}\n{body}".encode("utf-8")).hexdigest()[:16]
        day = self._now().strftime("%Y%m%d")
        target = self.root / source / day / f"{digest}.{ext}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return str(target)

    def purge_expired(self) -> int:
        """清理超过 ttl_days 的快照，返回删除文件数。"""
        if not self.root.exists():
            return 0
        deadline = time.time() - self.ttl_days * 86400
        removed = 0
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if path.stat().st_mtime < deadline:
                path.unlink()
                removed += 1
        for directory in sorted(self.root.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        return removed