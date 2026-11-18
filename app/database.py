from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


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
                         connect_args={"prepare_threshold": None})


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
