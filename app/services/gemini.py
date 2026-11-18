"""Google Gemini integration: document data extraction and market commentary.

Mirrors services/llm.py (Claude) so either provider can be selected with
PIS_AI_PROVIDER, or automatically from whichever API key is configured.
"""
from __future__ import annotations

import copy
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

    return genai.Client(api_key=api_key())


def _call(system: str, contents: list, *, max_tokens: int, json_schema: dict | None = None) -> str:
    from google.genai import errors, types

    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_tokens,
        **({"response_mime_type": "application/json", "response_json_schema": json_schema} if json_schema else {}),
    )
    try:
        response = _client().models.generate_content(model=settings.gemini_model, contents=contents, config=config)
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
    except Exception as exc:  # network problems and anything unexpected
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
