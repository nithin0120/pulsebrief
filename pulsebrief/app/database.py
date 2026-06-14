"""SQLAlchemy database engine, session management, and lightweight migration."""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    # timeout: wait (seconds) for a competing writer instead of failing immediately
    # with "database is locked" when the scheduler, API, and CLI overlap.
    connect_args={"check_same_thread": False, "timeout": 30},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the first release. SQLite can ALTER TABLE ADD COLUMN
# cheaply, so we add anything missing rather than forcing a DB reset (which
# would wipe the user's local interaction memory).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "articles": {
        "canonical_url": "VARCHAR(2048)",
        "is_opinion": "BOOLEAN DEFAULT 0",
        "importance": "INTEGER",
        "bias_or_angle": "TEXT",
        "key_entities": "TEXT",
        "follow_up_question": "TEXT",
        "background": "TEXT",
        "what_changed_today": "TEXT",
        "what_to_watch_next": "TEXT",
        "cluster_key": "VARCHAR(128)",
    },
    "digest_runs": {
        "cluster_count": "INTEGER DEFAULT 0",
        "brief_json": "TEXT",
        "groq_requests": "INTEGER DEFAULT 0",
        "fetched_count": "INTEGER DEFAULT 0",
    },
}


def _migrate(connection) -> None:
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = {col["name"] for col in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in present:
                continue
            logger.info("Migrating: adding column %s.%s", table, name)
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        _migrate(connection)
