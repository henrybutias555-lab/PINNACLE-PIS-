"""Read a valuation report the way a surveyor would, without AI.

Three passes, each only filling fields that are still blank:

1. Sections: numbered / capitalised headings ("5.0 LOCATION AND NEIGHBOURHOOD",
   "TENURE", "METHOD OF VALUATION") are matched to fields and the text under
   them is used.
2. Sentences: typical valuation wording anywhere in the report ("registered in
   the name of ...", "instructed by ...", "inspected on ...", "a leasehold
   interest of 99 years from 2010", "measuring approximately 0.10 acre",
   signature block with MGhIS, ...).
3. Small conversions: unexpired term from lease start + term, coordinates in
   degrees/minutes/seconds, standard names for methods and purposes.
"""
from __future__ import annotations

import re
from datetime import date

# ------------------------------------------------------------------ sections

# heading words -> fields the section can fill (first match wins per field)
SECTION_FIELDS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("purpose of valuation", "purpose of the valuation", "purpose", "terms of reference"), ("purpose",)),
    (("basis of valuation", "basis of value", "bases of value", "basis"), ("basis_of_value",)),
    (("date of inspection", "inspection date", "inspection"), ("inspection_date",)),
    (("date of valuation", "valuation date", "effective date"), ("record_date",)),
    (("client", "instruction", "instructions", "addressee"), ("client",)),
    (("location and neighbourhood", "location & neighbourhood", "location", "situation", "neighbourhood",
      "neighborhood", "locality"), ("location_text",)),
    (("accessibility", "access"), ("accessibility",)),
    (("site", "the site", "land", "plot", "site description", "site and land", "land area", "site area"), ("site_text",)),
    (("tenure and title", "title and tenure", "tenure", "title", "interest valued", "legal title", "ownership"),
     ("title_text",)),
    (("description of property", "property description", "description of the property", "description",
      "the property", "subject property", "improvements", "buildings", "the buildings", "building"), ("building_text",)),
    (("accommodation", "accommodation details", "rooms"), ("accommodation",)),
    (("construction", "construction details", "construction and finishes", "specification", "finishes",
      "construction and specification"), ("construction_details",)),
    (("services", "utilities", "services and utilities", "amenities"), ("services",)),
    (("floor area", "floor areas", "measurements", "gross floor area", "areas"), ("floor_areas",)),
    (("ancillary", "ancillary structures", "outbuildings", "external works", "site improvements"),
     ("ancillary_structures",)),
    (("condition", "state of repair", "repairs", "condition and repair", "general condition"), ("state_of_repair_text",)),
    (("planning", "town planning", "zoning", "planning and zoning", "statutory", "permits"), ("planning_status",)),
    (("method of valuation", "valuation method", "valuation methodology", "methodology", "approach to valuation",
      "valuation approach", "method"), ("method_text",)),
    (("valuation", "opinion of value", "market value", "valuation figure", "our opinion", "conclusion", "valuation opinion"),
     ("value_text",)),
    (("highest and best use", "highest & best use"), ("highest_best_use",)),
    (("occupation", "occupancy", "current use", "use and occupation", "existing use"), ("use_occupancy",)),
    (("marketability", "market comment", "market commentary"), ("marketability_text",)),
    (("encumbrances", "encumbrance", "charges and encumbrances"), ("encumbrances",)),
    (("special assumptions", "assumptions", "general assumptions", "assumptions and limiting conditions",
      "limiting conditions", "caveats", "conditions"), ("assumptions",)),
    (("disclaimer", "legal disclaimer"), ("legal_disclaimer",)),
    (("limitation", "limitations", "inspection limitation", "scope of inspection", "limitation of inspection"),
     ("inspection_limitation",)),
    (("restriction", "restriction on use", "reliance", "confidentiality", "purpose restriction",
      "publication", "third party"), ("purpose_restriction",)),
]

_NUMBERING = r"(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[A-Z]\.|[IVX]{1,5}\.|\(\w\))"
_HEADING_RE = re.compile(rf"^\s*(?:{_NUMBERING}\s+)?(?P<title>[A-Za-z][A-Za-z &/,'’()-]{{2,70}}?)\s*:?\s*$")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" .:;-–")


def _is_heading(line: str) -> str | None:
    """The heading text if ``line`` looks like a report heading, else None."""
    raw = line.strip()
    if not raw or len(raw) > 80 or raw.endswith((".", ",", ";")) or "|" in raw:
        return None
    m = _HEADING_RE.match(raw)
    if not m:
        return None
    title = m.group("title").strip()
    letters = [c for c in title if c.isalpha()]
    numbered = bool(re.match(rf"\s*{_NUMBERING}\s+", raw))
    upper = letters and sum(c.isupper() for c in letters) / len(letters) > 0.7
    if numbered or upper or raw.endswith(":"):
        return title.lower()
    return None


def sections(text: str) -> list[tuple[str, str]]:
    """[(heading, body)] in document order."""
    out, current, body = [], None, []
    for line in text.splitlines():
        title = _is_heading(line)
        if title:
            if current is not None:
                out.append((current, "\n".join(body).strip()))
            current, body = title, []
        elif current is not None:
            body.append(line)
    if current is not None:
        out.append((current, "\n".join(body).strip()))
    return [(h, b) for h, b in out if b]


def _section_map(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    secs = sections(text)
    for words, fields in SECTION_FIELDS:
        for heading, body in secs:
            h = re.sub(r"[^a-z& ]", " ", heading).strip()
            if any(h == w or h.startswith(w + " ") or h.endswith(" " + w) for w in words):
                for f in fields:
                    found.setdefault(f, body)
                break
    return found


def _first_sentences(body: str, limit: int = 600) -> str:
    flat = _clean(body)
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    end = cut.rfind(". ")
    return (cut[: end + 1] if end > 120 else cut.rstrip() + "…")


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if not line.strip() or _is_heading(line) or "|" in line:
            continue
        out += [s.strip() for s in re.split(r"(?<!\bNo)(?<!\bVol)(?<!\bFol)(?<=[.;])\s+(?=[A-Z(])", line) if s.strip()]
    return out


# ------------------------------------------------------------------ standard names

# Purpose of valuation -> one short label (the report's sentence is not kept).
PURPOSES = [
    (r"\bcompulsory acquisition|\bcompensation\b", "Compulsory acquisition"),
    (r"\bmortgage|\bloans?\b|\bcollateral|\bsecurity\b|\bcredit\b|\bfacility\b|\blending\b|\bbank", "Mortgage"),
    (r"\bsale\b|\bsell(?:ing)?\b|\bdisposal\b|\bdivest", "Sale"),
    (r"\bpurchase\b|\bacquisition\b|\bbuy(?:ing)?\b|\bacquire", "Purchase"),
    (r"\binsurance\b|\breinstatement\b", "Insurance"),
    (r"\bfinancial reporting|\baccounts?\b|\bifrs\b|\bbalance sheet|\baudit\b|\basset register", "Financial reporting"),
    (r"\brent(?:al)?\b|\brent review|\blease renewal|\bletting\b", "Rental"),
    (r"\btax(?:ation)?\b|\brating\b|\bstamp duty|\bcapital gains", "Taxation"),
    (r"\blitigation\b|\bdispute\b|\bcourt\b|\barbitration\b", "Litigation"),
    (r"\bprobate\b|\bestate administration|\bsuccession\b|\binheritance\b", "Probate"),
    (r"\bpartnership\b|\bjoint venture\b|\bequity\b", "Joint venture / equity"),
]
# when no reason is given: 'to assess the capital / market value of the property'
GENERIC_PURPOSE = (r"\b(?:capital|market|current|open market) value\b|\bvalue of the (?:property|subject|premises)\b|"
                   r"\bassess(?:ing|ment)? (?:of )?the value", "Capital value assessment")

METHODS = [
    (r"(?:direct )?(?:sales )?compar(?:ison|ative)(?: method| approach)?|market approach", "Sales Comparison Approach"),
    (r"depreciated replacement cost|\bdrc\b|cost approach|contractor'?s? (?:method|basis)|replacement cost",
     "Cost Approach (DRC)"),
    (r"investment (?:method|approach)|income (?:approach|capitali[sz]ation)|capitali[sz]ation of (?:income|rent)",
     "Investment / Income Approach"),
    (r"residual (?:method|approach|valuation)", "Residual Method"),
    (r"profits? (?:method|approach)|accounts method", "Profits Method"),
]


def standard_purpose(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    hits = []
    for order, (pattern, name) in enumerate(PURPOSES):
        m = re.search(pattern, low)
        if m:
            hits.append((m.start(), order, name))
    if hits:
        return min(hits)[2]       # the reason mentioned first
    if re.search(GENERIC_PURPOSE[0], low):
        return GENERIC_PURPOSE[1]
    clean = _clean(text)
    return (clean[:60] if len(clean) <= 60 else clean.split(",")[0][:60]).strip() or None


def standard_methods(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    found = []
    for pattern, name in METHODS:
        m = re.search(pattern, low)
        if m:
            found.append((m.start(), name))
    return " & ".join(n for _, n in sorted(found)) or None


# ------------------------------------------------------------------ sentence patterns

_NAME = r"([A-Z][A-Za-z.'’&-]*(?:\s+(?:[A-Z][A-Za-z.'’&-]*|of|and|&|de|van)){0,7})"


def _name_only(s: str) -> str:
    """Cut a captured name at the end of its sentence ('Adom Properties Limited. Title' -> 'Adom Properties Limited')."""
    s = re.split(r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bProf)(?<!\bRev)(?<!\bSt)(?<!\bHon)\.\s+(?=[A-Z])", s)[0]
    return _clean(s)


def _owner(text: str) -> str | None:
    m = re.search(r"(?:registered (?:in the name of|to)|vested in|owned by|in the name of|the property of|"
                  r"belongs to|proprietor(?:ship)? (?:is|of)|registered proprietor(?: is)?)\s*:?\s*" + _NAME, text)
    return _name_only(m.group(1)) if m else None


def _client(text: str) -> str | None:
    m = re.search(r"(?:instructed by|instructions? (?:received )?from|at the (?:request|instance) of|on behalf of|"
                  r"prepared for|commissioned by|requested by)\s*:?\s*(?:the\s+)?" + _NAME, text)
    return _name_only(m.group(1)) if m else None


def _purpose(text: str) -> str | None:
    m = re.search(r"(?i)purpose of (?:this|the|our) (?:valuation|report|exercise) is (?:to|for)\s+([^.\n]{3,160})", text) \
        or re.search(r"(?i)\bfor ([a-z][a-z /&-]{2,40}?) purposes?\b", text) \
        or re.search(r"(?i)\bvaluation (?:is |was )?(?:required|requested|undertaken|carried out) (?:for|to)\s+([^.\n]{3,120})", text)
    return standard_purpose(m.group(1)) if m else None


def _inspection_date(text: str, find_date) -> str | None:
    m = re.search(r"(?i)inspect\w*[^.\n]{0,40}?\b(?:on|dated)\s+(?:the\s+)?([^.\n]{4,40})", text)
    if m:
        d = find_date(m.group(1))
        if d:
            return d.isoformat()
    return None


def _tenure(text: str, valuation_year: int | None) -> dict:
    out = {}
    low = text.lower()
    if re.search(r"\bfreehold\b", low) and not re.search(r"\bleasehold\b", low):
        out["tenure"] = "Customary freehold" if "customary" in low else "Freehold"
    elif re.search(r"\bsub-?lease\b", low):
        out["tenure"] = "Sub-lease"
    elif re.search(r"\bleasehold\b|\blease\b", low):
        out["tenure"] = "Leasehold"
    m = re.search(r"(?i)unexpired (?:term|residue|portion|period)[^0-9\n]{0,30}(\d{1,3})\s*(?:years?|yrs)", text) \
        or re.search(r"(?i)(\d{1,3})\s*(?:years?|yrs) unexpired", text)
    if m:
        out["unexpired_term"] = f"{m.group(1)} years"
    else:
        m = re.search(r"(?i)(\d{2,3})[- ]?(?:years?|yrs?)\b[^.\n]{0,60}?(?:from|commencing|with effect from|w\.e\.f\.?)"
                      r"\s*[^.\n]{0,25}?((?:19|20)\d\d)", text)
        if m and valuation_year:
            left = int(m.group(1)) - (valuation_year - int(m.group(2)))
            if 0 < left <= int(m.group(1)):
                out["unexpired_term"] = f"{left} years (calculated: {m.group(1)}-year term from {m.group(2)})"
    m = re.search(r"(?i)((?:land title certificate|land certificate|certificate of title|title certificate|"
                  r"indenture|deed of (?:assignment|gift|lease|conveyance)|lease agreement|allocation (?:letter|paper)|"
                  r"site plan)(?:[^.;\n]|\.(?=\s*\d)|(?<=No)\.|(?<=Vol)\.|(?<=Fol)\.){0,90})", text)
    if m:
        out["source_of_title"] = _clean(m.group(1))[:200]
    m = re.search(r"(?i)\b(private(?:ly owned)?|family|stool|skin|state|government|public|clan|institutional)\s+"
                  r"(?:land|ownership|property)", text)
    if m:
        out["ownership"] = m.group(1).capitalize().replace("Privately owned", "Private")
    return out


_SIZE = r"([\d.,]+\s*(?:acres?|ac\b|hectares?|ha\b|sq\.?\s?m\b|m²|m2\b|square met\w+|sq\.?\s?ft|ft²|plots?))"


def _land_size(text: str) -> str | None:
    m = re.search(rf"(?i)\b(?:plot|site|land|parcel|compound)\b[^.\n]{{0,30}}?(?:measures|measuring|is|of|covers|covering|"
                  rf"area of|size of|extent of)\s*(?:about|approximately|approx\.?|some|an area of)?\s*{_SIZE}(\s*\([^)]{{0,40}}\))?",
                  text)
    if m:
        return _clean(m.group(1) + (m.group(2) or ""))
    m = re.search(rf"(?i)(?:measuring|measures|land (?:area|size) of|site (?:area|size) of|plot (?:area|size) of|"
                  rf"covering|covers|extent of|area of|land of)\s*(?:about|approximately|approx\.?|some|an area of)?\s*"
                  rf"{_SIZE}(\s*\([^)]{{0,40}}\))?", text)
    if m and not re.search(r"(?i)(?:lettable|floor|built|gross|internal|net|usable|plinth)\s+(?:\w+\s+)?area of\s*$",
                           text[max(0, m.start() - 5):m.start() + 30][:40] + " area of"):
        before = text[max(0, m.start() - 25):m.start() + 12].lower()
        if not re.search(r"lettable|floor|built|gross|internal|usable|plinth", before):
            return _clean(m.group(1) + (m.group(2) or ""))
    return None


def _building(text: str) -> str | None:
    m = re.search(r"(?i)(?:subject property|property|it|the building|the development|the premises)\s+"
                  r"(?:is|comprises|consists of|is made up of|is developed with)\s+(?:of\s+)?(?:a|an)?\s*"
                  r"((?:(?:single|one|two|three|four|five|six|\d+)[- ](?:stor(?:e)?y|level|floor)|detached|semi-detached|"
                  r"terraced|storey|multi-storey|uncompleted|completed)[^.;,]{0,90}?"
                  r"(?:house|bungalow|building|block|apartment|flats?|duplex|villa|mansion|warehouse|office|shop|"
                  r"complex|structure|residence|dwelling|hotel|storey building)(?:\s+(?:block|building|complex|house))?)", text)
    return _clean(m.group(1))[:160] if m else None


def _accommodation(text: str) -> str | None:
    m = re.search(r"(?i)accommodation\s+(?:comprises|consists of|is made up of|include[s]?|provides)\s*:?\s*([^.]{5,300})", text)
    if m:
        return _clean(m.group(1))
    m = re.search(r"(?i)((?:\d{1,2}|one|two|three|four|five|six|seven|eight)[- ]bedroom(?:s|ed)?[^.]{0,200})", text)
    return _clean(m.group(1)) if m else None


_ASSUMPTION = re.compile(r"^\W*(?:that\b|it (?:is|has been) assumed|we (?:have )?assumed|assum)|"
                         r"not subject to any (?:unusual|onerous)|assum(?:e|ed|ing|ption)", re.I)


def _encumbrances(text: str) -> str | None:
    """Encumbrances the report states. A valuation *assumption* ('That the property is not subject to
    any unusual ... encumbrances') is not a finding: then record that none are known."""
    pattern = (r"encumbrance|free from (?:all )?(?:charges|encumbrances|disputes)|mortgage(?:d)? to|caveat|"
               r"litigation on the")
    hits = [x for x in _sentences(text) if re.search(pattern, x, re.I)]
    stated = [x for x in hits if not _ASSUMPTION.search(x)]
    if stated:
        return " ".join(stated[:2])[:500].rstrip()
    if hits:
        return "None stated (the valuation assumes no unusual or onerous encumbrances)"
    return None


def _sentences_with(text: str, pattern: str, limit: int = 3, max_len: int = 500) -> str | None:
    hits = [s for s in _sentences(text) if re.search(pattern, s, re.I)]
    # a sentence at the end of the letter can run into the sign-off
    hits = [re.split(r"(?i)\b(?:yours (?:faithfully|sincerely|truly)|for and on behalf of)\b", h)[0].strip(" ,;") for h in hits]
    hits = [h for h in hits if h]
    out = " ".join(hits[:limit])
    return out[:max_len].rstrip() if out else None


def _valuer(text: str) -> str | None:
    m = re.search(r"(?m)^\s*" + _NAME + r"\s*[,(]?\s*((?:B\.?Sc|M\.?Sc|MPhil|FGhIS|MGhIS|AGhIS|FRICS|MRICS|AssocRICS|"
                  r"Dip|PhD|Chartered)[^\n]{0,80})", text)
    if m:
        name, quals = _clean(m.group(1)), _clean(m.group(2))
        if len(name.split()) >= 2:
            return f"{name}, {quals}"[:200]
    m = re.search(r"(?i)(?:yours faithfully|signed|for and on behalf of)[\s,]*\n+\s*(?:\S[^\n]*\n\s*){0,2}?"
                  + _NAME + r"\s*\n", text)
    if m and len(m.group(1).split()) >= 2:
        return _clean(m.group(1))[:200]
    return None


def _dms(text: str) -> str | None:
    """5°38'22.6"N 0°10'03.1"W -> '5.639611, -0.167528'."""
    m = re.search(r"(\d{1,2})\s*°\s*(\d{1,2})\s*['’′]\s*([\d.]+)?\s*[\"”″]?\s*([NS])[\s,;/]+"
                  r"(\d{1,3})\s*°\s*(\d{1,2})\s*['’′]\s*([\d.]+)?\s*[\"”″]?\s*([EW])", text)
    if not m:
        return None
    lat = int(m.group(1)) + int(m.group(2)) / 60 + float(m.group(3) or 0) / 3600
    lon = int(m.group(5)) + int(m.group(6)) / 60 + float(m.group(7) or 0) / 3600
    lat = -lat if m.group(4) == "S" else lat
    lon = -lon if m.group(8) == "W" else lon
    return f"{lat:.6f}, {lon:.6f}"


# ------------------------------------------------------------------ main entry

def read_report(text: str, record: dict, helpers) -> dict[str, object]:
    """Values for blank fields of ``record`` found in ``text``. ``helpers`` gives rules functions
    (find_date, money_in, normalise_basis, normalise_rating, property_tags, find_coordinates)."""
    if not text or not text.strip():
        return {}
    found: dict[str, object] = {}

    def put(field, value):
        if value in (None, "", [], {}) or record.get(field) not in (None, "", [], {}) or field in found:
            return
        found[field] = value

    sec = _section_map(text)
    year = None
    if record.get("record_date"):
        year = int(str(record["record_date"])[:4])

    # ---- sections first (the report's own structure)
    if "purpose" in sec:
        put("purpose", standard_purpose(sec["purpose"]))
    if "basis_of_value" in sec:
        put("basis_of_value", helpers.normalise_basis(_first_sentences(sec["basis_of_value"], 160)))
    if "inspection_date" in sec:
        put("inspection_date", _inspection_date(sec["inspection_date"], helpers.find_date))
    for key, field in (("inspection_date", "inspection_date"), ("record_date", "record_date")):
        if key in sec:
            body = sec[key]
            if field == "inspection_date":   # "inspected on ..." first; a shared heading also holds the valuation date
                body = re.sub(r"(?i)(?:valuation|value)[^.]*?\bas (?:at|of)\b[^.]*", "", body)
            d = helpers.find_date(body)
            if d:
                put(field, d.isoformat())
                if field == "record_date":
                    year = d.year
    if "client" in sec:
        put("client", _client(sec["client"]) or _clean(sec["client"].splitlines()[0])[:120])
    if "location_text" in sec:
        body = sec["location_text"]
        put("accessibility", _sentences_with(body, r"\baccess|\broad|\bstreet|\bjunction|\bkm\b|kilomet|minutes? drive|"
                                                   r"motorway|highway|\bfrom the\b"))
        put("location_text", _first_sentences(body, 500))
    for field in ("accessibility", "accommodation", "construction_details", "services", "floor_areas",
                  "ancillary_structures", "planning_status", "highest_best_use", "use_occupancy", "encumbrances",
                  "assumptions", "legal_disclaimer", "inspection_limitation", "purpose_restriction"):
        if field in sec:
            limit = 250 if field in ("planning_status", "highest_best_use", "use_occupancy") else 700
            put(field, _first_sentences(sec[field], limit))
    if "site_text" in sec:
        put("land_size", _land_size(sec["site_text"]))
    if "title_text" in sec:
        for k, v in _tenure(sec["title_text"], year).items():
            put(k, v)
        put("owner", _owner(sec["title_text"]))
    if "building_text" in sec:
        put("building_type", _building(sec["building_text"]) or _first_sentences(sec["building_text"].split(".")[0], 160))
        put("accommodation", _accommodation(sec["building_text"]))
    if "state_of_repair_text" in sec:
        put("state_of_repair", helpers.normalise_rating(_first_sentences(sec["state_of_repair_text"], 250), "repair"))
    if "method_text" in sec:
        put("method", standard_methods(sec["method_text"]))
    if "value_text" in sec and record.get("category", "valuation") == "valuation":
        value, cur = helpers.money_in_labelled(sec["value_text"], ["market value", "value", "sum of", "opinion"])
        if value is None:
            value, cur = helpers.money_in(sec["value_text"], 1000)
        put("capital_value", value)
        if cur:
            put("currency", cur)
        fsv, _ = helpers.money_in_labelled(sec["value_text"], ["forced sale value", "forced sale", "fsv"])
        put("forced_sale_value", fsv)
    if "marketability_text" in sec:
        put("overall_marketability", helpers.normalise_rating(_first_sentences(sec["marketability_text"], 250)))

    # ---- sentences anywhere (wording the report uses outside its sections)
    put("owner", _owner(text))
    put("client", _client(text))
    put("purpose", _purpose(text))
    put("inspection_date", _inspection_date(text, helpers.find_date))
    for k, v in _tenure(text, year).items():
        put(k, v)
    put("land_size", _land_size(text))
    put("building_type", _building(text))
    put("accommodation", _accommodation(text))
    put("method", standard_methods(_sentences_with(text, r"method|approach|comparison|depreciated|investment|residual",
                                                   limit=6, max_len=2000)))
    put("services", _sentences_with(text, r"\b(?:ECG|GWCL|pipe-?borne|electricity|water supply|"
                                          r"telephone|sewage|septic|drainage|borehole|polytank)\b", limit=2))
    put("accessibility", _sentences_with(text, r"\baccessible\b|\baccess(?:ed)? (?:road|via|through|from|is)|"
                                               r"motorable|tarred|untarred|gravel(?:led)? road", limit=2))
    put("planning_status", _sentences_with(text, r"building permit|development permit|zoned|zoning|planning scheme|"
                                                 r"layout plan|planning (?:permission|approval|status)", limit=2,
                                           max_len=250))
    put("highest_best_use", _sentences_with(text, r"highest and best use|highest & best use", limit=1, max_len=250))
    put("use_occupancy", _sentences_with(text, r"owner[- ]occupied|occupied by|tenanted|currently (?:used|vacant|let)|"
                                               r"\bvacant\b|let to|in use as", limit=1, max_len=250))
    put("encumbrances", _encumbrances(text))
    put("inspection_limitation", _sentences_with(text, r"no structural survey|did not (?:carry out|undertake|inspect|test)|"
                                                       r"not (?:carried out|tested|inspected)|unable to inspect|"
                                                       r"inspection was limited|visual inspection|"
                                                       r"no (?:soil|engineering|structural|site) (?:tests?|investigations?|surveys?)|"
                                                       r"(?:soil|engineering) (?:tests?|investigations?) (?:were|was|have) not",
                                                 limit=2))
    put("legal_disclaimer", _sentences_with(text, r"no responsibility|not responsible|legal (?:opinion|advice|title)|"
                                                  r"does not constitute|we accept no|without liability|no liability|"
                                                  r"disclaim|does not carry (?:the )?right|right (?:of|to) publication|"
                                                  r"without (?:our|the) (?:prior )?written (?:consent|approval)", limit=2))
    put("purpose_restriction", _sentences_with(text, r"sole use|solely for|only for the purpose|should not be (?:used|relied)|"
                                                     r"confidential to|not be reproduced|no reliance|third part(?:y|ies)|"
                                                     r"(?:use|benefit|purpose) of the addressee|addressee(?:'s)? only|"
                                                     r"bankers?,? (?:and )?solicitors|for the (?:exclusive|specific) use",
                                               limit=2))
    put("assumptions", _sentences_with(text, r"\bassum(?:e|ed|ing|ption)", limit=4, max_len=700))
    put("valuer", _valuer(text))
    put("gps_coordinates", _dms(text) or helpers.find_coordinates(text))
    put("ancillary_structures", _sentences_with(text, r"boys'? quarters|outhouse|out-?building|garage|car ?port|car park|"
                                                      r"generator house|fence wall|swimming pool|gatehouse|security post|"
                                                      r"guard house|pump house|store house",
                                                limit=2, max_len=300))
    put("floor_areas", _sentences_with(text, r"floor area|gross internal area|\bgia\b|\bgfa\b|plinth area", limit=2,
                                       max_len=300))
    _tidy(record, found, year)
    tags = helpers.property_tags(" ".join(str(found.get(k) or record.get(k) or "") for k in
                                          ("building_type", "accommodation")) or text[:3000])
    put("tags", tags or None)
    found.pop("location_text", None)
    return found


_PERSON = r"^(?:mr|mrs|miss|ms|dr|prof|rev|hon|nana|alhaji|hajia|madam|togbe|naa|nii|osabarima|barima)\b\.?"


def _tidy(record: dict, found: dict, year: int | None) -> None:
    """Turn long values from the report into the standard short forms (in ``found``)."""
    def cur(k):
        return found.get(k, record.get(k))

    tenure = cur("tenure")
    if isinstance(tenure, str) and len(tenure) > 20:
        info = _tenure(tenure, year)
        if info.get("tenure"):
            found["tenure"] = info["tenure"]
        if info.get("unexpired_term") and not cur("unexpired_term"):
            found["unexpired_term"] = info["unexpired_term"]
    purpose = cur("purpose")
    if isinstance(purpose, str):
        std = standard_purpose(purpose)
        if std and std != purpose:
            found["purpose"] = std
    for k in ("owner", "client"):
        v = cur(k)
        if isinstance(v, str) and v:
            found[k] = _name_only(v)[:255]
    owner = cur("owner")
    if owner and not cur("ownership"):
        low = owner.lower()
        if re.search(r"\b(?:ltd|limited|plc|company|co\.|bank|enterprise|holdings|properties|estates?|"
                     r"developers?|investments?|ventures?|group|corporation|inc)\b", low):
            found["ownership"] = "Corporate"
        elif re.search(r"\bstool\b|\bskin\b", low):
            found["ownership"] = "Stool"
        elif re.search(r"\bfamily\b|\bclan\b", low):
            found["ownership"] = "Family"
        elif re.search(r"government|ministry|state|assembly|authority|commission", low):
            found["ownership"] = "State / public"
        elif re.match(_PERSON, low) or len(owner.split()) in (2, 3, 4):
            found["ownership"] = "Private (individual)"
