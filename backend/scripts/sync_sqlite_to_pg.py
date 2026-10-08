"""运维脚本：把本地 SQLite 开发库同步到 PostgreSQL（Docker 部署库）。

背景：本地开发库（backend/alleybite.db）是数据验收签认后的最新状态
（83 条真实媒体报道种子 + 49 条演示种子 + 1 条官方页），
而 Docker 部署库可能停留在旧流水线状态。本脚本把本地库完整搬入 PG，
不重跑 LLM 抽取，保留已验收的 mention / 榜单快照。

用法（在仓库根目录）：
  docker compose stop scheduler
  docker cp backend/alleybite.db <api容器>:/tmp/alleybite.db
  docker cp backend/scripts/sync_sqlite_to_pg.py <api容器>:/tmp/
  docker exec <api容器> python /tmp/sync_sqlite_to_pg.py
  docker compose start scheduler
  docker exec <redis容器> redis-cli FLUSHALL

行为：
  1) 按 app.db.models 当前定义在 PG 上 drop_all + create_all
     （表结构与当前代码严格一致，旧数据全部清空，由 SQLite 数据取代）
  2) 按 metadata.sorted_tables 依赖顺序逐表搬运，保留主键 / 外键 / JSON 结构
  3) naive datetime 一律补 UTC 时区（SQLite 不存时区，应用层统一写 UTC）
  4) 重置各表 id 序列，避免后续插入主键冲突
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from app.db import models  # noqa: F401  确保全部表注册进 metadata
from app.db.base import Base

SQLITE_PATH = os.environ.get("SYNC_SQLITE_PATH", "/tmp/alleybite.db")
PG_URL = os.environ["DATABASE_URL"]

sqlite_engine = create_engine(f"sqlite:///{SQLITE_PATH}")
pg_engine = create_engine(PG_URL)

# 1) 重建 PG 表结构（旧表结构与数据一并清除）
Base.metadata.drop_all(pg_engine)
Base.metadata.create_all(pg_engine)

# 2) 逐表搬运
tables = Base.metadata.sorted_tables
counts: dict[str, int] = {}
with sqlite_engine.connect() as src, pg_engine.begin() as dst:
    for table in tables:
        rows = [dict(r._mapping) for r in src.execute(table.select())]
        counts[table.name] = len(rows)
        if not rows:
            continue
        # 时区处理：SQLite 读回的 naive datetime 视为 UTC
        for row in rows:
            for key, value in row.items():
                if isinstance(value, datetime):
                    row[key] = (
                        value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                    )
        dst.execute(table.insert(), rows)

# 3) 重置 id 序列
with pg_engine.begin() as conn:
    for table in tables:
        if "id" not in table.columns:
            continue
        seq = conn.execute(
            text("SELECT pg_get_serial_sequence(:t, 'id')"),
            {"t": table.name},
        ).scalar()
        if not seq:
            continue
        max_id = (
            conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table.name}"))
            .scalar()
            or 0
        )
        if max_id > 0:
            conn.execute(text(f"SELECT setval('{seq}', {max_id}, true)"))
        else:
            conn.execute(text(f"SELECT setval('{seq}', 1, false)"))

print("sync done:")
for name, count in counts.items():
    print(f"  {name}: {count}")
sys.exit(0)
