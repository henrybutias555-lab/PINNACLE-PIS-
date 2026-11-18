"""Vercel serverless entry point: exposes the FastAPI app.

If the application fails to import (bad configuration, missing package, ...),
serve a small diagnostic page instead of letting Vercel report an opaque
FUNCTION_INVOCATION_FAILED. Secrets from the environment are masked.
"""
from __future__ import annotations

import html
import os
import traceback
from urllib.parse import unquote, urlsplit


def _secrets() -> set[str]:
    values = set()
    for name in ("PIS_SECRET_KEY", "PIS_ADMIN_PASSWORD", "ANTHROPIC_API_KEY"):
        if os.getenv(name):
            values.add(os.environ[name])
    for name in ("PIS_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL"):
        url = os.getenv(name) or ""
        rest = url.partition("://")[2]
        if "@" in rest:
            password = rest[: rest.rfind("@")].partition(":")[2]
            values.update({password, unquote(password)})
    return {v for v in values if v and len(v) >= 4}


def _mask(text: str) -> str:
    for secret in sorted(_secrets(), key=len, reverse=True):
        text = text.replace(secret, "****")
    return text


try:
    from app.main import app  # noqa: F401
except BaseException:  # pragma: no cover - only on a broken deployment
    _detail = _mask(traceback.format_exc())
    _commit = (os.getenv("VERCEL_GIT_COMMIT_SHA") or "unknown")[:7]
    _page = (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>PIS startup error</title><body style='font-family:system-ui;max-width:760px;margin:2rem auto;padding:0 16px'>"
        "<h1 style='color:#b42318'>Pinnacle Intelligence System failed to start</h1>"
        f"<p>Build {html.escape(_commit)}. Send this page to your developer. Secrets are masked.</p>"
        f"<pre style='white-space:pre-wrap;background:#f4f6f9;padding:1rem;border-radius:8px'>{html.escape(_detail)}</pre>"
    ).encode()

    async def app(scope, receive, send):  # minimal ASGI app
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 500,
                    "headers": [(b"content-type", b"text/html; charset=utf-8")]})
        await send({"type": "http.response.body", "body": _page})
