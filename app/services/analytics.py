"""Statistics, trends, cross-analysis and comparable search over stored records."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date

from ..models import Record

METRICS = {
    "capital_value": ("valuation", "Valuation opinion"),
    "value_per_sqm": ("valuation", "Valuation opinion per m²"),
    "annual_rent": ("valuation", "Rental opinion (annual)"),
    "rent_per_sqm": ("valuation", "Rental opinion per m² p.a."),
    "yield_pct": ("valuation", "Yield (%)"),
    "cost_per_sqm": ("cost", "Construction cost per m²"),
    "total_cost": ("cost", "Total construction cost"),
    "avg_price": ("housing", "Average house price"),
    "avg_monthly_rent": ("housing", "Average monthly rent"),
    "affordability_ratio": ("housing", "Affordability ratio"),
    "supply_units": ("housing", "Supply (units)"),
}
RATIO_METRICS = {"yield_pct", "affordability_ratio"}


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 2),
        "median": round(statistics.median(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def _values(records: list[Record], metric: str) -> list[float]:
    return [float(getattr(r, metric)) for r in records if getattr(r, metric) is not None]


def dominant_currency(records: list[Record]) -> str | None:
    counts = Counter(r.currency for r in records if r.currency)
    return counts.most_common(1)[0][0] if counts else None


# A valuation opinion below this per m² (in cedis) is almost certainly a mis-read figure
# (e.g. 2,300 instead of 2,300,000); such records are listed and left out of averages.
MIN_VALUE_PER_SQM_GHS = 100
MIN_VALUE_GHS = 10_000


def suspect_values(records: list[Record]) -> list[Record]:
    out = []
    for r in records:
        if r.category != "valuation" or (r.currency or "GHS") != "GHS" or not r.capital_value:
            continue
        if r.capital_value < MIN_VALUE_GHS or (r.value_per_sqm is not None and r.value_per_sqm < MIN_VALUE_PER_SQM_GHS):
            out.append(r)
    return out


def basis_figure(r: Record, basis: str) -> float | None:
    """The opinion of value that belongs to a basis (rent for Market Rent, FSV for Forced Sale Value)."""
    if basis == "Market Rent":
        return r.annual_rent if r.annual_rent else (r.capital_value if r.basis_of_value == "Market Rent" else None)
    if basis == "Forced Sale Value":
        if r.forced_sale_value:
            return r.forced_sale_value
        return r.capital_value if r.basis_of_value == "Forced Sale Value" else None
    return r.capital_value


def by_basis(records: list[Record]) -> list[dict]:
    """Average opinion of value for each basis of value (valuations only, one currency)."""
    vals = [r for r in records if r.category == "valuation"]
    currency = dominant_currency(vals)
    vals = [r for r in vals if (r.currency or currency) == currency]
    groups: dict[str, list[tuple[float, float | None]]] = defaultdict(list)
    for r in vals:
        basis = r.basis_of_value or "Not stated"
        fig = basis_figure(r, basis)
        if fig:
            per = fig / r.size_sqm if r.size_sqm and basis not in ("Market Rent",) else None
            groups[basis].append((fig, per))
        if r.forced_sale_value and basis != "Forced Sale Value":   # FSV stated alongside another basis
            groups["Forced Sale Value"].append((r.forced_sale_value,
                                                r.forced_sale_value / r.size_sqm if r.size_sqm else None))
        if r.annual_rent and basis not in ("Market Rent",):          # rent opinion given alongside
            groups["Market Rent"].append((r.annual_rent, None))
    out = []
    for basis, items in groups.items():
        figs = [f for f, _ in items]
        pers = [p for _, p in items if p]
        out.append({"basis": basis, "currency": currency, "count": len(figs),
                    "mean": round(statistics.fmean(figs), 2), "median": round(statistics.median(figs), 2),
                    "min": round(min(figs), 2), "max": round(max(figs), 2),
                    "per_sqm": round(statistics.fmean(pers), 2) if pers else None,
                    "per_year": basis == "Market Rent"})
    order = {"Market Value": 0, "Market Rent": 1, "Forced Sale Value": 2, "Not stated": 99}
    out.sort(key=lambda x: (order.get(x["basis"], 10), -x["count"]))
    return out


def _on_basis(r: Record, basis: str) -> bool:
    """Does this valuation give an opinion on this basis?"""
    if basis == "Market Rent":
        return bool(r.annual_rent) or r.basis_of_value == "Market Rent"
    if basis == "Forced Sale Value":
        return bool(r.forced_sale_value) or r.basis_of_value == "Forced Sale Value"
    if basis == "Not stated":
        return not r.basis_of_value
    return r.basis_of_value == basis


def _basis_stats(items: list[tuple[Record, float]], per_sqm: bool) -> dict | None:
    if not items:
        return None
    figs = [f for _, f in items]
    pers = [f / r.size_sqm for r, f in items if r.size_sqm] if per_sqm else []
    return {"count": len(figs), "mean": round(statistics.fmean(figs), 2), "median": round(statistics.median(figs), 2),
            "min": round(min(figs), 2), "max": round(max(figs), 2),
            "per_sqm": round(statistics.fmean(pers), 2) if pers else None}


def basis_analysis(records: list[Record], basis: str, compare: str | None = None, interval: str = "quarter") -> dict:
    """Analysis of the opinions of value on one basis (optionally against a second basis, e.g.
    Market Value against Market Rent): overall figures, by location, by property type, trend."""
    bad = {r.id for r in suspect_values(records)}
    vals = [r for r in records if r.category == "valuation" and r.id not in bad]
    currency = dominant_currency(vals)
    vals = [r for r in vals if (r.currency or currency) == currency]
    bases = [basis] + ([compare] if compare and compare != basis else [])

    def items(recs, b):
        out = []
        for r in recs:
            if _on_basis(r, b):
                f = basis_figure(r, b)
                if f:
                    out.append((r, float(f)))
        return out

    def group(key):
        buckets: dict[str, list[Record]] = defaultdict(list)
        for r in vals:
            if any(_on_basis(r, b) for b in bases):
                buckets[key(r) or "unspecified"].append(r)
        rows = []
        for name, recs in buckets.items():
            row = {"name": name}
            for b in bases:
                row[b] = _basis_stats(items(recs, b), b != "Market Rent")
            if len(bases) == 2 and row[bases[0]] and row[bases[1]]:
                row["yield_pct"] = _yield(row, bases)
            rows.append(row)
        rows.sort(key=lambda x: -max((x[b] or {}).get("count", 0) for b in bases))
        return rows[:15]

    overall = {b: _basis_stats(items(vals, b), b != "Market Rent") for b in bases}
    out = {"basis": basis, "compare": bases[1] if len(bases) == 2 else None, "bases": bases, "currency": currency,
           "overall": overall,
           "by_location": group(lambda r: r.location or r.city),
           "by_district": group(lambda r: r.district),
           "by_property_type": group(lambda r: r.property_type),
           "trend": {}, "excluded_suspect": len(bad), "interval": interval}
    used = [r for r in vals if any(_on_basis(r, b) for b in bases)]
    dates = sorted(r.record_date for r in used if r.record_date)
    out["date_range"] = [dates[0].isoformat(), dates[-1].isoformat()] if dates else None
    out["undated"] = sum(1 for r in used if not r.record_date)
    if len(bases) == 2 and overall[bases[0]] and overall[bases[1]]:
        out["yield_pct"] = _yield(overall, bases)
    for b in bases:
        buckets: dict[str, list[float]] = defaultdict(list)
        for r, f in items(vals, b):
            if r.record_date:
                buckets[_period(r.record_date, interval)].append(f)
        out["trend"][b] = [{"period": p, "mean": round(statistics.fmean(v), 2), "count": len(v)}
                           for p, v in sorted(buckets.items())]
    return out


def _yield(row: dict, bases: list[str]) -> float | None:
    """Rent as a % of capital value, when one basis is a rent and the other a capital value."""
    if "Market Rent" not in bases:
        return None
    rent = row["Market Rent"]["mean"]
    cap = row[[b for b in bases if b != "Market Rent"][0]]["mean"]
    return round(rent / cap * 100, 2) if cap else None


def summary(records: list[Record]) -> dict:
    suspects = suspect_values(records)
    suspect_ids = {r.id for r in suspects}
    all_records = records
    records = [r for r in records if r.id not in suspect_ids]
    currencies = Counter(r.currency for r in records if r.currency)
    currency = dominant_currency(records)
    # Monetary stats only mix records of one currency.
    same_cur = [r for r in records if r.currency == currency] if currency else records
    metrics = {}
    for metric, (_, label) in METRICS.items():
        pool = records if metric in RATIO_METRICS or metric == "supply_units" else same_cur
        s = _stats(_values(pool, metric))
        if s:
            metrics[metric] = {"label": label, **s}
    dates = sorted(r.record_date for r in records if r.record_date)
    return {
        "by_basis": by_basis(records),
        "suspect_values": [{"id": r.id, "label": r.address or r.location or r.title or f"Record {r.id}",
                            "capital_value": r.capital_value, "value_per_sqm": r.value_per_sqm,
                            "size_sqm": r.size_sqm} for r in suspects],
        "record_count": len(all_records),
        "by_category": dict(Counter(r.category for r in all_records)),
        "by_property_type": dict(Counter((r.property_type or "unspecified") for r in all_records).most_common(10)),
        "top_locations": dict(Counter((r.location or r.city or "unspecified") for r in all_records).most_common(10)),
        "top_districts": dict(Counter((r.district or "unspecified") for r in all_records).most_common(10)),
        "currency": currency,
        "other_currencies_excluded": {c: n for c, n in currencies.items() if c != currency},
        "date_range": [dates[0].isoformat(), dates[-1].isoformat()] if dates else None,
        "metrics": metrics,
    }


def breakdown(records: list[Record], group_by: list[str]) -> list[dict]:
    bad = {r.id for r in suspect_values(records)}
    records = [r for r in records if r.id not in bad]
    currency = dominant_currency(records)
    groups: dict[tuple, list[Record]] = defaultdict(list)
    for r in records:
        key = tuple((getattr(r, g) or "unspecified") for g in group_by)
        groups[key].append(r)
    rows = []
    for key, recs in groups.items():
        same = [r for r in recs if r.currency == currency]
        row = {g: k for g, k in zip(group_by, key)}
        row["count"] = len(recs)
        for metric in ("value_per_sqm", "capital_value", "annual_rent", "yield_pct", "cost_per_sqm",
                       "avg_price", "avg_monthly_rent"):
            vals = _values(recs if metric in RATIO_METRICS else same, metric)
            row[metric] = round(statistics.fmean(vals), 2) if vals else None
        rows.append(row)
    rows.sort(key=lambda r: -r["count"])
    return rows


def _period(d: date, interval: str) -> str:
    if interval == "year":
        return str(d.year)
    if interval == "month":
        return f"{d.year}-{d.month:02d}"
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def trend(records: list[Record], metric: str, interval: str = "quarter") -> list[dict]:
    bad = {r.id for r in suspect_values(records)}
    records = [r for r in records if r.id not in bad]
    currency = dominant_currency(records)
    pool = records if metric in RATIO_METRICS else [r for r in records if r.currency == currency]
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in pool:
        v = getattr(r, metric)
        if v is not None and r.record_date:
            buckets[_period(r.record_date, interval)].append(float(v))
    series = []
    prev = None
    for period in sorted(buckets):
        vals = buckets[period]
        mean = round(statistics.fmean(vals), 2)
        change = round((mean - prev) / prev * 100, 1) if prev else None
        series.append({"period": period, "mean": mean, "median": round(statistics.median(vals), 2),
                       "count": len(vals), "change_pct": change})
        prev = mean
    return series


def cross_analysis(records: list[Record], interval: str = "year") -> list[dict]:
    """Construction cost per m² vs capital value per m² over time (same currency)."""
    # construction cost per m²: cost reports and the 'rate adopted' in valuation reports
    cost = {p["period"]: p for p in trend([r for r in records if r.cost_per_sqm], "cost_per_sqm", interval)}
    value = {p["period"]: p for p in trend([r for r in records if r.category == "valuation"], "value_per_sqm", interval)}
    rows = []
    for period in sorted(set(cost) | set(value)):
        c = cost.get(period, {}).get("mean")
        v = value.get(period, {}).get("mean")
        rows.append({
            "period": period,
            "cost_per_sqm": c,
            "value_per_sqm": v,
            "cost_to_value_pct": round(c / v * 100, 1) if c and v else None,
            "development_margin_per_sqm": round(v - c, 2) if c and v else None,
        })
    return rows


def find_comparables(candidates: list[Record], *, location: str | None, property_type: str | None,
                     size_sqm: float | None, ref_date: date | None, limit: int = 10) -> list[dict]:
    """Rank records by similarity to a subject property (0-100 score)."""
    ref_date = ref_date or date.today()
    out = []
    for r in candidates:
        score = 0.0
        reasons = []
        if location:
            loc = location.lower()
            if r.location and r.location.lower() == loc:
                score += 40; reasons.append("same location")
            elif any(loc in (x or "").lower() for x in (r.location, r.address)):
                score += 30; reasons.append("location match")
            elif r.city and r.city.lower() == loc:
                score += 15; reasons.append("same city")
        else:
            score += 20
        if property_type:
            if r.property_type and r.property_type.lower() == property_type.lower():
                score += 25; reasons.append("same property type")
        else:
            score += 12
        if size_sqm and r.size_sqm:
            diff = abs(r.size_sqm - size_sqm) / size_sqm
            score += max(0.0, 20 * (1 - diff)); reasons.append(f"size within {round(diff * 100)}%")
        elif not size_sqm:
            score += 10
        if r.record_date:
            months = abs((ref_date - r.record_date).days) / 30.4
            score += max(0.0, 15 * (1 - months / 36)); reasons.append(f"{round(months)} months old")
        out.append({"score": round(score, 1), "reasons": reasons, "record": r})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def _fmt(value: float | None, currency: str | None = None, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    if pct:
        return f"{value:.2f}%"
    return f"{currency + ' ' if currency else ''}{value:,.0f}"


def template_commentary(stats: dict, trends: dict, scope: str) -> str:
    """Rule-based commentary used when Claude is not configured."""
    m = stats["metrics"]
    cur = stats.get("currency")
    n = stats["record_count"]
    if n == 0:
        return f"No records are held in the Pinnacle Intelligence System for {scope}. Commentary cannot be prepared."
    paras = []
    dr = stats.get("date_range")
    period = f" dated between {dr[0]} and {dr[1]}" if dr else ""
    paras.append(
        f"This commentary draws on {n} record{'s' if n != 1 else ''} held in the firm's database for {scope}{period}. "
        + ("The evidence base is limited and the figures below should be treated with caution. " if n < 5 else "")
    )
    val_bits = []
    if "value_per_sqm" in m:
        s = m["value_per_sqm"]
        val_bits.append(f"valuation opinions averaged {_fmt(s['mean'], cur)} per m² (range {_fmt(s['min'], cur)} to {_fmt(s['max'], cur)}, {s['count']} observations)")
    elif "capital_value" in m:
        s = m["capital_value"]
        val_bits.append(f"valuation opinions averaged {_fmt(s['mean'], cur)} (median {_fmt(s['median'], cur)})")
    if "annual_rent" in m:
        val_bits.append(f"annual rents averaged {_fmt(m['annual_rent']['mean'], cur)}")
    if "yield_pct" in m:
        s = m["yield_pct"]
        val_bits.append(f"yields ranged from {_fmt(s['min'], pct=True)} to {_fmt(s['max'], pct=True)} with a mean of {_fmt(s['mean'], pct=True)}")
    if val_bits:
        paras.append("Valuation evidence indicates that " + "; ".join(val_bits) + ".")
    if "cost_per_sqm" in m:
        s = m["cost_per_sqm"]
        text = (f"Construction cost evidence shows an average of {_fmt(s['mean'], cur)} per m² of gross floor area, "
                f"ranging from {_fmt(s['min'], cur)} to {_fmt(s['max'], cur)} per m².")
        t = trends.get("cost_per_sqm") or []
        if len(t) >= 2 and t[-1].get("change_pct") is not None:
            text += f" The most recent period ({t[-1]['period']}) moved {t[-1]['change_pct']:+.1f}% on the previous period."
        paras.append(text)
    housing = []
    if "avg_price" in m:
        housing.append(f"average house prices of {_fmt(m['avg_price']['mean'], cur)}")
    if "avg_monthly_rent" in m:
        housing.append(f"average monthly rents of {_fmt(m['avg_monthly_rent']['mean'], cur)}")
    if "affordability_ratio" in m:
        housing.append(f"an average affordability ratio of {m['affordability_ratio']['mean']:.1f}")
    if housing:
        paras.append("Housing market data records " + ", ".join(housing) + ".")
    t = trends.get("value_per_sqm") or []
    if len(t) >= 2:
        first, last = t[0], t[-1]
        if first["mean"]:
            change = (last["mean"] - first["mean"]) / first["mean"] * 100
            direction = "increased" if change > 0 else "decreased"
            paras.append(f"Between {first['period']} and {last['period']} average capital values per m² {direction} by {abs(change):.1f}%.")
    paras.append("These figures are derived from the firm's own report data and should be read alongside current market enquiries.")
    return "\n\n".join(p.strip() for p in paras if p.strip())
