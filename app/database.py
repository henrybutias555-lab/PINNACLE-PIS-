from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def safe_url(url: str) -> str:
    """The database URL with the password hidden, for error messages."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "(unparseable database URL)"


def _make_engine(url: str):
    if url.startswith("sqlite"):
        db_path = url.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    # Serverless instances are frozen between requests; keep the pool small and
    # check connections before use so stale ones are replaced.
    # prepare_threshold=None: transaction-mode poolers (Supabase Supavisor port 6543,
    # PgBouncer, Neon pooled URLs) do not support server-side prepared statements.
    return create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=3, pool_recycle=300,
                         connect_args={"prepare_threshold": None, "connect_timeout": 10})


# Never let a bad database URL or a missing driver crash the whole app at import
# time: record the problem and let the app explain it on every request instead.
ENGINE_ERROR: str | None = None
try:
    engine = _make_engine(settings.database_url)
except Exception as exc:  # pragma: no cover - depends on deployment config
    ENGINE_ERROR = f"{type(exc).__name__}: {exc}"
    log.error("Could not configure database %s: %s", safe_url(settings.database_url), ENGINE_ERROR)
    engine = create_engine("sqlite://")

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
