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
# Currency written before an amount (case-insensitive: GH¢, Gh¢, GHS, GH₵, GHC, ₵, ¢, US$, USD, $ ...)
_CUR_BEFORE = r"(?:gh\s?[s¢₵c]|ghana\s+cedis?|₵|¢|us\s?\$|usd|\$|ngn|₦|gbp|£|eur|€)"
# ... or after it ("2,300,000.00 Ghana Cedis", "180,000 US Dollars")
_CUR_AFTER = r"(?:ghana\s+cedis|cedis|gh\s?[s¢₵c]\b|us\s+dollars|u\.?s\.?\s+dollars|dollars|usd\b)"
_NUM = r"\d{1,3}(?:(?:,\s?|[\u00a0\u202f\u2009 '’′])\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_SCALE = r"(?:million|mn|m|bn|billion|k)\b"
_MONEY_RE = re.compile(
    rf"(?P<cur1>{_CUR_BEFORE})\s*(?P<n1>{_NUM})\s*(?P<s1>{_SCALE})?"
    rf"|(?P<n2>{_NUM})\s*(?P<s2>{_SCALE})?\s*(?P<cur2>{_CUR_AFTER})", re.I)
_PLAIN_NUM_RE = re.compile(rf"(?<![\d.,])(?P<n>{_NUM})\s*(?P<s>{_SCALE})?(?!\s*(?:st|nd|rd|th)\b)(?![\d.,%])", re.I)
_MONTH_WORDS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
AMOUNT = r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|m|bn|billion|k)?"

_MONTHS = "January February March April May June July August September October November December".split()


def _num(raw: str, scale: str | None = None) -> float | None:
    try:
        value = float(re.sub(r"[,\s\u00a0\u202f\u2009'’′]", "", raw))
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
    s = re.sub(r"\s+", "", sym.upper())
    if "CEDI" in s or s in ("¢", "GH¢", "GH₵", "GHS", "GHC"):
        return "GHS"
    if "DOLLAR" in s or s in ("US$",):
        return "USD"
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

# Bases of value: IVS (Market Value, Market Rent, Equitable Value, Investment Value / Worth,
# Synergistic Value, Liquidation Value) and others used in practice.
BASES_OF_VALUE = ['Market Value', 'Market Rent', 'Fair Value', 'Equitable Value', 'Investment Value', 'Synergistic Value', 'Marriage Value', 'Special Value', 'Liquidation Value', 'Forced Sale Value', 'Reinstatement Cost', 'Depreciated Replacement Cost', 'Mortgage Lending Value']
# other names -> standard basis
BASIS_ALIASES = {
    "open market value": "Market Value", "fair market value": "Market Value", "omv": "Market Value",
    "market rental value": "Market Rent", "open market rent": "Market Rent", "rental value": "Market Rent",
    "worth": "Investment Value", "investment value (worth)": "Investment Value",
    "equitable value": "Equitable Value", "synergy value": "Synergistic Value", "merger value": "Synergistic Value",
    "forced sale": "Forced Sale Value", "fsv": "Forced Sale Value", "quick sale value": "Forced Sale Value",
    "distress value": "Forced Sale Value", "liquidation": "Liquidation Value", "insurance value": "Reinstatement Cost",
    "reinstatement value": "Reinstatement Cost", "drc": "Depreciated Replacement Cost",
}


SECTION_LABELS = {
    "property_name": ["property name", "property number", "house number", "house no", "plot number", "plot no"],
    "owner": ["owner", "registered owner", "name of owner", "proprietor"],
    "use_occupancy": ["use / occupancy", "use/occupancy", "occupancy", "current use", "use"],
    "highest_best_use": ["highest and best use", "highest & best use"],
    "land_size": ["land size", "land area", "plot size", "site area"],
    "accessibility": ["accessibility", "access"],
    "building_type": ["building type", "type of building"],
    "accommodation": ["accommodation"],
    "construction_details": ["construction details", "construction", "specification"],
    "services": ["services", "utilities"],
    "floor_areas": ["floor areas", "floor area"],
    "ancillary_structures": ["ancillary structures", "ancillary buildings", "outbuildings"],
    "ownership": ["ownership"],
    "tenure": ["tenure", "interest valued"],
    "source_of_title": ["source of title", "title document", "title"],
    "unexpired_term": ["unexpired term", "unexpired lease term", "residual term"],
    "planning_status": ["planning status", "planning", "building permit", "zoning"],
    "location_rating": ["location rating"],
    "accessibility_rating": ["accessibility rating"],
    "state_of_repair": ["state of repair", "condition"],
    "user_conformity": ["user conformity", "conformity"],
    "neighbourhood_rating": ["neighbourhood rating", "neighborhood rating"],
    "overall_marketability": ["overall marketability", "marketability"],
    "encumbrances": ["encumbrances", "encumbrance"],
    "legal_disclaimer": ["legal disclaimer", "disclaimer"],
    "inspection_limitation": ["inspection limitation", "inspection limitations", "limitations"],
    "purpose_restriction": ["purpose restriction", "restriction on use", "reliance"],
}

COORD_RE = re.compile(r"(-?\d{1,2}\.\d{3,})\s*°?\s*([NS])?\s*[,;/ ]\s*(-?\d{1,3}\.\d{3,})\s*°?\s*([EW])?", re.I)


def find_coordinates(text: str) -> str | None:
    """Decimal GPS coordinates, e.g. '5.6396, -0.1676' or '5.6396 N, 0.1676 W'."""
    labelled = _labelled(text, ["gps coordinates", "coordinates", "lat/long", "latitude/longitude", "lat long"])
    for source in ([labelled] if labelled else []):
        m = COORD_RE.search(source)
        if m:
            lat, ns, lon, ew = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
            if ns and ns.upper() == "S":
                lat = -abs(lat)
            if ew and ew.upper() == "W":
                lon = -abs(lon)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return f"{lat:.6f}".rstrip("0").rstrip(".") + ", " + f"{lon:.6f}".rstrip("0").rstrip(".")
        return labelled[:80]
    return None


_COORDS_LIKE = re.compile(r"-?\d{1,3}\.\d{2,}|\d{1,3}\s*°|\b(lat|long|latitude|longitude)\b", re.I)


def is_digital_address(value: str | None) -> bool:
    return bool(value) and bool(GPS_RE.fullmatch(re.sub(r"\s*-\s*", "-", str(value).strip().upper())))


def clean_gps_fields(rec: dict, text: str = "") -> bool:
    """The digital address must be a Ghana Post code (AK-039-5028). Coordinates put there by mistake
    move to gps_coordinates, and the real digital address is looked for in the report text.
    Returns True when something changed."""
    value = rec.get("gps_address")
    if not value:
        return False
    code = GPS_RE.search(str(value).upper())
    if code and not _COORDS_LIKE.search(str(value)):
        tidy = f"{code.group(1)}-{code.group(2)}-{code.group(3)}"
        if tidy != value:
            rec["gps_address"] = tidy
            return True
        return False
    if not _COORDS_LIKE.search(str(value)):
        return False          # some other wording (kept for the reviewer to judge)
    if not rec.get("gps_coordinates"):
        rec["gps_coordinates"] = str(value).strip()[:80]
    rec["gps_address"] = (find_gps(text) if text else None) or (code and f"{code.group(1)}-{code.group(2)}-{code.group(3)}") or None
    return True


def find_gps(text: str) -> str | None:
    """Ghana Post GPS digital address, e.g. GA-183-8164."""
    labelled = _labelled(text, ["gps address", "digital address", "ghana post gps", "gps"])
    for source in ([labelled] if labelled else []) + [text]:
        m = GPS_RE.search(source.upper())
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


_COMP_COLUMNS = [  # (field, header words) - first match wins
    ("price_per_sqm", ["per m", "per sq", "/m", "rate", "price/m", "unit price"]),
    ("date", ["date", "year", "period", "when"]),
    ("rent", ["rent", "rental"]),
    ("price", ["price", "value", "amount", "consideration", "sale", "asking", "sold"]),
    ("size_sqm", ["size", "area", "sqm", "m²", "m2", "gfa", "plot"]),
    ("property_type", ["type", "description", "property", "accommodation"]),
    ("address", ["location", "address", "comparable", "property", "site", "area"]),
    ("notes", ["remark", "note", "comment", "adjust", "condition", "source"]),
]


def _cells(line: str) -> list[str]:
    sep = "|" if "|" in line else "\t"
    return [c.strip() for c in line.split(sep)]


def find_comparables(text: str, limit: int = 30) -> list[dict]:
    """Rows of a comparables table, e.g. 'No. | Location | Type | Date | Price (GH¢) | Remarks'."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        head = _cells(lines[i])
        low = [h.lower() for h in head]
        has_place = any(any(w in h for w in ("location", "address", "comparable", "property", "description")) for h in low)
        has_money = any(any(w in h for w in ("price", "value", "rent", "amount", "consideration", "sale")) for h in low)
        if len(head) >= 3 and has_place and has_money and ("|" in lines[i] or "\t" in lines[i]):
            used, mapping = set(), {}
            for idx, h in enumerate(low):
                if re.fullmatch(r"(no\.?|#|s/?n|item)", h):
                    continue
                for field, words in _COMP_COLUMNS:
                    if field in used and field not in ("notes",):
                        continue
                    if any(w in h for w in words):
                        mapping[idx] = field
                        used.add(field)
                        break
            if "price" not in used and "rent" not in used:
                i += 1
                continue
            j = i + 1
            while j < len(lines) and ("|" in lines[j] or "\t" in lines[j]):
                cells = _cells(lines[j])
                row = {}
                for idx, field in mapping.items():
                    if idx >= len(cells) or not cells[idx]:
                        continue
                    raw = cells[idx]
                    if field in ("price", "rent", "price_per_sqm"):
                        value, _ = _money_in(raw, minimum=1)
                        if value is not None:
                            row[field] = value
                    elif field == "size_sqm":
                        m = re.search(r"([\d,]+(?:\.\d+)?)\s*(acres?|ha|hectares?|sq\.?\s?ft|ft²)?", raw, re.I)
                        if m and _num(m.group(1)):
                            v, unit = _num(m.group(1)), (m.group(2) or "").lower()
                            v = v * 4046.86 if unit.startswith("acre") else v * 10000 if unit in ("ha", "hectare", "hectares") \
                                else v * 0.092903 if "ft" in unit else v
                            row["size_sqm"] = round(v, 2)
                    elif field == "notes" and row.get("notes"):
                        row["notes"] += f"; {raw}"
                    else:
                        row[field] = raw[:300]
                if row.get("address") or row.get("price") or row.get("rent"):
                    if row.get("price") and row.get("size_sqm") and not row.get("price_per_sqm"):
                        row["price_per_sqm"] = round(row["price"] / row["size_sqm"], 2)
                    out.append(row)
                j += 1
            i = j
            if len(out) >= limit:
                break
        else:
            i += 1
    return out[:limit]


def normalise_basis(value: str | None) -> str | None:
    """One standard basis of value: 'Market Value VALUATION OPINION' -> 'Market Value',
    'Worth' -> 'Investment Value'."""
    if not value or not str(value).strip():
        return None
    low = str(value).lower()
    hits = sorted((low.find(b.lower()), b) for b in BASES_OF_VALUE if b.lower() in low)
    for alias, basis in BASIS_ALIASES.items():
        m = re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low)
        if m and not any(p <= m.start() < p + len(b) for p, b in hits):
            hits.append((m.start(), basis))
    hits.sort()
    primary = [b for _, b in hits if b != "Forced Sale Value"]
    if primary:
        return primary[0]
    if hits:
        return hits[0][1]
    return str(value).strip()[:160]


def find_basis_of_value(text: str) -> str | None:
    """The basis of value, as ONE basis (e.g. "Market Value"). A forced sale value is a
    separate figure, so "Market Value and Forced Sale Value" gives "Market Value"."""
    labelled = _labelled(text, ["basis of value", "basis of valuation", "bases of value"])
    source = (labelled or text).lower()
    hits = sorted((source.find(b.lower()), b) for b in BASES_OF_VALUE if b.lower() in source)
    primary = [b for _, b in hits if b != "Forced Sale Value"]
    if primary:
        return primary[0]
    if hits:
        return hits[0][1]
    return labelled[:160] if labelled else None


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
    """Value after 'Label: value', 'Label - value' or a two-column table row 'Label | value'."""
    for label in labels:
        for m in re.finditer(rf"(?im)^\s*(?:\d{{1,2}}(?:\.\d{{1,2}})*\.?\s+)?{label}\s*(?:[:\-–]|\|)\s*(.+)$", text):
            value = m.group(1).strip()
            if value.count("|") >= 1:   # a multi-column table row, not "label | value"
                continue
            if value:
                return value[:300]
    return None


def _money_in(window: str, minimum: float = 100) -> tuple[float | None, str | None]:
    """First amount in a short piece of text. Amounts written with a currency win; a bare
    number is used only if it isn't a date, a year, a day ('1st') or too small to be money."""
    m = _MONEY_RE.search(window)
    if m:
        if m.group("n1"):
            return _num(m.group("n1"), m.group("s1")), _currency_code(m.group("cur1"))
        return _num(m.group("n2"), m.group("s2")), _currency_code(m.group("cur2"))
    for m in _PLAIN_NUM_RE.finditer(window):
        raw = m.group("n")
        value = _num(raw, m.group("s"))
        before, after = window[max(0, m.start() - 12):m.start()].lower(), window[m.end():m.end() + 12].lower()
        is_year = re.fullmatch(r"(19|20)\d\d", raw) is not None
        near_month = re.search(_MONTH_WORDS + r"\W*$", before) or re.match(r"\W*" + _MONTH_WORDS, after)
        if value is None or is_year or near_month or value < minimum or re.match(r"\s*(?:sq|m²|m2|acre|ha|%|years?|km)", after):
            continue
        return value, None
    return None, None


_WORD_NUMS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_WORD_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_WORDS_RE = re.compile(
    r"\b((?:(?:" + "|".join(list(_WORD_NUMS) + list(_WORD_SCALES)) + r")[\s,-]*(?:and\s+)?)+)"
    r"(?:ghana\s+)?(?:cedis?|ghana\s+cedis|us\s+dollars|dollars)", re.I)


def words_to_number(phrase: str) -> float | None:
    """'Two Million, Three Hundred Thousand' -> 2300000."""
    total, current, seen = 0, 0, False
    for word in re.findall(r"[a-z]+", phrase.lower()):
        if word == "and":
            continue
        if word in _WORD_NUMS:
            current += _WORD_NUMS[word]
            seen = True
        elif word == "hundred":
            current = (current or 1) * 100
            seen = True
        elif word in _WORD_SCALES:
            total += (current or 1) * _WORD_SCALES[word]
            current = 0
            seen = True
        else:
            return None
    return float(total + current) if seen else None


def _amount_in_words(window: str) -> float | None:
    m = _WORDS_RE.search(window)
    if not m:
        return None
    value = words_to_number(m.group(1))
    return value if value and value >= 100 else None


def _labelled_amount(text: str, labels: list[str]) -> tuple[float | None, str | None]:
    """Amount stated after a label, looking to the end of the sentence / next lines.
    A figure written in words ("Two Million ... Ghana Cedis") is used to check the digits."""
    # A number broken across lines ("GH¢2,300,\n000.00") is joined first.
    text = re.sub(r"(\d[,.'’]?)\s*\n\s*(\d{3}\b)", r"\1\2", text)
    for label in labels:
        for m in re.finditer(rf"(?i)(?<![a-z]){label}(?![a-z])", text):
            rest = text[m.end():]
            line, _, following = rest.partition("\n")
            stop = re.search(r"\.\s+[A-Z]", line)  # end of the sentence
            first = line[:stop.start() + 1] if stop else line
            value, cur = _european(first) or _money_in(first)
            nxt = following.split("\n", 1)[0]
            if value is None and re.match(rf"\s*(?:{_CUR_BEFORE}|\d)", nxt, re.I):
                # "Market Value:" with the figure on the next line
                value, cur = _european(nxt) or _money_in(nxt)
            # "Market Value (GH¢'000)" / "in thousands": the figure is in thousands
            if value is not None and re.search(r"['’]000|\bin\s+thousands?\b|\(000\)", line[:60], re.I) \
                    and value < 1_000_000:
                value *= 1000
            # words on the label's own line, or the next line when that isn't another heading
            next_is_heading = ":" in nxt[:80] or "|" in nxt
            words = _amount_in_words(line) or (None if next_is_heading else _amount_in_words(nxt))
            if words and (value is None or abs(words - value) / words > 0.01):
                # trust the words when the digits are missing or clearly cut short (e.g. 2,300 for 2,300,000)
                if value is None or value < words / 50 or value > words * 50:
                    value = words
                    cur = cur or ("USD" if re.search(r"dollar", f"{line} {nxt}", re.I) else "GHS")
            if value is not None:
                return value, cur
    return None, None


def _european(window: str):
    """'GH¢2.300.000,00' -> (2300000, 'GHS'); None if the text isn't in that style."""
    m = re.search(rf"(?P<cur>{_CUR_BEFORE})?\s*(?P<n>\d{{1,3}}(?:\.\d{{3}}){{2,}})(?:,\d{{1,2}})?(?!\d)", window, re.I)
    if not m:
        return None
    value = float(m.group("n").replace(".", ""))
    return value, (_currency_code(m.group("cur")) if m.group("cur") else None)


def find_date(text: str) -> date | None:
    labelled = _labelled(text, ["date of valuation", "valuation date", "report date", "date of inspection", "date"])
    candidates = [labelled] if labelled else []
    # "the valuation is as at 1st August, 2023" / "Market Value ... as at ..." beats the first date in the report
    m = re.search(r"(?i)(?:valuation (?:is|was|has been) (?:made |undertaken |carried out )?(?:as at|as of|on)|"
                  r"date of valuation(?: is| was)?|\bas at|\bas of|effective date(?: of valuation)?(?: is)?)"
                  r"\s*:?\s*(?:the\s+)?([^.;\n]{4,40})", text)
    if m and not labelled:
        candidates.insert(0, m.group(1))
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
    """The currency used most often for amounts in the text (an exchange-rate note such as
    '$1 = GH¢12.5' does not make a cedi report a dollar one)."""
    from collections import Counter

    counts = Counter()
    for m in _MONEY_RE.finditer(text):
        cur = _currency_code(m.group("cur1") or m.group("cur2"))
        n = m.group("n1") or m.group("n2")
        if _num(n) and _num(n) >= 100:
            counts[cur] += 1
    return counts.most_common(1)[0][0] if counts else None


def extract(text: str, default_currency: str = "GHS") -> dict:
    """Return a draft dict: {report_type, title, summary, records: [..]}."""
    category = classify(text)
    record: dict = {"category": category}
    record["title"] = _labelled(text, ["title", "subject", "re", "property"]) or None
    record["address"] = _labelled(text, ["property address", "address", "site"])
    record["gps_address"] = find_gps(text)
    record["location"] = _labelled(text, ["location", "neighbourhood", "neighborhood", "suburb", "area", "locality"])
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
        from .reader import standard_methods
        record["method"] = standard_methods(_labelled(text, ["method of valuation", "valuation method", "method"]) or "") \
            or standard_methods(" ".join(s for s in re.split(r"(?<=[.])\s+", text) if re.search(
                r"(?i)adopt|method|approach|we have used|employed|applied", s))[:3000])
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
        record["comparables"] = find_comparables(text) or None
        record["valuer"] = _labelled(text, ["valuer", "valued by", "prepared by", "signed by", "reporting valuer"])
        record["forced_sale_value"], _ = _labelled_amount(text, ["forced sale value", "fsv"])
        if record.get("forced_sale_value") and record.get("capital_value") == record["forced_sale_value"]:
            record["capital_value"], _ = _labelled_amount(text, ["open market value", "market value"])
        insp = _labelled(text, ["date of inspection", "inspection date", "date inspected"])
        d = find_date(insp) if insp else None
        record["inspection_date"] = d.isoformat() if d else None
        for key, labels in SECTION_LABELS.items():
            record[key] = record.get(key) or _labelled(text, labels)
        record["gps_coordinates"] = find_coordinates(text)
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
    # Read the rest of the report (sections, sentences, signature) for anything still blank.
    try:
        from types import SimpleNamespace

        from ..routers.records import normalise_rating
        from . import reader
        helpers = SimpleNamespace(find_date=find_date, money_in=_money_in, money_in_labelled=_labelled_amount,
                                  normalise_basis=normalise_basis, normalise_rating=normalise_rating,
                                  property_tags=property_tags, find_coordinates=find_coordinates)
        for field, value in reader.read_report(text, record, helpers).items():
            record[field] = value
    except Exception:  # the extra reading must never stop an upload
        import logging
        logging.getLogger(__name__).exception("report reader failed")
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
