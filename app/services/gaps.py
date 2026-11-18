"""Fill gaps in a record from information it (or its report) already contains.

Only blank fields are filled; nothing a surveyor or the report supplied is
overwritten. Professional judgements (ratings, highest and best use, values)
are never invented. Every filled field carries the reason, so the surveyor
can check it.
"""
from __future__ import annotations

import logging

from . import places, rules

log = logging.getLogger(__name__)

# Assemblies whose area is described as being in Accra / Tema.
_ACCRA = {"Accra Metropolitan", "Ablekuma Central Municipal", "Ablekuma North Municipal", "Ablekuma West Municipal",
          "Ayawaso Central Municipal", "Ayawaso East Municipal", "Ayawaso North Municipal", "Ayawaso West Municipal",
          "Korle Klottey Municipal", "La Dade-Kotopon Municipal", "Ledzokuku Municipal", "Krowor Municipal",
          "Okaikwei North Municipal", "Adentan Municipal", "La Nkwantanang-Madina Municipal", "Ga East Municipal",
          "Ga West Municipal", "Ga North Municipal", "Ga Central Municipal", "Ga South Municipal",
          "Weija-Gbawe Municipal"}
_TEMA = {"Tema Metropolitan", "Tema West Municipal", "Kpone-Katamanso Municipal"}
_KUMASI = {"Kumasi Metropolitan", "Asokwa Municipal", "Kwadaso Municipal", "Oforikrom Municipal",
           "Old Tafo Municipal", "Suame Municipal", "Asokore Mampong Municipal", "Subin Municipal"}

# Fields never filled automatically: they are the surveyor's professional opinion.
JUDGEMENT = {"capital_value", "forced_sale_value", "annual_rent", "location_rating", "accessibility_rating",
             "state_of_repair", "user_conformity", "neighbourhood_rating", "overall_marketability",
             "highest_best_use", "basis_of_value", "purpose", "method", "valuer", "client"}


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or v == [] or v == {}


def city_for_mmda(mmda: str | None) -> str | None:
    if not mmda:
        return None
    if mmda in _ACCRA:
        return "Accra"
    if mmda in _TEMA:
        return "Tema"
    if mmda in _KUMASI:
        return "Kumasi"
    for name, _region, capital, _aliases in places._MMDAS:
        if name == mmda and capital:
            return capital
    return None


def infer(rec: dict, text: str | None = None) -> dict[str, tuple[object, str]]:
    """{field: (value, reason)} for blank fields that can be filled. ``rec`` is not changed."""
    work = dict(rec)
    filled: dict[str, tuple[object, str]] = {}

    def fill(field, value, reason, judgement_ok=False):
        if _blank(value) or not _blank(work.get(field)):
            return
        if field in JUDGEMENT and not judgement_ok:
            return
        work[field] = value
        filled[field] = (value, reason)

    category = work.get("category") or "valuation"

    # 1. Anything the report text states that is still blank (the report's own words).
    if text and text.strip():
        try:
            from_report = rules.extract(text)["records"][0]
        except Exception:  # never let a parsing problem stop gap filling
            log.exception("rule extraction failed during gap filling")
            from_report = {}
        for field, value in from_report.items():
            if field in ("category", "currency", "title") or field.endswith("_per_sqm") or field == "yield_pct":
                continue
            if from_report.get("category") != category and field in ("capital_value", "total_cost", "avg_price"):
                continue
            fill(field, value, "from the report text", judgement_ok=True)

    # 2. Places: MMDA from the location, region and city from the MMDA.
    place = places.resolve(work.get("location"), work.get("address"), work.get("city"), work.get("region"),
                           text or "", work.get("district"))
    fill("district", place.get("district"), "from the location")
    fill("city", place.get("city"), "from the location")
    fill("region", place.get("region"), "from the location")
    fill("location", place.get("location"), "from the address")
    if work.get("district"):
        fill("region", places.MMDA_REGION.get(work["district"]), f"from the MMDA ({work['district']})")
        fill("city", city_for_mmda(work["district"]), f"from the MMDA ({work['district']})")

    # 3. Digital address written inside the address.
    for source in ("address", "property_name", "location"):
        if work.get(source):
            fill("gps_address", rules.find_gps(f"GPS address: {work[source]}"), f"from the {source.replace('_', ' ')}")

    # 4. Property type and tags from the building description.
    building = " ".join(str(work.get(k) or "") for k in
                        ("building_type", "use_occupancy", "accommodation", "property_valued", "property_name"))
    if building.strip():
        fill("property_type", rules.property_type(building), "from the building description")
        tags = rules.property_tags(building)
        fill("tags", tags or None, "from the building description")

    # 5. Simple calculations.
    def num(k):
        try:
            return float(work[k]) if not _blank(work.get(k)) else None
        except (TypeError, ValueError):
            return None

    size, value, rent, cost = num("size_sqm"), num("capital_value"), num("annual_rent"), num("total_cost")
    if size:
        if value:
            fill("value_per_sqm", round(value / size, 2), "calculated: market value ÷ floor area")
        if rent:
            fill("rent_per_sqm", round(rent / size, 2), "calculated: rent ÷ floor area")
        if cost:
            fill("cost_per_sqm", round(cost / size, 2), "calculated: total cost ÷ floor area")
    if value and rent:
        fill("yield_pct", round(rent / value * 100, 2), "calculated: rent ÷ value")
    if category == "valuation" and (value or rent):
        fill("valuation_basis", "both" if value and rent else "capital" if value else "rental", "from the figures given")

    # 6. Short summary of the property valued.
    if category == "valuation" and _blank(work.get("property_valued")) and work.get("building_type"):
        where = work.get("location") or work.get("address")
        fill("property_valued", f"{work['building_type']}{' at ' + where if where else ''}",
             "summarised from building type and location")

    fill("currency", "GHS" if _blank(work.get("currency")) else None, "default currency")
    return filled


def apply(rec: dict, filled: dict[str, tuple[object, str]]) -> dict:
    out = dict(rec)
    for field, (value, _reason) in filled.items():
        out[field] = value
    return out
