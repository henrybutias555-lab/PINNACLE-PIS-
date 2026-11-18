"""Rule-based extraction: the offline fallback when no AI provider is configured.

It is deliberately conservative: it fills fields it can find with clear labels and
leaves the rest blank for the surveyor to complete during review.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from . import places

# Words that indicate a building of each type (whole-word, case-insensitive).
PROPERTY_TYPES = {
    "residential": ["residential", "house", "houses", "mansion", "villa", "bungalow", "duplex", "townhouse",
                    "town house", "apartment", "apartments", "flats", "dwelling", "residence", "bedroom",
                    "bedrooms", "bed room", "boys quarters", "boys' quarters", "master bedroom", "en-suite",
                    "ensuite", "living room", "detached", "semi-detached", "storey building", "family home"],
    "commercial": ["commercial", "office", "offices", "office block", "retail", "shop", "shops", "mall",
                   "shopping", "showroom", "plaza"],
    "industrial": ["industrial", "warehouse", "warehouses", "factory", "logistics"],
    "hospitality": ["hotel", "guest house", "guesthouse", "hospitality", "lodge", "resort"],
    "mixed-use": ["mixed-use", "mixed use"],
}
# Phrases that mean the subject is land only (no building).
LAND_ONLY = ["bare land", "vacant land", "undeveloped land", "vacant plot", "undeveloped plot",
             "serviced plot", "land only", "empty plot", "parcel of vacant land"]
LAND_WORDS = ["land", "plot", "plots", "acre", "acres", "hectare", "hectares"]
# Sub-types worth recording as search tags (first match wins).
SUBTYPES = ["mansion", "villa", "semi-detached house", "detached house", "townhouse", "bungalow", "duplex",
            "penthouse", "apartment", "storey building", "office block", "warehouse", "hotel", "guest house"]

CATEGORY_KEYWORDS = {
    "valuation": ["valuation", "market value", "open market", "capital value", "rental value", "yield",
                  "comparable", "forced sale", "investment method", "valuer"],
    "cost": ["construction cost", "bill of quantities", "boq", "cost per", "preliminaries", "substructure",
             "superstructure", "finishes", "external works", "quantity surveyor", "cost plan", "estimate"],
    "housing": ["housing", "housing market", "affordability", "housing stock", "supply", "demand", "household",
                "housing deficit", "rent-to-income", "house prices"],
}

CURRENCY = r"(?:GH[S¢₵]|GHC|₵|USD|US\$|\$|NGN|₦|GBP|£|EUR|€)"
AMOUNT = r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|m|bn|billion|k)?"

_MONTHS = "January February March April May June July August September October November December".split()


def _num(raw: str, scale: str | None = None) -> float | None:
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    scale = (scale or "").lower()
    if scale in ("million", "m"):
        value *= 1_000_000
    elif scale in ("billion", "bn"):
        value *= 1_000_000_000
    elif scale == "k":
        value *= 1_000
    return value


def _currency_code(sym: str) -> str:
    s = sym.upper()
    if s in ("GHS", "GH¢", "GH₵", "GHC", "₵"):
        return "GHS"
    if s in ("USD", "US$", "$"):
        return "USD"
    if s in ("NGN", "₦"):
        return "NGN"
    if s in ("GBP", "£"):
        return "GBP"
    if s in ("EUR", "€"):
        return "EUR"
    return s


GPS_RE = re.compile(r"\b([A-Z]{2})[-\s](\d{3,4})[-\s](\d{4})\b")

BASES_OF_VALUE = ["Market Value", "Market Rent", "Forced Sale Value", "Fair Value", "Investment Value",
                  "Reinstatement Cost", "Depreciated Replacement Cost", "Mortgage Lending Value"]


def find_gps(text: str) -> str | None:
    """Ghana Post GPS digital address, e.g. GA-183-8164."""
    labelled = _labelled(text, ["gps address", "digital address", "ghana post gps", "gps"])
    for source in ([labelled] if labelled else []) + [text]:
        m = GPS_RE.search(source.upper())
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def find_basis_of_value(text: str) -> str | None:
    labelled = _labelled(text, ["basis of value", "basis of valuation", "bases of value"])
    if labelled:
        return labelled[:160]
    low = text.lower()
    found = [b for b in BASES_OF_VALUE if b.lower() in low]
    return " & ".join(found[:2]) or None


def classify(text: str) -> str:
    low = text.lower()
    scores = {cat: sum(low.count(k) for k in kws) for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "valuation"


def _count(words: list[str], low: str) -> int:
    return sum(len(re.findall(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low)) for w in words)


def property_type(text: str) -> str | None:
    """Classify the subject property. A report that mentions bedrooms, a house or a
    mansion is residential even though it also talks about the land it sits on."""
    labelled = _labelled(text, ["property type", "type of property", "property description", "description"])
    for source in ([labelled] if labelled else []) + [text]:
        low = source.lower()
        scores = {name: _count(words, low) for name, words in PROPERTY_TYPES.items()}
        best = max(scores, key=scores.get)
        if _count(LAND_ONLY, low) and scores[best] < 2:
            return "land"
        if scores[best] > 0:
            return best
        if source is labelled and _count(LAND_WORDS, low):
            return "land"
    return "land" if _count(LAND_WORDS, text.lower()) else None


def property_tags(text: str) -> list[str]:
    """Sub-type and bedroom tags, e.g. ['mansion', '5-bedroom']."""
    low = text.lower()
    tags = []
    for sub in SUBTYPES:
        if re.search(rf"(?<![a-z]){re.escape(sub)}", low):
            tags.append(sub)
            break
    m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)[\s-]*(?:no\.?\s*)?bed(?:room|[\s-]room)s?\b", low)
    if m:
        words = "one two three four five six seven eight nine ten".split()
        n = m.group(1)
        n = str(words.index(n) + 1) if n in words else n
        tags.append(f"{n}-bedroom")
    return tags


def _labelled(text: str, labels: list[str]) -> str | None:
    for label in labels:
        m = re.search(rf"(?im)^\s*{label}\s*[:\-–]\s*(.+)$", text)
        if m:
            return m.group(1).strip()[:300]
    return None


def _labelled_amount(text: str, labels: list[str]) -> tuple[float | None, str | None]:
    for label in labels:
        m = re.search(rf"(?i){label}[^0-9\n]{{0,40}}?({CURRENCY})?\s*{AMOUNT}", text)
        if m:
            value = _num(m.group(2), m.group(3))
            if value is not None:
                return value, (_currency_code(m.group(1)) if m.group(1) else None)
    return None, None


def find_date(text: str) -> date | None:
    labelled = _labelled(text, ["date of valuation", "valuation date", "date of inspection", "report date", "date"])
    candidates = [labelled] if labelled else []
    candidates.append(text[:4000])
    for chunk in candidates:
        if not chunk:
            continue
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", chunk)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
        m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r"),?\s+(\d{4})\b", chunk, re.I)
        if m:
            return datetime.strptime(f"{m.group(1)} {m.group(2).title()} {m.group(3)}", "%d %B %Y").date()
        m = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", chunk, re.I)
        if m:
            return datetime.strptime(f"{m.group(2)} {m.group(1).title()} {m.group(3)}", "%d %B %Y").date()
        m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", chunk)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass
    return None


def _size(text: str) -> float | None:
    m = re.search(r"(?i)(?:gross floor area|floor area|gfa|land area|site area|size|area)[^0-9\n]{0,30}([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:sq\.?\s?m|m²|m2|square met)", text)
    if m:
        return _num(m.group(1))
    m = re.search(r"(?i)(?:gross floor area|floor area|gfa|size|area)[^0-9\n]{0,30}([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:sq\.?\s?ft|ft²|square feet)", text)
    if m:
        value = _num(m.group(1))
        return round(value * 0.092903, 2) if value else None
    return None


def _first_currency(text: str) -> str | None:
    m = re.search(CURRENCY, text)
    return _currency_code(m.group(0)) if m else None


def extract(text: str, default_currency: str = "GHS") -> dict:
    """Return a draft dict: {report_type, title, summary, records: [..]}."""
    category = classify(text)
    record: dict = {"category": category}
    record["title"] = _labelled(text, ["title", "subject", "re", "property"]) or None
    record["address"] = _labelled(text, ["property address", "address", "site"])
    record["gps_address"] = find_gps(text)
    record["location"] = _labelled(text, ["location", "area", "neighbourhood", "neighborhood", "suburb", "district"])
    record["district"] = _labelled(text, ["mmda", "district assembly", "municipal assembly", "metropolitan assembly",
                                          "assembly", "local authority", "district"])
    record["city"] = _labelled(text, ["city", "town"])
    record["region"] = _labelled(text, ["region", "state", "province"])
    record["client"] = _labelled(text, ["client", "prepared for", "instructed by"])
    record["purpose"] = _labelled(text, ["purpose of valuation", "purpose", "basis of instruction"])
    if record.get("location") and re.match(r"\s*[\d.,]+", record["location"]):
        record["location"] = None  # e.g. "Area: 0.25 acres" is a size, not a place
    place = places.resolve(record.get("location"), record.get("address"), record.get("city"),
                           record.get("region"), text, record.get("district"))
    record.update({k: v for k, v in place.items() if v})
    record["property_type"] = property_type(text)
    tags = property_tags(text)
    if tags:
        record["tags"] = tags
    d = find_date(text)
    record["record_date"] = d.isoformat() if d else None
    record["size_sqm"] = _size(text)
    record["currency"] = _first_currency(text) or default_currency
    cur = None

    if category == "valuation":
        record["capital_value"], cur = _labelled_amount(text, ["open market value", "market value", "capital value", "value of the property", "valued at", "valuation figure"])
        record["annual_rent"], _ = _labelled_amount(text, ["annual rent", "rental value per annum", "rent per annum", "market rent", "rental value"])
        m = re.search(r"(?i)(?:yield|capitali[sz]ation rate|cap rate)[^0-9\n]{0,30}([0-9]+(?:\.[0-9]+)?)\s*%", text)
        record["yield_pct"] = float(m.group(1)) if m else None
        m = re.search(r"(?i)(comparison|comparative|investment|income|residual|cost|depreciated replacement cost|profits?)\s+(?:method|approach)", text)
        record["method"] = m.group(0).strip().title() if m else None
        low = text.lower()
        if record["capital_value"] and record["annual_rent"]:
            record["valuation_basis"] = "both"
        elif record["annual_rent"] and not record["capital_value"]:
            record["valuation_basis"] = "rental"
        elif "rental valuation" in low:
            record["valuation_basis"] = "rental"
        else:
            record["valuation_basis"] = "capital"
        general = _labelled(text, ["assumptions"])
        special = _labelled(text, ["special assumptions"])
        record["assumptions"] = "; ".join(x for x in (general, f"Special: {special}" if special else None) if x) or None
        record["property_valued"] = _labelled(text, ["property valued", "subject property", "property description",
                                                     "description of property", "description of the property"])
        record["basis_of_value"] = find_basis_of_value(text)
        record["valuer"] = _labelled(text, ["valuer", "valued by", "prepared by", "signed by", "reporting valuer"])
    elif category == "cost":
        record["total_cost"], cur = _labelled_amount(text, ["total construction cost", "total cost", "contract sum", "estimated cost", "total"])
        record["cost_per_sqm"], c2 = _labelled_amount(text, ["cost per square met", "cost per sq", "cost/m", "rate per m", "per m2", "per m²"])
        cur = cur or c2
        breakdown = {}
        for key, labels in {
            "preliminaries": ["preliminaries"],
            "substructure": ["substructure"],
            "superstructure": ["superstructure", "structure"],
            "finishes": ["finishes"],
            "services": ["services", "m&e", "mechanical"],
            "external_works": ["external works"],
            "contingency": ["contingenc"],
        }.items():
            value, _ = _labelled_amount(text, labels)
            if value is not None:
                breakdown[key] = value
        record["cost_breakdown"] = breakdown or None
        record["specification"] = _labelled(text, ["specification", "spec", "standard"])
    else:  # housing
        record["avg_price"], cur = _labelled_amount(text, ["average house price", "average price", "median price", "average sale price"])
        record["avg_monthly_rent"], _ = _labelled_amount(text, ["average monthly rent", "average rent", "median rent", "monthly rent"])
        m = re.search(r"(?i)(?:supply|new units|units completed|housing stock)[^0-9\n]{0,30}([0-9][0-9,]*)\s*(?:units|houses|homes|dwellings)?", text)
        record["supply_units"] = int(_num(m.group(1)) or 0) if m else None
        m = re.search(r"(?i)demand[^.\n]{0,40}\b(very high|high|strong|moderate|low|weak)\b", text)
        record["demand_level"] = m.group(1).lower() if m else None
        m = re.search(r"(?i)(?:affordability ratio|price[- ]to[- ]income|rent[- ]to[- ]income)[^0-9\n]{0,30}([0-9]+(?:\.[0-9]+)?)", text)
        record["affordability_ratio"] = float(m.group(1)) if m else None

    if cur:
        record["currency"] = cur
    size = record.get("size_sqm")
    if size:
        if record.get("capital_value") and not record.get("value_per_sqm"):
            record["value_per_sqm"] = round(record["capital_value"] / size, 2)
        if record.get("annual_rent"):
            record["rent_per_sqm"] = round(record["annual_rent"] / size, 2)
        if record.get("total_cost") and not record.get("cost_per_sqm"):
            record["cost_per_sqm"] = round(record["total_cost"] / size, 2)
    if record.get("capital_value") and record.get("annual_rent") and not record.get("yield_pct"):
        record["yield_pct"] = round(record["annual_rent"] / record["capital_value"] * 100, 2)

    record = {k: v for k, v in record.items() if v not in (None, "", {})}
    record.setdefault("category", category)
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return {
        "report_type": category,
        "title": record.get("title") or first_line[:200] or None,
        "summary": None,
        "records": [record],
    }
