"""数据库连接与会话。本地默认 SQLite，生产切 PostgreSQL 只需改 DATABASE_URL。"""

from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..core.config import get_settings


class Base(DeclarativeBase):
    pass


def _create_engine(url: str, echo: bool):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, echo=echo, connect_args=connect_args, future=True)


engine = _create_engine(get_settings().database_url, get_settings().db_echo)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_foreign_keys_on(dbapi_connection, _record):
    """SQLite 默认不强制外键，生产 PostgreSQL 始终强制。

    本地打开后行为与生产一致：脏外键（如 view_event 指向已删店铺）
    立即报 IntegrityError，而不是静默成功、上线后炸 500。
    """
    if get_settings().database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# create_all 只建新表、不改既有表；此处为既有库补齐后加列（接 Alembic 后移除）。
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "mention": {
        "area": "VARCHAR(64)",
        "cuisine": "VARCHAR(64)",
        "avg_price": "FLOAT",
    },
    "app_user": {
        "email": "VARCHAR(255)",
    },
    "restaurant": {
        "open_hours": "VARCHAR(128)",
        # 方向四：老店档案 + 存活监测（2026-10-14 设计方案，第 1/2 批）
        "first_mentioned_at": "TIMESTAMP",
        "first_evidence_confident": "BOOLEAN NOT NULL DEFAULT FALSE",
        "last_mentioned_at": "TIMESTAMP",
        "last_seen_at": "TIMESTAMP",
        "miss_count": "INTEGER NOT NULL DEFAULT 0",
        "liveness": "VARCHAR(16) NOT NULL DEFAULT 'active'",
        "closed_at": "TIMESTAMP",
    },
    "raw_content": {
        # ingest 幂等短路指纹（2026-10-16）：重复抓取不再作废清洗/抽取结果
        "clean_fingerprint": "VARCHAR(80)",
    },
    "email_code": {
        # 邮箱验证码防爆破：连续失败达上限即作废该码（2026-10-16）
        "fail_count": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    """建表（M0 用 create_all；后续接 Alembic 做迁移）。"""
    from . import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(engine)
    _add_missing_columns()


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session