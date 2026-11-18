"""Pinnacle Intelligence System - FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .auth import hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from .models import User
from .routers import auth_users, intelligence, records, reports

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pis")

STATIC_DIR = Path(__file__).parent / "static"


def init_db() -> None:
    Base.metadata.create_all(engine)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        if (db.scalar(select(func.count()).select_from(User)) or 0) == 0:
            db.add(User(email=settings.admin_email.lower(), full_name="System Administrator", role="admin",
                        password_hash=hash_password(settings.admin_password)))
            db.commit()
            log.warning("Created initial admin account %s - change its password after first login.",
                        settings.admin_email)
    if settings.secret_key.startswith("dev-insecure"):
        log.warning("PIS_SECRET_KEY is not set; using an insecure development key.")
    log.info("AI extraction/commentary: %s", "enabled" if settings.ai_enabled else "disabled (no Anthropic key)")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Pinnacle Intelligence System", version="1.0.0", lifespan=lifespan)
app.include_router(auth_users.router)
app.include_router(reports.router)
app.include_router(records.router)
app.include_router(intelligence.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "ai_enabled": settings.ai_enabled, "model": settings.claude_model if settings.ai_enabled else None,
            "default_currency": settings.default_currency}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
