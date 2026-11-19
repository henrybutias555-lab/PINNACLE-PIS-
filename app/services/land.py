"""Land values: the land's size as a number, land values stated in reports, and trends by MMDA."""
from __future__ import annotations

import re
import statistics
from collections import defaultdict

ACRE_SQM = 4046.8564
HECTARE_SQM = 10_000.0
SQFT_SQM = 0.09290304
PLOT_SQM = 100 * 70 * SQFT_SQM          # a standard Ghanaian plot, 100 ft × 70 ft (about 650 m²)

_N = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def land_area_sqm(text: str | None) -> float | None:
    """'0.25 acres (1,011.7 m²)' -> 1011.7; '100ft x 70ft' -> 650.3; '1.5 ha' -> 15000; '2 plots' -> 1300.6."""
    if not text:
        return None
    t = str(text).lower().replace("×", "x").replace("’", "'")
    m = re.search(rf"{_N}\s*(?:m²|m2|sq\.?\s*m(?:etres?|eters?)?\b|square\s*met(?:re|er)s?)", t)
    if m:
        return round(_num(m.group(1)), 2)
    m = re.search(rf"{_N}\s*(?:acres?|ac\b)", t)
    if m:
        return round(_num(m.group(1)) * ACRE_SQM, 4)
    m = re.search(rf"{_N}\s*(?:hectares?|ha\b)", t)
    if m:
        return round(_num(m.group(1)) * HECTARE_SQM, 4)
    m = re.search(rf"{_N}\s*(?:'|ft|feet|foot)?\s*(?:x|by)\s*{_N}\s*(?:'|ft|feet|foot)", t)
    if m:
        return round(_num(m.group(1)) * _num(m.group(2)) * SQFT_SQM, 4)
    m = re.search(rf"{_N}\s*m\s*(?:x|by)\s*{_N}\s*m\b", t)
    if m:
        return round(_num(m.group(1)) * _num(m.group(2)), 2)
    m = re.search(rf"{_N}\s*(?:sq\.?\s*f(?:ee)?t|square\s*f(?:ee|oo)t|ft²|ft2)", t)
    if m:
        return round(_num(m.group(1)) * SQFT_SQM, 4)
    m = re.search(rf"{_N}\s*plots?\b", t) or re.search(r"\b(one|two|three|four|five|half(?: a)?)\s+plots?\b", t)
    if m:
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "half": 0.5, "half a": 0.5}
        count = words[m.group(1)] if m.group(1) in words else _num(m.group(1))
        return round(count * PLOT_SQM, 4)
    return None


_LAND_TYPES = re.compile(r"\b(?:land|plot|vacant|bare land|site|parcel|acreage|undeveloped)\b", re.I)


def is_land(record) -> bool:
    ptype = (getattr(record, "property_type", None) or "").lower()
    tags = " ".join(getattr(record, "tags", None) or [])
    return bool(_LAND_TYPES.search(ptype) or (not ptype and _LAND_TYPES.search(tags)))


def land_figure(record) -> float | None:
    """The land value of a valuation: stated separately (e.g. in a cost approach) or, for land, the value."""
    if getattr(record, "land_value", None):
        return float(record.land_value)
    if is_land(record) and getattr(record, "capital_value", None):
        return float(record.capital_value)
    return None


def _period(d, interval: str) -> str:
    if interval == "year":
        return str(d.year)
    if interval == "month":
        return f"{d.year}-{d.month:02d}"
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def land_trend(records, districts: list[str], metric: str = "per_acre", interval: str = "quarter") -> dict:
    """Average land value over time for each MMDA asked for ('' = all MMDAs together).
    metric: per_acre | per_sqm | total."""
    from .analytics import dominant_currency, suspect_values

    bad = {r.id for r in suspect_values(records)}
    vals = [r for r in records if r.category == "valuation" and r.id not in bad]
    currency = dominant_currency(vals)
    vals = [r for r in vals if (r.currency or currency) == currency]
    out = {"metric": metric, "interval": interval, "currency": currency, "series": [], "undated": 0,
           "no_area": 0, "plot_sqm": round(PLOT_SQM, 2)}
    key = lambda name: re.sub(r"[^a-z0-9]+", "", (name or "").lower())  # noqa: E731  'Kpone-Katamanso' = 'Kpone Katamanso'
    for district in districts or [""]:
        buckets: dict[str, list[float]] = defaultdict(list)
        count = 0
        for r in vals:
            if district and key(r.district) != key(district):
                continue
            value = land_figure(r)
            if not value:
                continue
            area = r.land_area_sqm or land_area_sqm(r.land_size)
            if metric == "per_acre":
                if not area:
                    out["no_area"] += 1
                    continue
                figure = value / area * ACRE_SQM
            elif metric == "per_sqm":
                if not area:
                    out["no_area"] += 1
                    continue
                figure = value / area
            else:
                figure = value
            if not r.record_date:
                out["undated"] += 1
                continue
            buckets[_period(r.record_date, interval)].append(figure)
            count += 1
        points, prev = [], None
        for period in sorted(buckets):
            v = buckets[period]
            mean = round(statistics.fmean(v), 2)
            points.append({"period": period, "mean": mean, "median": round(statistics.median(v), 2),
                           "count": len(v), "change_pct": round((mean - prev) / prev * 100, 1) if prev else None})
            prev = mean
        first, last = (points[0]["mean"], points[-1]["mean"]) if points else (None, None)
        out["series"].append({"district": district or "All MMDAs", "points": points, "valuations": count,
                              "overall_change_pct": round((last - first) / first * 100, 1) if first and len(points) > 1 else None})
    return out
