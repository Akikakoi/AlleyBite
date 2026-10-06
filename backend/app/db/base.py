"""数据库连接与会话。本地默认 SQLite，生产切 PostgreSQL 只需改 DATABASE_URL。"""

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..core.config import get_settings


class Base(DeclarativeBase):
    pass


def _create_engine(url: str, echo: bool):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, echo=echo, connect_args=connect_args, future=True)


engine = _create_engine(get_settings().database_url, get_settings().db_echo)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# create_all 只建新表、不改既有表；此处为既有库补齐后加列（接 Alembic 后移除）。
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "mention": {
        "area": "VARCHAR(64)",
        "cuisine": "VARCHAR(64)",
        "avg_price": "FLOAT",
    }
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