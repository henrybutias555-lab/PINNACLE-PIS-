"""Pinnacle Intelligence System - FastAPI application entry point."""
from __future__ import annotations

import html
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .auth import hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from .models import User
from .routers import auth_users, intelligence, records, reports

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pis")

STATIC_DIR = Path(__file__).parent / "static"

_init_lock = threading.Lock()
_initialised = False


def init_db() -> None:
    """Create tables and the first admin account. Safe to call repeatedly and concurrently."""
    global _initialised
    if _initialised:
        return
    with _init_lock:
        if _initialised:
            return
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            if (db.scalar(select(func.count()).select_from(User)) or 0) == 0:
                db.add(User(email=settings.admin_email.lower(), full_name="System Administrator", role="admin",
                            password_hash=hash_password(settings.admin_password)))
                try:
                    db.commit()
                    log.warning("Created initial admin account %s - change its password after first login.",
                                settings.admin_email)
                except IntegrityError:  # another instance created it first
                    db.rollback()
        log.info("AI extraction/commentary: %s", "enabled" if settings.ai_enabled else "disabled (no Anthropic key)")
        _initialised = True


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.deployment_problems():
        init_db()
    yield


app = FastAPI(title="Pinnacle Intelligence System", version="1.0.0", lifespan=lifespan)


def _setup_page(problems: list[str]) -> str:
    items = "".join(f"<li>{html.escape(p)}</li>" for p in problems)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>PIS setup required</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:3rem auto;padding:0 16px;line-height:1.5;
color:#1c2430;background:#f4f6f9}}code{{background:#e8f1f3;padding:0 .3em;border-radius:4px}}
h1{{color:#0f4c5c}}</style></head><body>
<h1>Pinnacle Intelligence System &mdash; setup required</h1>
<p>The app is deployed but needs these settings before it can run safely. In Vercel open
<b>Project &rarr; Settings &rarr; Environment Variables</b>, add them, then redeploy.</p>
<ul>{items}</ul>
<p>Optional: <code>ANTHROPIC_API_KEY</code> turns on Claude for document extraction and commentary.</p>
</body></html>"""


@app.middleware("http")
async def deployment_guard(request: Request, call_next):
    problems = settings.deployment_problems()
    if problems:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Server setup incomplete", "problems": problems}, status_code=503)
        return HTMLResponse(_setup_page(problems), status_code=503)
    init_db()
    return await call_next(request)


app.include_router(auth_users.router)
app.include_router(reports.router)
app.include_router(records.router)
app.include_router(intelligence.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "ai_enabled": settings.ai_enabled,
            "model": settings.claude_model if settings.ai_enabled else None,
            "default_currency": settings.default_currency, "max_upload_mb": settings.max_upload_mb}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
