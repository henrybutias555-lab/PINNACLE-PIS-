"""Ghana place names, used to fill city / region / location from report text.

Kept deliberately simple and data-driven: extend the tables as the firm meets
new areas. Matching is case-insensitive on whole words.
"""
from __future__ import annotations

import re

# City / town -> region
CITIES = {
    "Accra": "Greater Accra", "Tema": "Greater Accra", "Kasoa": "Central", "Kumasi": "Ashanti",
    "Takoradi": "Western", "Sekondi": "Western", "Cape Coast": "Central", "Tamale": "Northern",
    "Koforidua": "Eastern", "Ho": "Volta", "Sunyani": "Bono", "Wa": "Upper West", "Bolgatanga": "Upper East",
    "Techiman": "Bono East", "Obuasi": "Ashanti", "Winneba": "Central", "Tarkwa": "Western",
    "Nkawkaw": "Eastern", "Dodowa": "Greater Accra", "Aburi": "Eastern", "Ashaiman": "Greater Accra",
    "Prampram": "Greater Accra", "Ada": "Greater Accra", "Akosombo": "Eastern", "Hohoe": "Volta",
    "Damongo": "Savannah", "Goaso": "Ahafo", "Dambai": "Oti", "Nalerigu": "North East",
    "Sefwi Wiawso": "Western North", "Elmina": "Central", "Mampong": "Ashanti", "Ejisu": "Ashanti",
}

# Neighbourhood / suburb -> city
AREAS = {
    "Accra": [
        "East Legon Hills", "East Legon", "West Legon", "Legon", "Airport Residential", "Airport City",
        "Airport Hills", "Cantonments", "Osu", "Labone", "Ridge", "North Ridge", "Roman Ridge", "Dzorwulu",
        "Abelemkpe", "Achimota", "Tesano", "Spintex", "Baatsona", "Adenta", "Madina", "Haatso", "Dome",
        "Kwabenya", "Ashongman", "Oyarifa", "Ogbojo", "Trasacco", "Santeo", "Oyibi", "Abokobi", "Lakeside",
        "Lashibi", "Sakumono", "Teshie", "Nungua", "La", "Labadi", "Shiashie", "Kaneshie", "Dansoman",
        "Weija", "McCarthy Hill", "Mallam", "Kasoa Road", "Lapaz", "Abeka", "Kwashieman", "Darkuman",
        "Pokuase", "Amasaman", "Taifa", "Ofankor", "Kissieman", "Agbogba", "North Legon", "Dzorwulu",
        "Asylum Down", "Adabraka", "Kokomlemle", "Kanda", "Nima", "Maamobi", "Kotobabi", "Alajo",
        "Burma Camp", "Sakaman", "Gbawe", "Mataheko", "Korle Bu", "Jamestown", "Ussher Town",
        "Tse Addo", "Manet", "Regimanuel", "Botwe", "Ayi Mensah", "Peduase", "Kpone", "Afienya",
        "Quartey Papafio",
    ],
    "Tema": [f"Community {n}" for n in range(1, 26)] + ["Tema New Town", "Michel Camp", "Golf City"],
    "Kumasi": ["Ahodwo", "Nhyiaeso", "Asokwa", "Danyame", "Bantama", "Adum", "Patasi", "Kwadaso",
               "Atonsu", "Santasi", "Ayeduase", "Bomso", "Kentinkrono", "Ahinsan", "Asokore Mampong"],
    "Takoradi": ["Anaji", "Airport Ridge", "Beach Road", "Effia", "Kwesimintsim", "Fijai"],
    "Cape Coast": ["Abura", "Pedu", "Kotokuraba", "Amamoma"],
    "Tamale": ["Kalpohini", "Vittin", "Sakasaka", "Gumani"],
}

REGIONS = sorted({*CITIES.values(), "Western North", "Upper East", "Upper West", "Bono East", "North East"},
                 key=len, reverse=True)

# Longest names first so "East Legon Hills" wins over "Legon".
_AREA_INDEX = sorted(((area, city) for city, areas in AREAS.items() for area in areas),
                     key=lambda x: len(x[0]), reverse=True)
_CITY_INDEX = sorted(CITIES, key=len, reverse=True)


def _has(name: str, text: str) -> re.Match | None:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text, re.I)


def find_city(text: str) -> str | None:
    """A city named explicitly in the text (e.g. '..., Accra')."""
    best = None
    for city in _CITY_INDEX:
        if city in ("Ho", "Wa", "Ada"):  # too short to match safely in free text
            if not re.search(rf"(?:^|,|\b(?:in|at|town of|city of))\s*{city}\s*(?:,|\.|$)", text, re.M):
                continue
        m = _has(city, text)
        if m and (best is None or m.start() < best[1]):
            best = (city, m.start())
    return best[0] if best else None


def find_area(text: str) -> tuple[str, str] | None:
    """(area, city) for the first known neighbourhood mentioned."""
    hits = []
    for area, city in _AREA_INDEX:
        if area in ("La", "Ridge"):  # ambiguous short words
            if not re.search(rf"(?:^|,|\s){area}(?:,|\s*$)", text, re.M):
                continue
        m = _has(area, text)
        if m:
            hits.append((m.start(), -len(area), area, city))
    if not hits:
        return None
    hits.sort()
    _, _, area, city = hits[0]
    return area, city


def find_region(text: str) -> str | None:
    for region in REGIONS:
        if re.search(rf"(?<![A-Za-z]){re.escape(region)}\s+Region", text, re.I):
            return region
    return None


def clean_location(location: str, city: str | None) -> str:
    """Drop a trailing ', City' / ', City, Region' from a location string."""
    loc = location.strip(" ,.-–")
    if city:
        loc = re.sub(rf"[,\s]+{re.escape(city)}\b.*$", "", loc, flags=re.I).strip(" ,.-–")
    return loc or location.strip()


def resolve(location: str | None, address: str | None, city: str | None, region: str | None,
            text: str, district: str | None = None) -> dict:
    """Best guess for location / district (MMDA) / city / region from labelled fields plus the full text."""
    fields = " , ".join(x for x in (location, address) if x)
    city = city or find_city(fields)
    area = find_area(fields) or (None if location else find_area(text[:6000]))
    if area and not city:
        city = area[1]
    if not city:
        city = find_city(text[:6000])
    if not location and area:
        location = area[0]
    if not location and re.search(r"\b(?:in|within|at)\s+(?:the\s+)?central business district", text[:6000], re.I):
        location = "Central Business District"
    if location:
        location = clean_location(location, city)
    region = region or find_region(fields) or find_region(text[:6000]) or (CITIES.get(city) if city else None)
    if region:
        region = re.sub(r"\s+Region$", "", region.strip(), flags=re.I)
    given = district
    district = normalise_mmda(district, said_assembly=bool(given and re.search(r"assembly", given, re.I)))
    if given and not district and not location:
        location = given  # e.g. "Central Business District" is a place, not an assembly
    district = district or find_mmda(fields) or find_mmda(text[:8000]) or mmda_for(location, city, fields)
    if district and not region:
        region = MMDA_REGION.get(district)
    return {"location": location, "district": district, "city": city, "region": region}


# ------------------------------------------------------------------ MMDAs
# Metropolitan, Municipal and District Assemblies. Names are stored without the
# word "Assembly", e.g. "Adentan Municipal".

GREATER_ACCRA_MMDAS = [
    "Accra Metropolitan", "Tema Metropolitan", "Ablekuma Central Municipal", "Ablekuma North Municipal",
    "Ablekuma West Municipal", "Adentan Municipal", "Ashaiman Municipal", "Ayawaso Central Municipal",
    "Ayawaso East Municipal", "Ayawaso North Municipal", "Ayawaso West Municipal", "Ga Central Municipal",
    "Ga East Municipal", "Ga North Municipal", "Ga South Municipal", "Ga West Municipal",
    "Korle Klottey Municipal", "Kpone-Katamanso Municipal", "Krowor Municipal", "La Dade-Kotopon Municipal",
    "La Nkwantanang-Madina Municipal", "Ledzokuku Municipal", "Okaikwei North Municipal", "Tema West Municipal",
    "Weija-Gbawe Municipal", "Ada East District", "Ada West District", "Ningo-Prampram District",
    "Shai-Osudoku District",
]

# Neighbourhood / town -> MMDA. Only well-established cases are listed; anything
# else is left blank for the surveyor (or the AI) to fill. Extend as needed.
AREA_MMDA: dict[str, str | None] = {
    # Greater Accra
    "Osu": "Korle Klottey Municipal", "Adabraka": "Korle Klottey Municipal", "Asylum Down": "Korle Klottey Municipal",
    "Jamestown": "Accra Metropolitan", "Ussher Town": "Accra Metropolitan", "Korle Bu": "Accra Metropolitan",
    "Labone": "La Dade-Kotopon Municipal", "La": "La Dade-Kotopon Municipal", "Labadi": "La Dade-Kotopon Municipal",
    "Cantonments": "La Dade-Kotopon Municipal", "Burma Camp": "La Dade-Kotopon Municipal",
    "Dzorwulu": "Ayawaso West Municipal", "Abelemkpe": "Ayawaso West Municipal", "Roman Ridge": "Ayawaso West Municipal",
    "East Legon": "Ayawaso West Municipal",
    # Look-alike names that are NOT part of the area above: deliberately left blank.
    "East Legon Hills": None, "West Legon": None, "North Legon": None, "Legon": None, "Santeo": None,
    "Trasacco": None, "Oyarifa": None, "Spintex": None, "Lashibi": None,
    "Nima": "Ayawaso East Municipal", "Kanda": "Ayawaso East Municipal", "Maamobi": "Ayawaso North Municipal",
    "Kotobabi": "Ayawaso Central Municipal", "Alajo": "Ayawaso Central Municipal",
    "Madina": "La Nkwantanang-Madina Municipal", "Adenta": "Adentan Municipal",
    "Haatso": "Ga East Municipal", "Dome": "Ga East Municipal", "Kwabenya": "Ga East Municipal",
    "Abokobi": "Ga East Municipal", "Taifa": "Ga East Municipal", "Ashongman": "Ga East Municipal",
    "Ofankor": "Ga North Municipal", "Amasaman": "Ga West Municipal",
    "Achimota": "Okaikwei North Municipal", "Tesano": "Okaikwei North Municipal",
    "Dansoman": "Ablekuma West Municipal",
    "Weija": "Weija-Gbawe Municipal", "Gbawe": "Weija-Gbawe Municipal", "McCarthy Hill": "Weija-Gbawe Municipal",
    "Teshie": "Ledzokuku Municipal", "Nungua": "Krowor Municipal", "Sakumono": "Tema West Municipal",
    "Ashaiman": "Ashaiman Municipal", "Kpone": "Kpone-Katamanso Municipal",
    "Prampram": "Ningo-Prampram District", "Dodowa": "Shai-Osudoku District", "Ada": "Ada East District",
    "Makola": "Accra Metropolitan", "Tudu": "Accra Metropolitan", "Kantamanto": "Accra Metropolitan",
    "Ridge": None, "North Ridge": None,
    # Kumasi area
    "Adum": "Kumasi Metropolitan", "Bantama": "Kumasi Metropolitan", "Nhyiaeso": "Kumasi Metropolitan",
    "Ahodwo": "Kumasi Metropolitan", "Danyame": "Kumasi Metropolitan",
    "Asokwa": "Asokwa Municipal", "Kwadaso": "Kwadaso Municipal",
    "Ayeduase": "Oforikrom Municipal", "Bomso": "Oforikrom Municipal", "Kentinkrono": "Oforikrom Municipal",
    "Asokore Mampong": "Asokore Mampong Municipal",
}
AREA_MMDA.update({f"Community {n}": "Tema Metropolitan" for n in range(1, 13)})

# City / town -> MMDA, where the town has its own assembly. (Accra has many, so none.)
CITY_MMDA = {
    "Tema": None, "Kumasi": None,  # several assemblies: decided by neighbourhood
    "Ashaiman": "Ashaiman Municipal", "Tamale": "Tamale Metropolitan", "Cape Coast": "Cape Coast Metropolitan",
    "Takoradi": "Sekondi-Takoradi Metropolitan", "Sekondi": "Sekondi-Takoradi Metropolitan",
    "Koforidua": "New Juaben South Municipal", "Ho": "Ho Municipal", "Sunyani": "Sunyani Municipal",
    "Wa": "Wa Municipal", "Bolgatanga": "Bolgatanga Municipal", "Techiman": "Techiman Municipal",
    "Obuasi": "Obuasi Municipal", "Winneba": "Effutu Municipal", "Tarkwa": "Tarkwa-Nsuaem Municipal",
    "Kasoa": "Awutu Senya East Municipal", "Ejisu": "Ejisu Municipal", "Hohoe": "Hohoe Municipal",
    "Nkawkaw": "Kwahu West Municipal", "Akosombo": "Asuogyaman District", "Dodowa": "Shai-Osudoku District",
    "Prampram": "Ningo-Prampram District", "Ada": "Ada East District",
}

_ASSEMBLY = re.compile(
    r"([A-Z][A-Za-z'’]+(?:[ -](?:[A-Z][A-Za-z'’]+|and))*?)\s+(Metropolitan|Metropolis|Municipality|Municipal|District)"
    r"(?:\s+(?:Chief\s+Executive|Assembly|Council))?", re.M)
_NOT_PLACE = {"The", "This", "A", "An", "Of", "In", "Within", "Under", "Its", "Our", "And", "Local", "Government"}


# Words that describe a kind of area, never the name of an assembly.
_AREA_WORDS = {"central", "business", "commercial", "residential", "industrial", "financial", "school", "health",
               "police", "electoral", "postal", "education", "planning", "mixed", "zone", "trade", "shopping"}


def canonical_mmda(name: str | None) -> str | None:
    """Official spelling of one of the 261 MMDAs (ignores case, hyphens, 'Assembly',
    'Municipality', 'Metropolis'), else None."""
    if not name:
        return None
    return _CANONICAL.get(_key(name))


def looks_like_mmda(name: str | None, said_assembly: bool = False) -> bool:
    """True for a plausible Metropolitan / Municipal / District Assembly name.
    'Central Business District', 'Industrial Area' etc. are rejected."""
    if not name:
        return False
    clean = re.sub(r"\s+assembly$", "", name.strip(), flags=re.I)
    if canonical_mmda(clean):
        return True
    m = re.fullmatch(r"(.+?)\s+(Metropolitan|Metropolis|Municipal(?:ity)?|District)", clean, re.I)
    if not m:
        return False
    words = re.split(r"[\s-]+", m.group(1).lower())
    if any(w in _AREA_WORDS for w in words) or len(words) > 5:
        return False
    # "X District" is a common way to describe an area; trust it only when called an Assembly.
    return m.group(2).lower() != "district" or said_assembly


def normalise_mmda(name: str | None, said_assembly: bool = False) -> str | None:
    """Clean an MMDA value; None when it isn't one."""
    if not looks_like_mmda(name, said_assembly):
        return None
    clean = re.sub(r"\s+assembly$", "", name.strip(), flags=re.I)
    known = canonical_mmda(clean)
    if known:
        return known
    head, kind = clean.rsplit(" ", 1)
    kind = {"metropolis": "Metropolitan", "municipality": "Municipal"}.get(kind.lower(), kind.title())
    return f"{head} {kind}"


def find_mmda(text: str) -> str | None:
    """An MMDA named in the text, e.g. '... within the Adentan Municipal Assembly'."""
    for m in _ASSEMBLY.finditer(text):
        words = m.group(1).split()
        while words and words[0] in _NOT_PLACE:
            words = words[1:]
        if not words or len(words) > 5:
            continue
        kind = {"metropolis": "Metropolitan", "municipality": "Municipal"}.get(m.group(2).lower(), m.group(2).title())
        name = f"{' '.join(words)} {kind}"
        said_assembly = bool(re.match(r"\s+(Assembly|Chief\s+Executive|Council)", text[m.end(2):], re.I))
        found = normalise_mmda(name, said_assembly)
        if found:
            return found
    return None


def mmda_for(location: str | None, city: str | None, fields: str) -> str | None:
    """MMDA from a known neighbourhood or town."""
    hits = []
    for area, mmda in AREA_MMDA.items():
        if area in ("La", "Ada"):
            if not re.search(rf"(?:^|,|\s){area}(?:,|\s*$)", fields or "", re.M):
                continue
        m = _has(area, fields or "")
        if m:
            hits.append((m.start(), -len(area), mmda))
    if hits:
        return sorted(hits)[0][2]  # may be None: a known area whose assembly isn't listed
    if re.search(r"central business district|\bcbd\b|city cent(?:re|er)", f"{location or ''} {fields or ''}", re.I):
        return CBD_MMDA.get(city or "")
    return CITY_MMDA.get(city or "")


# The central business district of a city -> its assembly.
CBD_MMDA = {"Accra": "Accra Metropolitan", "Kumasi": "Kumasi Metropolitan", "Tamale": "Tamale Metropolitan",
            "Takoradi": "Sekondi-Takoradi Metropolitan", "Sekondi": "Sekondi-Takoradi Metropolitan",
            "Cape Coast": "Cape Coast Metropolitan", "Tema": "Tema Metropolitan"}

# Names offered in the District (MMDA) drop-down: Greater Accra in full, plus the
# assemblies referenced above. Surveyors can still type any other MMDA.
from .mmda_data import MMDAS as _MMDAS  # noqa: E402  (all 261 assemblies)

ALL_MMDAS = sorted(name for name, *_ in _MMDAS)
MMDA_REGION = {name: region for name, region, *_ in _MMDAS}


def _key(name: str) -> str:
    """Spelling-insensitive key: 'Korle-Klottey Municipal Assembly' == 'korle klottey municipal'."""
    name = re.sub(r"\s+(assembly|council)$", "", name.strip(), flags=re.I)
    name = re.sub(r"\bmetropolis$", "metropolitan", name, flags=re.I)
    name = re.sub(r"\bmunicipality$", "municipal", name, flags=re.I)
    return re.sub(r"[^a-z]", "", name.lower())


_CANONICAL = {}
for _name, _region, _capital, _aliases in _MMDAS:
    for _n in [_name, *_aliases]:
        _CANONICAL[_key(_n)] = _name

# A town that is an assembly's capital points to that assembly (not for cities split
# between several assemblies, which are decided by neighbourhood instead).
_SPLIT_CITIES = {"Accra", "Kumasi", "Tema", "Sekondi", "Takoradi", "Obuasi", "Sunyani", "Techiman", "Ho", "Wa"}
for _name, _region, _capital, _aliases in _MMDAS:
    if _capital and _capital not in _SPLIT_CITIES:
        CITY_MMDA.setdefault(_capital, _name)
