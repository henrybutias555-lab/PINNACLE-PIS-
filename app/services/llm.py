"""AI integration (Claude, with Gemini in services/gemini.py): extraction and commentary.

Used only when an Anthropic credential is configured (see ``settings.ai_enabled``).
Every call site has a non-AI fallback, so the system keeps working offline.
"""
from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from pathlib import Path

import anthropic

from ..config import settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_INLINE_BYTES = 30 * 1024 * 1024  # stay under the 32 MB request limit
MAX_TEXT_CHARS = 400_000

_STR = {"type": ["string", "null"]}
_NUM = {"type": ["number", "null"]}
_INT = {"type": ["integer", "null"]}

_COMPARABLE = {
    "type": "object",
    "properties": {
        "address": _STR, "property_type": _STR, "date": _STR, "price": _NUM, "rent": _NUM,
        "size_sqm": _NUM, "price_per_sqm": _NUM, "notes": _STR,
    },
}
_COST_ITEM = {
    "type": "object",
    "properties": {"item": _STR, "area_sqm": _NUM, "rate": _NUM, "amount": _NUM},
}
_BREAKDOWN = {
    "type": "object",
    "properties": {
        "preliminaries": _NUM, "substructure": _NUM, "superstructure": _NUM, "finishes": _NUM,
        "services": _NUM, "external_works": _NUM, "contingency": _NUM, "other": _NUM,
    },
}
_RECORD_PROPS = {
    "category": {"type": "string", "enum": ["valuation", "cost", "housing"]},
    "title": _STR, "address": _STR,
    "gps_address": {"type": ["string", "null"], "description": "Ghana Post GPS digital address ONLY: two letters, then digits, e.g. AK-039-5028 or GA-183-8164. Never latitude/longitude (those go in gps_coordinates). null if the report gives no digital address."},
    "location": _STR,
    "district": {"type": ["string", "null"],
                 "description": "MMDA: the local government ASSEMBLY (Metropolitan, Municipal or District Assembly), "
                                "e.g. 'Accra Metropolitan', 'Adentan Municipal', 'Shai-Osudoku District'. NOT a "
                                "neighbourhood or area type such as 'Central Business District' or 'Industrial Area'."},
    "city": _STR, "region": _STR,
    "property_type": _STR, "purpose": _STR, "client": _STR,
    "record_date": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD"},
    "currency": {"type": "string", "description": "ISO currency code, e.g. GHS, USD"},
    "size_sqm": _NUM,
    "valuation_basis": {"type": ["string", "null"], "enum": ["capital", "rental", "both", None]},
    "method": _STR,
    "property_valued": {"type": ["string", "null"], "description": "Short description of the property valued"},
    "basis_of_value": {"type": ["string", "null"],
                       "description": "ONE basis of value, e.g. 'Market Value' or 'Market Rent'. Never combine two "
                                      "bases; a forced sale value goes in forced_sale_value as a number."},
    "valuer": {"type": ["string", "null"], "description": "Valuer identity: name, qualifications, firm"},
    "capital_value": _NUM, "annual_rent": _NUM, "yield_pct": _NUM,
    "value_per_sqm": _NUM, "rent_per_sqm": _NUM, "assumptions": _STR,
    "comparables": {"type": ["array", "null"], "items": _COMPARABLE},
    "cost_items": {"type": ["array", "null"], "items": _COST_ITEM,
                   "description": "Every rate adopted in the cost approach / DRC computation, one row per part "
                                  "of the building as the report lists it (e.g. Ground floor 120 m² at 8,000 per m²; "
                                  "First floor 110 m² at 8,000; Boundary wall ...). Copy each rate exactly."},
    "total_cost": _NUM,
    "cost_per_sqm": {"type": ["number", "null"],
                     "description": "Construction cost per m². Reports call it the 'rate' or 'rate adopted' (e.g. in "
                                    "the cost approach / DRC computation: 'Rate adopted GH¢ 4,500 per m²'). Also for "
                                    "valuation reports."},
    "cost_breakdown": {"anyOf": [_BREAKDOWN, {"type": "null"}]},
    "specification": _STR,
    "avg_price": _NUM, "avg_monthly_rent": _NUM, "supply_units": _INT, "demand_level": _STR,
    "affordability_ratio": _NUM, "stock_notes": _STR,
    "property_name": {"type": ["string", "null"], "description": 'Property name or house / plot number'},
    "owner": {"type": ["string", "null"], "description": 'Registered owner of the property'},
    "use_occupancy": {"type": ["string", "null"], "description": "Current use and occupancy, e.g. 'Residential, owner-occupied'"},
    "highest_best_use": {"type": ["string", "null"], "description": 'Highest and best use'},
    "gps_coordinates": {"type": ["string", "null"], "description": "Latitude, longitude, e.g. '5.6396, -0.1676'"},
    "land_size": {"type": ["string", "null"], "description": "Land / plot size as stated, e.g. '0.25 acres (1,011.7 m²)'"},
    "accessibility": {"type": ["string", "null"], "description": 'Road access and accessibility'},
    "building_type": {"type": ["string", "null"], "description": "e.g. 'Two-storey detached house'"},
    "accommodation": {"type": ["string", "null"], "description": 'Rooms and accommodation'},
    "construction_details": {"type": ["string", "null"], "description": 'Foundation, walls, roof, floors, finishes'},
    "services": {"type": ["string", "null"], "description": 'Water, electricity, drainage, sewage, etc.'},
    "floor_areas": {"type": ["string", "null"], "description": 'Floor areas as described (the total in m² goes in size_sqm)'},
    "ancillary_structures": {"type": ["string", "null"], "description": 'Boys quarters, garage, fence wall, pool, etc.'},
    "ownership": {"type": ["string", "null"], "description": 'Ownership type, e.g. private, family, stool, state'},
    "tenure": {"type": ["string", "null"], "description": 'e.g. Leasehold, Freehold'},
    "source_of_title": {"type": ["string", "null"], "description": 'e.g. Land Title Certificate No. ..., Indenture dated ...'},
    "unexpired_term": {"type": ["string", "null"], "description": "Unexpired term of the lease, e.g. '87 years'"},
    "planning_status": {"type": ["string", "null"], "description": 'Building / development permit and zoning status'},
    "inspection_date": {"type": ["string", "null"], "description": 'Date of inspection, YYYY-MM-DD'},
    "forced_sale_value": {"type": ["number", "null"], "description": 'Forced sale value as a number'},
    "location_rating": {"type": ["string", "null"], "description": 'Excellent / Very good | Good | Fair | Poor'},
    "accessibility_rating": {"type": ["string", "null"], "description": 'Excellent / Very good | Good | Fair | Poor'},
    "state_of_repair": {"type": ["string", "null"], "description": 'Excellent / Very good | Good | Fair | Poor | Dilapidated'},
    "user_conformity": {"type": ["string", "null"], "description": 'Conforming / Non-conforming with zoning'},
    "neighbourhood_rating": {"type": ["string", "null"], "description": 'Excellent / Very good | Good | Fair | Poor'},
    "overall_marketability": {"type": ["string", "null"], "description": 'Excellent / Very good | Good | Fair | Poor'},
    "encumbrances": {"type": ["string", "null"], "description": 'Encumbrances, charges, disputes'},
    "legal_disclaimer": {"type": ["string", "null"], "description": 'Legal disclaimer'},
    "inspection_limitation": {"type": ["string", "null"], "description": 'Limitations of the inspection'},
    "purpose_restriction": {"type": ["string", "null"], "description": 'Restriction on use of the report'},
    "notes": _STR,
    "tags": {"type": ["array", "null"], "items": {"type": "string"}},
}


def _strict(obj: dict) -> dict:
    """Make every object in the schema closed with all properties required (nullable)."""
    if obj.get("type") == "object" or (isinstance(obj.get("type"), list) and "object" in obj["type"]):
        props = obj.get("properties", {})
        obj["required"] = list(props)
        obj["additionalProperties"] = False
        for v in props.values():
            _strict(v)
    if "items" in obj:
        _strict(obj["items"])
    for alt in obj.get("anyOf", []):
        _strict(alt)
    return obj


EXTRACTION_SCHEMA = _strict({
    "type": "object",
    "properties": {
        "report_type": {"type": "string", "enum": ["valuation", "cost", "housing"]},
        "title": _STR,
        "summary": {"type": ["string", "null"], "description": "2-3 sentence summary of the report"},
        "records": {"type": "array", "items": {"type": "object", "properties": _RECORD_PROPS}},
    },
})

EXTRACTION_SYSTEM = """You are the data-extraction engine of the Pinnacle Intelligence System, an internal tool for \
Pinnacle Estate Surveyors (a real estate valuation, construction cost and housing advisory firm).

Read the professional report provided and extract structured data for the firm's market database.

Categories:
- "valuation": capital or rental valuations. Capture: purpose; the property valued (a short description); \
the basis of value (e.g. Market Value, Forced Sale Value); assumptions and special assumptions; the valuer's identity \
(name, qualifications, firm); the opinion of value (capital_value, and annual_rent for rental valuations); method, \
yield; and EVERY comparable listed (address, date, price or rent, size, price per m²).
- For valuations also capture the report sections where stated: property (name/number, owner, use/occupancy, \
highest and best use); location (address, accessibility, land size, GPS coordinates); improvements (building type, \
accommodation, construction details, services, floor areas, ancillary structures); title (ownership, tenure, source \
of title, unexpired term, planning status); valuation (date of inspection, date of valuation = record_date, method, \
market value = capital_value, forced_sale_value); marketability ratings (one scale: "Excellent / Very good" for a report's "very good" or "excellent" (the same grade), then Good, Fair, Poor; never turn very good into Good); and assumptions (encumbrances, legal \
disclaimer, inspection limitations, purpose restriction, valuation assumptions = assumptions). Keep text concise.
- Address details: the Ghana Post GPS digital address (gps_address: only a code like "AK-039-5028", never latitude/longitude, which belong in gps_coordinates), location, MMDA \
(district), region, property type and client.
- "cost": construction cost data. Capture total cost, cost per m² of gross floor area, and the elemental \
breakdown (preliminaries, substructure, superstructure, finishes, services, external works, contingency, other).
- "housing": residential market data. Capture average prices, average monthly rents, supply in units, demand \
level (low / moderate / high), affordability ratios and housing stock notes.

Rules:
- Produce one record per distinct subject property, cost project or market area. Most reports have one.
- Use null (or leave the field out) for anything not stated. Never invent or estimate figures that are not \
in the document.
- "location" is the neighbourhood / suburb (e.g. "East Legon Hills"); "city" is the city or town (e.g. "Accra"); \
"region" is the administrative region without the word "Region" (e.g. "Greater Accra"); \
"district" is the Metropolitan, Municipal or District Assembly (MMDA) the property falls under, without the word \
"Assembly" (e.g. "La Nkwantanang-Madina Municipal"). Give the MMDA only when the report states it or the location \
makes it certain; otherwise leave it null.
- property_type describes the subject as built: a house, mansion, villa or flats on a plot is "residential", \
not "land". Use "land" only for bare / undeveloped land. Put the sub-type and bedroom count in tags \
(e.g. "mansion", "5-bedroom").
- Convert sizes to square metres (1 sq ft = 0.092903 m²). Express rent as annual rent in annual_rent and as \
monthly rent in avg_monthly_rent only for housing records.
- Amounts are plain numbers (no symbols or commas); put the currency's ISO code in currency \
(Ghana cedi = GHS). Default to {currency} if the report does not state one.
- property_type is one of: residential, commercial, industrial, land, mixed-use, hospitality, other.
- Add up to 6 short lowercase tags useful for search (e.g. "gated community", "mortgage valuation").
"""

COMMENTARY_SYSTEM = """You are a senior chartered surveyor at Pinnacle Estate Surveyors drafting market commentary \
for inclusion in a client report. Write in British English, in the measured, professional register of a valuation \
report. Base every statement strictly on the statistics provided; do not introduce outside figures. Where the \
evidence is thin (few records), say so plainly. Do not use bullet points or headings; write 2-4 paragraphs of prose. \
The surveyor will review and edit your draft before use."""


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=3, timeout=300)


class AIError(RuntimeError):
    pass


def _call(system: str, content: list, *, max_tokens: int, output_config: dict) -> str:
    try:
        response = _client().beta.messages.create(
            model=settings.claude_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config=output_config,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("Claude API credentials are invalid") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Claude API rate limit reached; try again shortly") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Could not reach the Claude API") from exc

    if response.stop_reason == "refusal":
        raise AIError("Claude declined to process this document")
    if response.stop_reason == "max_tokens":
        raise AIError("Claude's response was cut off (document too large); try splitting it")
    text = "".join(b.text for b in response.content if b.type == "text")
    if not text:
        raise AIError("Claude returned an empty response")
    return text


def _with_mmda_list(system: str) -> str:
    from .places import ALL_MMDAS
    return (system + "\n\nWhen you fill \"district\" (the MMDA), use exactly one of these official names, or null "
            "if the report does not make it clear:\n" + "; ".join(ALL_MMDAS))


def clean_extraction(data: dict) -> dict:
    """Drop empty values so the review form shows blanks instead of nulls, '' or 0."""
    for rec in data.get("records", []) or []:
        if isinstance(rec.get("cost_breakdown"), dict):
            rec["cost_breakdown"] = {k: v for k, v in rec["cost_breakdown"].items() if v not in (None, 0, "")} or None
        if isinstance(rec.get("cost_items"), list):
            rec["cost_items"] = [{k: v for k, v in c.items() if v not in (None, "")}
                                 for c in rec["cost_items"] if isinstance(c, dict) and any(c.values())] or None
        if isinstance(rec.get("comparables"), list):
            rec["comparables"] = [{k: v for k, v in c.items() if v not in (None, "")}
                                  for c in rec["comparables"] if isinstance(c, dict) and any(c.values())] or None
        for key in [k for k, v in rec.items() if v in (None, "", []) or (v == 0 and k != "category")]:
            del rec[key]
    return data


def commentary_prompt(stats: dict, scope: str, tone: str) -> str:
    length = "two short paragraphs" if tone == "concise" else "three to four paragraphs"
    return (
        f"Scope of the analysis: {scope}\n\n"
        f"Statistics from the firm's database (JSON):\n{json.dumps(stats, indent=2, default=str)}\n\n"
        f"Draft market commentary of {length} covering values/rents/yields, construction costs and housing "
        "conditions as far as the data supports, with trends over time where available."
    )


def provider() -> str | None:
    """'gemini', 'claude' or None, from PIS_AI_PROVIDER or whichever API key is set."""
    return settings.ai_provider


def extract_report(path: Path, file_type: str, text: str, default_currency: str) -> dict:
    """Extract structured data from a report with the configured AI provider."""
    if provider() == "gemini":
        from . import gemini
        return gemini.extract_report(path, file_type, text, default_currency)
    return _claude_extract(path, file_type, text, default_currency)


def market_commentary(stats: dict, scope: str, tone: str) -> str:
    prompt = commentary_prompt(stats, scope, tone)
    if provider() == "gemini":
        from . import gemini
        return gemini.market_commentary(prompt)
    return _call(COMMENTARY_SYSTEM, [{"type": "text", "text": prompt}], max_tokens=8000,
                 output_config={"effort": "medium"}).strip()


def _claude_extract(path: Path, file_type: str, text: str, default_currency: str) -> dict:
    """Extract structured data from a report using Claude. Returns the draft dict."""
    content: list = []
    size = path.stat().st_size if path.exists() else 0
    if file_type == "pdf" and size and size <= MAX_INLINE_BYTES:
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.standard_b64encode(path.read_bytes()).decode("ascii")},
        })
    elif file_type == "image" and size and size <= 5 * 1024 * 1024 and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()]
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media,
                       "data": base64.standard_b64encode(path.read_bytes()).decode("ascii")},
        })
    else:
        if not text.strip():
            raise AIError("No readable text found in the document")
        if len(text) > MAX_TEXT_CHARS:
            raise AIError("Document text is too long for a single extraction; split it into smaller files")
        content.append({"type": "text", "text": f"<report filename=\"{path.name}\">\n{text}\n</report>"})
    content.append({"type": "text", "text": "Extract the structured data from this report."})

    raw = _call(
        _with_mmda_list(EXTRACTION_SYSTEM.replace("{currency}", default_currency)),
        content,
        max_tokens=16000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIError("Claude returned malformed JSON") from exc
    return clean_extraction(data)
