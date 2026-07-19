"""SQLAlchemy 2.0 engine + session factory.

Uses SQLite by default (`.cache/ventureos_ui.db`). Postgres is supported via
DATABASE_URL — flip the env var, no code change. All ORM models use the
generic JSON column type, which maps to SQLite's JSON1 extension or
Postgres's JSONB automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

def _resolve_url() -> str:
    """Prefer DATABASE_URL if it looks like a real connection string,
    otherwise fall back to a local SQLite file under `.cache/`.

    In deployment environments (e.g. Render) set VENTUREOS_DATA_DIR to
    a persistent-disk mount and the SQLite file will live there via the
    symlinked .cache directory.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if url and not url.startswith("postgresql+psycopg://user:pass"):
        # Real Postgres URL provided
        return url
    data_dir = os.getenv("VENTUREOS_DATA_DIR", "").strip()
    if data_dir:
        # Direct path onto the persistent disk — bypass the symlink dance
        db_dir = Path(data_dir) / ".cache"
    else:
        db_dir = Path(".cache")
    db_dir.mkdir(parents=True, exist_ok=True)
    path = db_dir / "ventureos_ui.db"
    return f"sqlite+pysqlite:///{path.resolve()}"


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = _resolve_url()
        # `check_same_thread=False` is required for SQLite when Streamlit
        # spins up worker threads. It's safe here because we always open
        # short-lived sessions and never share Connection objects across threads.
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


def get_session() -> Session:
    """Open a new short-lived session. Callers should use `with get_session() as s:`."""
    return get_session_factory()()


def init_db() -> None:
    """Create all tables. Idempotent — safe to call every startup."""
    # Import here to avoid a circular import: models_orm.Base uses get_engine
    # indirectly via metadata.create_all.
    from ventureos_ui.models_orm import Base

    Base.metadata.create_all(get_engine())


def is_sqlite() -> bool:
    return get_engine().url.get_backend_name() == "sqlite"