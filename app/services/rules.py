"""Rule-based extraction: the offline fallback when Claude is not configured.

It is deliberately conservative: it fills fields it can find with clear labels and
leaves the rest blank for the surveyor to complete during review.
"""
from __future__ import annotations

import re
from datetime import date, datetime

PROPERTY_TYPES = [
    ("residential", ["residential", "house", "apartment", "flat", "townhouse", "bungalow", "villa", "duplex", "dwelling"]),
    ("commercial", ["commercial", "office", "retail", "shop", "mall", "shopping"]),
    ("industrial", ["industrial", "warehouse", "factory", "logistics"]),
    ("land", ["land", "plot", "acre", "hectare"]),
    ("mixed-use", ["mixed-use", "mixed use"]),
    ("hospitality", ["hotel", "guest house", "hospitality", "lodge"]),
]

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


def classify(text: str) -> str:
    low = text.lower()
    scores = {cat: sum(low.count(k) for k in kws) for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "valuation"


def property_type(text: str) -> str | None:
    low = text.lower()
    scores = {name: sum(low.count(k) for k in kws) for name, kws in PROPERTY_TYPES}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


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
    record["address"] = _labelled(text, ["property address", "address", "subject property", "site"])
    record["location"] = _labelled(text, ["location", "area", "neighbourhood", "neighborhood", "suburb", "district"])
    record["city"] = _labelled(text, ["city", "town", "municipality"])
    record["region"] = _labelled(text, ["region", "state", "province"])
    record["client"] = _labelled(text, ["client", "prepared for", "instructed by"])
    record["purpose"] = _labelled(text, ["purpose of valuation", "purpose", "basis of instruction"])
    record["property_type"] = property_type(text)
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
        record["assumptions"] = _labelled(text, ["assumptions", "special assumptions"])
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
