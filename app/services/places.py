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
    return re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text, re.I)


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
            text: str) -> dict:
    """Best guess for location / city / region from labelled fields plus the full text."""
    fields = " , ".join(x for x in (location, address) if x)
    city = city or find_city(fields)
    area = find_area(fields) or (None if location else find_area(text[:6000]))
    if area and not city:
        city = area[1]
    if not city:
        city = find_city(text[:6000])
    if not location and area:
        location = area[0]
    if location:
        location = clean_location(location, city)
    region = region or find_region(fields) or find_region(text[:6000]) or (CITIES.get(city) if city else None)
    if region:
        region = re.sub(r"\s+Region$", "", region.strip(), flags=re.I)
    return {"location": location, "city": city, "region": region}
