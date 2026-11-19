"""Shared record filtering used by search, analytics and exports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import Query
from sqlalchemy import Select, func, or_, select

from ..models import Record

RANGE_FIELDS = {
    "valuation": "capital_value",
    "cost": "cost_per_sqm",
    "housing": "avg_price",
}


@dataclass
class RecordFilters:
    q: str | None = None
    category: str | None = None
    location: str | None = None
    district: str | None = None
    city: str | None = None
    property_type: str | None = None
    purpose: str | None = None
    basis_of_value: str | None = None
    tag: str | None = None
    currency: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    report_id: int | None = None

    def describe(self) -> str:
        parts = []
        if self.category:
            parts.append({"valuation": "valuation", "cost": "construction cost", "housing": "housing"}[self.category] + " data")
        if self.property_type:
            parts.append(f"{self.property_type} property")
        if self.location:
            parts.append(f"in {self.location}")
        if self.district:
            parts.append(f"{'' if self.location else 'in '}{self.district}".strip())
        if self.city:
            parts.append(f"{'' if self.location else 'in '}{self.city}".strip())
        if self.basis_of_value:
            parts.append(f"basis of value: {self.basis_of_value}")
        if self.purpose:
            parts.append(f"for {self.purpose} purposes")
        if self.date_from or self.date_to:
            parts.append(f"dated {self.date_from or 'earliest'} to {self.date_to or 'latest'}")
        return ", ".join(parts) or "all records in the database"


def filters_dep(
    q: str | None = Query(None, description="Free-text search"),
    category: str | None = Query(None, pattern="^(valuation|cost|housing)$"),
    location: str | None = None,
    district: str | None = None,
    city: str | None = None,
    property_type: str | None = None,
    purpose: str | None = None,
    basis_of_value: str | None = Query(None, description="e.g. Market Value"),
    tag: str | None = None,
    currency: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    min_amount: float | None = Query(None, description="Min value/cost (capital value, cost per m² or avg price by category)"),
    max_amount: float | None = None,
    report_id: int | None = None,
) -> RecordFilters:
    return RecordFilters(q=q, category=category, location=location, district=district, city=city,
                         property_type=property_type, purpose=purpose, basis_of_value=basis_of_value, tag=tag,
                         currency=currency,
                         date_from=date_from, date_to=date_to, min_amount=min_amount, max_amount=max_amount,
                         report_id=report_id)


def _ilike(col, value: str):
    return func.lower(col).like(f"%{value.lower().strip()}%")


def apply_filters(stmt: Select, f: RecordFilters) -> Select:
    if f.category:
        stmt = stmt.where(Record.category == f.category)
    if f.location:
        stmt = stmt.where(or_(_ilike(Record.location, f.location), _ilike(Record.address, f.location)))
    if f.district:
        stmt = stmt.where(_ilike(Record.district, f.district))
    if f.city:
        stmt = stmt.where(_ilike(Record.city, f.city))
    if f.property_type:
        stmt = stmt.where(_ilike(Record.property_type, f.property_type))
    if f.purpose:
        stmt = stmt.where(_ilike(Record.purpose, f.purpose))
    if f.basis_of_value:
        if f.basis_of_value == "Forced Sale Value":
            # FSV is usually reported alongside a market value
            stmt = stmt.where(or_(Record.basis_of_value == "Forced Sale Value", Record.forced_sale_value.is_not(None)))
        elif f.basis_of_value == "Market Rent":
            stmt = stmt.where(or_(Record.basis_of_value == "Market Rent", Record.annual_rent.is_not(None)))
        elif f.basis_of_value == "Not stated":
            stmt = stmt.where(Record.basis_of_value.is_(None))
        else:
            stmt = stmt.where(Record.basis_of_value == f.basis_of_value)
    if f.currency:
        stmt = stmt.where(Record.currency == f.currency.upper())
    if f.date_from:
        stmt = stmt.where(Record.record_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(Record.record_date <= f.date_to)
    if f.report_id:
        stmt = stmt.where(Record.report_id == f.report_id)
    if f.tag:
        # tags are stored as a JSON list; a text match is portable across SQLite/Postgres
        from sqlalchemy import String, cast
        stmt = stmt.where(_ilike(cast(Record.tags, String), f'"{f.tag}'))
    if f.q:
        terms = [t for t in f.q.split() if t]
        for term in terms:
            stmt = stmt.where(or_(
                _ilike(Record.title, term), _ilike(Record.address, term), _ilike(Record.location, term),
                _ilike(Record.city, term), _ilike(Record.district, term), _ilike(Record.region, term),
                _ilike(Record.property_type, term), _ilike(Record.notes, term), _ilike(Record.gps_address, term),
                _ilike(Record.valuer, term), _ilike(Record.basis_of_value, term), _ilike(Record.owner, term),
                _ilike(Record.property_name, term), _ilike(Record.tenure, term), _ilike(Record.building_type, term),
                _ilike(Record.client, term), _ilike(Record.method, term), _ilike(Record.purpose, term),
            ))
    if f.min_amount is not None or f.max_amount is not None:
        cols = [getattr(Record, RANGE_FIELDS[f.category])] if f.category else [
            Record.capital_value, Record.cost_per_sqm, Record.avg_price]
        conds = []
        for col in cols:
            c = col.is_not(None)
            if f.min_amount is not None:
                c = c & (col >= f.min_amount)
            if f.max_amount is not None:
                c = c & (col <= f.max_amount)
            conds.append(c)
        stmt = stmt.where(or_(*conds))
    return stmt


def filtered_records(db, f: RecordFilters, limit: int | None = None) -> list[Record]:
    stmt = apply_filters(select(Record), f).order_by(Record.record_date.desc().nullslast(), Record.id.desc())
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))
