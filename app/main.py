"""Pinnacle Intelligence System - FastAPI application entry point."""
from __future__ import annotations

import html
import os
import re
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
from .database import (ENGINE_ERROR, Base, SessionLocal, add_missing_columns, engine, ensure_schema, safe_url,
                       widen_columns)
from .models import User
from .routers import attachments, auth_users, gaps, imports, intelligence, records, reports

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pis")

STATIC_DIR = Path(__file__).parent / "static"

_init_lock = threading.Lock()
_initialised = False


def _fix_mmda_values() -> int:
    """Records whose MMDA isn't an assembly (e.g. 'Central Business District'): move the
    text to Location when that is empty, and re-derive the MMDA. Returns how many changed."""
    from .models import ActivityLog, Record
    from .services import places

    changed = 0
    with SessionLocal() as db:
        for rec in db.scalars(select(Record).where(Record.district.is_not(None))):
            if places.looks_like_mmda(rec.district, said_assembly=True) and not re.search(
                    r"business|industrial|commercial|residential", rec.district, re.I):
                continue
            old = rec.district
            if not rec.location:
                rec.location = old
            rec.district = places.mmda_for(rec.location, rec.city, " , ".join(x for x in (rec.location, rec.address) if x))
            db.add(ActivityLog(action="mmda_corrected", target=f"record:{rec.id}",
                               detail=f"'{old}' is not an assembly; MMDA now '{rec.district or ''}'"))
            changed += 1
        db.commit()
    return changed


def _fix_basis_values() -> int:
    """Saved bases of value with extra words ('Market Value VALUATION OPINION') -> one standard basis."""
    from .models import Record
    from .services.rules import normalise_basis

    changed = 0
    with SessionLocal() as db:
        for rec in db.scalars(select(Record).where(Record.basis_of_value.is_not(None))):
            clean = normalise_basis(rec.basis_of_value)
            if clean != rec.basis_of_value:
                rec.basis_of_value = clean
                changed += 1
        db.commit()
    return changed


def _safe_self_check(module) -> None:
    try:
        module.self_check()
    except Exception:  # never let the check affect the site
        log.exception("Gemini self-check crashed")


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
        for column in widen_columns(engine):
            log.info("Database upgraded: widened column %s", column)
        fixed = _fix_mmda_values() + _fix_basis_values()
        if fixed:
            log.warning("Corrected %d MMDA value(s) that were not assemblies", fixed)
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
        if settings.ai_provider == "gemini":
            # Check, in the background, that the key works and the model reads reports correctly.
            from .services import gemini as _gemini
            threading.Thread(target=_safe_self_check, args=(_gemini,), daemon=True).start()
        log.info("AI extraction/commentary: %s", f"{settings.ai_provider} ({settings.ai_model})" if settings.ai_enabled
                 else "disabled (no GEMINI_API_KEY or ANTHROPIC_API_KEY)")
        _initialised = True


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Initialisation happens on the first request (see deployment_guard), never at
    # startup: a database outage at startup would crash the whole serverless function.
    yield


app = FastAPI(title="Pinnacle Intelligence System", version="1.0.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def friendly_errors(request: Request, exc: Exception):
    """Unexpected problems: log the details, tell the user in plain words."""
    from sqlalchemy.exc import DataError, IntegrityError, OperationalError

    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    if isinstance(exc, DataError):
        detail = "One of the values is too long or in the wrong format for its field. Shorten long text " \
                 "(e.g. ratings should be a word like 'Good') and try again."
        status = 422
    elif isinstance(exc, IntegrityError):
        detail, status = "This change conflicts with existing data (for example a duplicate). Nothing was saved.", 409
    elif isinstance(exc, OperationalError):
        detail, status = "The database could not be reached just now. Please try again in a moment.", 503
    else:
        detail, status = "Something went wrong on the server and nothing was saved. Please try again; " \
                         "if it keeps happening, tell your administrator the time it happened.", 500
    return JSONResponse({"detail": detail}, status_code=status)


BUILD = (os.getenv("VERCEL_GIT_COMMIT_SHA") or os.getenv("RENDER_GIT_COMMIT") or "local")[:7]


def _asset_version() -> str:
    """Changes whenever the front-end files change (works locally too)."""
    import hashlib

    digest = hashlib.sha1()
    for name in ("styles.css", "app.js"):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def _mmda_json() -> str:
    import json

    from .services.mmda_data import MMDAS

    rows = [[name, region, capital] for name, region, capital, _ in MMDAS]
    return json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")


MMDA_JSON = _mmda_json()


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
app.include_router(gaps.router)


@app.get("/api/mmdas")
def mmdas():
    """All 261 Metropolitan, Municipal and District Assemblies with region and capital."""
    from .services.mmda_data import MMDAS

    return [{"name": n, "region": r, "capital": c} for n, r, c, _ in MMDAS]


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "alive", "build": BUILD}


def _ai_check() -> dict | None:
    if settings.ai_provider != "gemini":
        return None
    from .services.gemini import STATUS
    return {k: STATUS.get(k) for k in ("ok", "model", "detail", "checked_at")}


@app.get("/api/health")
def health():
    return {"status": "ok", "ai_enabled": settings.ai_enabled,
            "ai_provider": settings.ai_provider, "model": settings.ai_model, "ai_check": _ai_check(),
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
    page = page.replace('<script src="/static/app.js', f"<script>window.PIS_MMDAS = {MMDA_JSON};</script>\n  "
                        '<script src="/static/app.js', 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})
