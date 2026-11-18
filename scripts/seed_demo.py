"""Load illustrative demo records so the dashboards have something to show.

All demo records are tagged "demo" and can be removed with --clear.
Usage:  python -m scripts.seed_demo [--clear]
"""
from __future__ import annotations

import random
import sys
from datetime import date

from sqlalchemy import String, cast, delete

from app.database import SessionLocal
from app.main import init_db
from app.models import Record

AREAS = {  # location: (city, base value/m² GHS, base rent/m² p.a.)
    "East Legon": ("Accra", 9500, 620),
    "Cantonments": ("Accra", 13500, 900),
    "Airport Residential": ("Accra", 14500, 980),
    "Spintex": ("Accra", 6500, 430),
    "Tema Community 25": ("Tema", 4800, 300),
    "Kumasi Ahodwo": ("Kumasi", 5200, 330),
}
TYPES = ["residential", "commercial"]


def quarter_dates():
    for year in (2023, 2024, 2025):
        for month in (2, 5, 8, 11):
            yield date(year, month, 15)


def main(clear_only: bool) -> None:
    init_db()
    db = SessionLocal()
    db.execute(delete(Record).where(cast(Record.tags, String).like('%"demo"%')))
    db.commit()
    if clear_only:
        print("Demo records removed.")
        return
    rng = random.Random(42)
    n = 0
    for i, d in enumerate(quarter_dates()):
        growth = 1 + 0.035 * i  # ~3.5% per quarter in cedi terms
        for loc, (city, v, r) in AREAS.items():
            for ptype in TYPES:
                size = rng.choice([180, 250, 320, 450]) if ptype == "residential" else rng.choice([600, 1200, 2500])
                vpsm = v * growth * (1.25 if ptype == "commercial" else 1) * rng.uniform(0.9, 1.1)
                rent = r * growth * (1.3 if ptype == "commercial" else 1) * rng.uniform(0.9, 1.1) * size
                db.add(Record(category="valuation", location=loc, city=city, region="Greater Accra" if city != "Kumasi" else "Ashanti",
                              property_type=ptype, record_date=d, currency="GHS", size_sqm=size,
                              valuation_basis="both", method="Comparison Method",
                              capital_value=round(vpsm * size, -3), annual_rent=round(rent, -2),
                              value_per_sqm=round(vpsm, 2), rent_per_sqm=round(rent / size, 2),
                              yield_pct=round(rent / (vpsm * size) * 100, 2),
                              purpose=rng.choice(["mortgage", "sale", "insurance", "financial reporting"]),
                              address=f"Demo property {n + 1}, {loc}", tags=["demo"]))
                n += 1
            cps = 4200 * (1 + 0.04 * i) * rng.uniform(0.9, 1.1) * (1.15 if v > 9000 else 1)
            gfa = rng.choice([300, 800, 1500])
            db.add(Record(category="cost", location=loc, city=city, property_type=rng.choice(TYPES), record_date=d,
                          currency="GHS", size_sqm=gfa, total_cost=round(cps * gfa, -3), cost_per_sqm=round(cps, 2),
                          specification=rng.choice(["standard", "high", "luxury"]), title=f"Demo cost plan, {loc}",
                          cost_breakdown={"preliminaries": round(cps * gfa * .1), "substructure": round(cps * gfa * .15),
                                          "superstructure": round(cps * gfa * .35), "finishes": round(cps * gfa * .2),
                                          "services": round(cps * gfa * .15), "external_works": round(cps * gfa * .05)},
                          tags=["demo"]))
            if d.month == 11:
                db.add(Record(category="housing", location=loc, city=city, property_type="residential", record_date=d,
                              currency="GHS", avg_price=round(v * growth * 250, -3), avg_monthly_rent=round(r * growth * 250 / 12, -1),
                              supply_units=rng.randint(40, 400), demand_level=rng.choice(["high", "moderate"]),
                              affordability_ratio=round(rng.uniform(8, 18), 1), title=f"Demo housing review, {loc}", tags=["demo"]))
            n += 2
    db.commit()
    print(f"Inserted demo records (tagged 'demo').")


if __name__ == "__main__":
    main("--clear" in sys.argv)
