"""Google Gemini integration: document data extraction and market commentary.

Mirrors services/llm.py (Claude) so either provider can be selected with
PIS_AI_PROVIDER, or automatically from whichever API key is configured.
"""
from __future__ import annotations

import copy
import re
import json
import logging
import mimetypes
import os
from functools import lru_cache
from pathlib import Path

from ..config import settings
from .llm import (COMMENTARY_SYSTEM, EXTRACTION_SCHEMA, EXTRACTION_SYSTEM, MAX_TEXT_CHARS, AIError,
                  _with_mmda_list, clean_extraction)

log = logging.getLogger(__name__)

MAX_INLINE_BYTES = 18 * 1024 * 1024  # Gemini inline request limit is 20 MB


def _plain_schema(node):
    """Convert the strict, nullable Claude schema into the simpler form Gemini accepts:
    single types, no nulls, nothing required except the top-level essentials."""
    node = copy.deepcopy(node)
    if isinstance(node, dict):
        if "anyOf" in node:
            branches = [b for b in node["anyOf"] if b.get("type") != "null"]
            node = _plain_schema(branches[0]) if branches else {"type": "string"}
            return node
        t = node.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            node["type"] = non_null[0] if non_null else "string"
        if "enum" in node:
            node["enum"] = [e for e in node["enum"] if e is not None]
        node.pop("additionalProperties", None)
        node.pop("required", None)
        if "properties" in node:
            node["properties"] = {k: _plain_schema(v) for k, v in node["properties"].items()}
        if "items" in node:
            node["items"] = _plain_schema(node["items"])
    return node


GEMINI_SCHEMA = _plain_schema(EXTRACTION_SCHEMA)
GEMINI_SCHEMA["required"] = ["report_type", "records"]


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


@lru_cache(maxsize=1)
def _client():
    from google import genai

    from google.genai import types

    # Never let a slow answer hold a surveyor up: after this, the built-in reader's result is used.
    timeout_s = int(os.getenv("PIS_GEMINI_TIMEOUT", "120"))
    return genai.Client(api_key=api_key(), http_options=types.HttpOptions(timeout=timeout_s * 1000))


_THINKING_OK: dict[str, bool] = {}


def _thinking(types):
    """Short thinking: reading figures out of a report doesn't need long deliberation, and the
    newer models think for minutes by default."""
    model = settings.gemini_model
    if _THINKING_OK.get(model) is False:
        return None
    if re.match(r"gemini-([3-9]|\d{2})", model):
        return types.ThinkingConfig(thinking_level="LOW")
    if model.startswith("gemini-2.5"):
        return types.ThinkingConfig(thinking_budget=1024)
    return None


EXTRACTION_SEED = 20251119   # fixed: repeated readings of one report stay as alike as the model allows


def _call(system: str, contents: list, *, max_tokens: int, json_schema: dict | None = None) -> str:
    from google.genai import errors, types

    def config(with_thinking: bool):
        thinking = _thinking(types) if with_thinking else None
        return types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            **({"thinking_config": thinking} if thinking else {}),
            **({"response_mime_type": "application/json", "response_json_schema": json_schema} if json_schema else {}),
            # Extraction: the same report should read the same way every time.
            **({"seed": EXTRACTION_SEED} if json_schema else {}),
            **({"temperature": 0.0} if json_schema and settings.gemini_model.startswith("gemini-2") else {}),
        )

    try:
        try:
            response = _client().models.generate_content(model=settings.gemini_model, contents=contents,
                                                         config=config(True))
        except errors.ClientError as exc:
            # a model that doesn't accept the thinking setting: remember, and ask again without it
            if getattr(exc, "code", None) == 400 and "thinking" in str(exc).lower():
                _THINKING_OK[settings.gemini_model] = False
                response = _client().models.generate_content(model=settings.gemini_model, contents=contents,
                                                             config=config(False))
            else:
                raise
    except errors.ClientError as exc:
        code = getattr(exc, "code", None)
        if code in (401, 403):
            raise AIError("Gemini API key is invalid or lacks access") from exc
        if code == 404:
            raise AIError(f"Gemini model '{settings.gemini_model}' not found; set PIS_GEMINI_MODEL") from exc
        if code == 429:
            raise AIError("Gemini rate limit or quota reached; try again shortly") from exc
        raise AIError(f"Gemini API error ({code}): {getattr(exc, 'message', exc)}") from exc
    except errors.ServerError as exc:
        raise AIError(f"Gemini service error ({getattr(exc, 'code', '')}); try again shortly") from exc
    except Exception as exc:  # network problems, time-outs and anything unexpected
        if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
            raise AIError("Gemini took too long to answer") from exc
        raise AIError(f"Could not reach the Gemini API: {type(exc).__name__}") from exc

    candidate = (response.candidates or [None])[0]
    reason = getattr(getattr(candidate, "finish_reason", None), "name", None)
    if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
        raise AIError("Gemini declined to process this document")
    if reason == "MAX_TOKENS":
        raise AIError("Gemini's response was cut off (document too large); try splitting it")
    text = response.text or ""
    if not text.strip():
        raise AIError("Gemini returned an empty response")
    return text


def extract_report(path: Path, file_type: str, text: str, default_currency: str) -> dict:
    from google.genai import types

    contents: list = []
    size = path.stat().st_size if path.exists() else 0
    if file_type in ("pdf", "image") and size and size <= MAX_INLINE_BYTES:
        mime = "application/pdf" if file_type == "pdf" else (mimetypes.guess_type(path.name)[0] or "image/png")
        contents.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime))
    else:
        if not text.strip():
            raise AIError("No readable text found in the document")
        if len(text) > MAX_TEXT_CHARS:
            raise AIError("Document text is too long for a single extraction; split it into smaller files")
        contents.append(f'<report filename="{path.name}">\n{text}\n</report>')
    contents.append("Extract the structured data from this report.")

    raw = _call(_with_mmda_list(EXTRACTION_SYSTEM.replace("{currency}", default_currency)), contents,
                max_tokens=16000, json_schema=GEMINI_SCHEMA)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIError("Gemini returned malformed JSON") from exc
    return clean_extraction(data)


def market_commentary(prompt: str) -> str:
    return _call(COMMENTARY_SYSTEM, [prompt], max_tokens=8000).strip()


# ------------------------------------------------------------------ self-check

STATUS: dict = {"checked": False, "ok": None, "model": None, "detail": "not checked yet", "checked_at": None}

SAMPLE_REPORT = """VALUATION REPORT
We were instructed by Example Bank Ltd to value the property for mortgage purposes.
LOCATION: The property is at Osu, Accra, within the Korle Klottey Municipal Assembly.
TENURE: Leasehold, 60 years unexpired.
DESCRIPTION: A two-storey detached house with a gross floor area of 250 square metres.
VALUATION: We are of the opinion that the Market Value is GH¢3,000,000.00
(Three Million Ghana Cedis) and the Forced Sale Value is GH¢2,100,000.00."""


def _available_models() -> list[str]:
    """Gemini models this key may use for text generation, best first."""
    names = []
    for m in _client().models.list():
        actions = getattr(m, "supported_actions", None) or []
        name = (m.name or "").removeprefix("models/")
        if "generateContent" in actions and name.startswith("gemini"):
            names.append(name)

    def rank(n: str):
        version = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        v = float(version.group(1)) if version else 0
        stable = not re.search(r"preview|exp|latest|tts|image|live|audio|embedding|thinking", n)
        return (stable, "flash" in n and "lite" not in n, v, "pro" in n)

    return sorted(set(names), key=rank, reverse=True)


FAST_ENOUGH_S = 60


def self_check() -> dict:
    """Send a small sample report through the real extraction and check the answer.
    If the configured model no longer exists, switch to the best model the key can use."""
    import datetime as _dt
    import logging

    log_ = logging.getLogger("pis")
    STATUS.update(checked=True, checked_at=_dt.datetime.now(_dt.timezone.utc).isoformat())
    if not api_key():
        STATUS.update(ok=False, model=None, detail="GEMINI_API_KEY is not set")
        return STATUS

    def attempt() -> tuple[bool, str]:
        import time as _t
        from pathlib import Path as _P
        started = _t.monotonic()
        try:
            draft = extract_report(_P("sample.txt"), "txt", SAMPLE_REPORT, "GHS")
        except AIError as exc:
            return False, f"{exc} (after {_t.monotonic() - started:.0f}s)"
        took = f"in {_t.monotonic() - started:.0f}s"
        rec = (draft.get("records") or [{}])[0]
        got = rec.get("capital_value")
        if got and abs(float(got) - 3_000_000) < 1:
            return True, (f"read the sample report correctly {took} (market value {got:,.0f}, "
                          f"MMDA {rec.get('district') or '-'}, tenure {rec.get('tenure') or '-'})")
        return False, f"answered, but the sample market value came back as {got!r}"

    import time as _time

    started = _time.monotonic()
    ok, detail = attempt()
    slow = _time.monotonic() - started > FAST_ENOUGH_S
    log_.info("Gemini check %s: %s", settings.gemini_model, detail)
    if not ok or slow:
        # Missing, failing or slow: try the other models this key can use, fastest kinds first.
        original = settings.gemini_model
        try:
            models = _available_models()
            log_.info("Gemini models available to this key: %s", ", ".join(models[:15]))
        except Exception as exc:  # listing models failed too
            models = []
            detail += f"; could not list models ({type(exc).__name__})"
        lite_first = sorted([m for m in models if m != original], key=lambda m: ("lite" not in m))
        best = (original, detail) if ok else None
        for candidate in lite_first[:5]:
            settings.gemini_model = candidate
            t0 = _time.monotonic()
            c_ok, c_detail = attempt()
            log_.info("Gemini check %s: %s", candidate, c_detail)
            if c_ok and _time.monotonic() - t0 <= FAST_ENOUGH_S:
                best = (candidate, c_detail + f" (chosen over {original}: faster or working; "
                                              f"set PIS_GEMINI_MODEL={candidate} to keep it)")
                break
            if c_ok and best is None:
                best = (candidate, c_detail)
        if best:
            settings.gemini_model, detail = best
            ok = True
        else:
            settings.gemini_model = original
    STATUS.update(ok=ok, model=settings.gemini_model, detail=detail)
    (log_.info if ok else log_.error)("Gemini self-check %s using %s: %s", "PASSED" if ok else "FAILED",
                                      settings.gemini_model, detail)
    return STATUS
