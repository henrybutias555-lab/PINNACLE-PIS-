"""Pinnacle Intelligence System - FastAPI application entry point."""
from __future__ import annotations

import html
import os
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
from .database import ENGINE_ERROR, Base, SessionLocal, add_missing_columns, engine, ensure_schema, safe_url
from .models import User
from .routers import attachments, auth_users, imports, intelligence, records, reports

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
        ensure_schema(engine)
        Base.metadata.create_all(engine)
        for column in add_missing_columns(engine):
            log.info("Database upgraded: added column %s", column)
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
        log.info("AI extraction/commentary: %s", f"{settings.ai_provider} ({settings.ai_model})" if settings.ai_enabled
                 else "disabled (no GEMINI_API_KEY or ANTHROPIC_API_KEY)")
        _initialised = True


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Initialisation happens on the first request (see deployment_guard), never at
    # startup: a database outage at startup would crash the whole serverless function.
    yield


app = FastAPI(title="Pinnacle Intelligence System", version="1.0.0", lifespan=lifespan)


BUILD = (os.getenv("VERCEL_GIT_COMMIT_SHA") or os.getenv("RENDER_GIT_COMMIT") or "local")[:7]


def _asset_version() -> str:
    """Changes whenever the front-end files change (works locally too)."""
    import hashlib

    digest = hashlib.sha1()
    for name in ("styles.css", "app.js"):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def _setup_page(problems: list[str]) -> str:
    items = "".join(f"<li>{html.escape(p)}</li>" for p in problems)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>PIS setup required</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:3rem auto;padding:0 16px;line-height:1.5;
color:#1c2430;background:#f4f6f9}}code{{background:#e8f1f3;padding:0 .3em;border-radius:4px}}
h1{{color:#0f4c5c}}</style></head><body>
<h1>Pinnacle Intelligence System &mdash; setup required</h1>
<p>The app is deployed but needs these settings before it can run safely. Add them in your host's
environment settings (Render: <b>Service &rarr; Environment</b>; Vercel: <b>Project &rarr; Settings &rarr;
Environment Variables</b>), then redeploy.</p>
<ul>{items}</ul>
<p>Optional: <code>GEMINI_API_KEY</code> (Google Gemini) or <code>ANTHROPIC_API_KEY</code> (Claude) turns on AI document extraction and commentary.</p>
<p style="color:#64707f;font-size:.85em">Build {html.escape(BUILD)}</p>
</body></html>"""


def _db_error_message(exc: Exception | str) -> str:
    """Readable database error with any password removed."""
    from urllib.parse import unquote

    from sqlalchemy.engine import make_url

    text = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
    try:
        password = make_url(settings.database_url).password
    except Exception:
        password = None
    for secret in {password, unquote(password or "")} - {None, ""}:
        text = text.replace(secret, "****")
    return f"{text} (connecting to {safe_url(settings.database_url)})"


def _guard_response(request: Request, problems: list[str]):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Server setup incomplete", "problems": problems}, status_code=503)
    return HTMLResponse(_setup_page(problems), status_code=503)


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):
    """Versioned assets (?v=...) never change, so browsers may keep them; anything
    else under /static must be re-checked every time so an update shows at once."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        if "v=" in request.url.query:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def deployment_guard(request: Request, call_next):
    if request.url.path == "/healthz":  # liveness check for the host; never blocked
        return await call_next(request)
    problems = settings.deployment_problems()
    if not problems and ENGINE_ERROR:
        problems = ["DATABASE_URL is not valid: " + _db_error_message(ENGINE_ERROR)]
    if not problems:
        try:
            init_db()
        except Exception as exc:
            log.exception("Database initialisation failed")
            problems = ["Could not connect to the database. Check DATABASE_URL (use the Supabase "
                        "'Transaction pooler' string and the correct password). Error: " + _db_error_message(exc)]
    if problems:
        return _guard_response(request, problems)
    return await call_next(request)


app.include_router(auth_users.router)
app.include_router(reports.router)
app.include_router(records.router)
app.include_router(intelligence.router)
app.include_router(imports.router)
app.include_router(attachments.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "alive", "build": BUILD}


@app.get("/api/health")
def health():
    return {"status": "ok", "ai_enabled": settings.ai_enabled,
            "ai_provider": settings.ai_provider, "model": settings.ai_model,
            "default_currency": settings.default_currency, "max_upload_mb": settings.max_upload_mb,
            "build": BUILD, "asset_version": ASSET_VERSION}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    # Version the asset URLs so browsers fetch new CSS/JS after every deploy,
    # and never cache the page itself.
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    page = page.replace('<meta name="viewport"', f'<meta name="pis-version" content="{ASSET_VERSION}">\n  <meta name="viewport"', 1)
    for asset in ("/static/styles.css", "/static/app.js", "/static/vendor/chart.umd.js"):
        page = page.replace(f'"{asset}"', f'"{asset}?v={ASSET_VERSION}"')
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})
