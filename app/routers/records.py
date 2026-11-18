"""Structured data: search, filter, create, edit, tag and delete records."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user, log_activity, require_role
from ..database import get_db
from ..models import Record, User
from ..schemas import RecordIn, RecordOut
from ..services import places
from ..services.query import RecordFilters, apply_filters, filters_dep

router = APIRouter(prefix="/api/records", tags=["records"])

SORTABLE = {
    "date": Record.record_date, "created": Record.created_at, "location": Record.location,
    "capital_value": Record.capital_value, "cost_per_sqm": Record.cost_per_sqm,
    "value_per_sqm": Record.value_per_sqm, "avg_price": Record.avg_price, "yield": Record.yield_pct,
    "size": Record.size_sqm,
}


def _derive(rec: Record) -> None:
    """Fill derivable figures (rates per m², yield) when the inputs exist."""
    if rec.size_sqm:
        if rec.capital_value and not rec.value_per_sqm:
            rec.value_per_sqm = round(rec.capital_value / rec.size_sqm, 2)
        if rec.annual_rent and not rec.rent_per_sqm:
            rec.rent_per_sqm = round(rec.annual_rent / rec.size_sqm, 2)
        if rec.total_cost and not rec.cost_per_sqm:
            rec.cost_per_sqm = round(rec.total_cost / rec.size_sqm, 2)
    if rec.capital_value and rec.annual_rent and not rec.yield_pct:
        rec.yield_pct = round(rec.annual_rent / rec.capital_value * 100, 2)
    if rec.currency:
        rec.currency = rec.currency.upper()
    if rec.tags:
        rec.tags = sorted({t.strip().lower() for t in rec.tags if t and t.strip()})
    if rec.property_type:
        rec.property_type = rec.property_type.strip().lower()


def record_from_payload(payload: RecordIn, user: User) -> Record:
    data = payload.model_dump(mode="python")
    if data.get("comparables") is not None:
        data["comparables"] = [c for c in payload.model_dump(mode="json")["comparables"]]
    rec = Record(**data, created_by_id=user.id)
    _derive(rec)
    return rec


@router.get("", response_model=dict)
def search(
    f: RecordFilters = Depends(filters_dep),
    sort: str = "date",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    page_size = max(1, min(page_size, 200))
    page = max(1, page)
    base = apply_filters(select(Record), f)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    col = SORTABLE.get(sort, Record.record_date)
    col = col.asc().nullslast() if order == "asc" else col.desc().nullslast()
    rows = db.scalars(base.order_by(col, Record.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [RecordOut.model_validate(r).model_dump(mode="json") for r in rows],
    }


@router.get("/facets")
def facets(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Distinct values for filter drop-downs."""
    def distinct(col):
        return sorted(v for v in db.scalars(select(col).distinct()) if v)

    tags: set[str] = set()
    for t in db.scalars(select(Record.tags).where(Record.tags.is_not(None))):
        tags.update(t or [])
    return {
        "locations": distinct(Record.location),
        "cities": distinct(Record.city),
        "districts": sorted(set(distinct(Record.district)) | set(places.ALL_MMDAS)),
        "property_types": distinct(Record.property_type),
        "purposes": distinct(Record.purpose),
        "currencies": distinct(Record.currency),
        "tags": sorted(tags),
    }


@router.get("/{record_id}", response_model=RecordOut)
def get_record(record_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = db.get(Record, record_id)
    if rec is None:
        raise HTTPException(404, "Record not found")
    return rec


@router.post("", response_model=RecordOut, status_code=201)
def create_record(body: RecordIn, request: Request, user: User = Depends(require_role("junior")),
                  db: Session = Depends(get_db)):
    rec = record_from_payload(body, user)
    db.add(rec)
    db.commit()
    log_activity(db, user, "record_created", f"record:{rec.id}", rec.title or rec.address, request)
    return rec


@router.patch("/{record_id}", response_model=RecordOut)
def update_record(record_id: int, body: dict, request: Request, user: User = Depends(require_role("junior")),
                  db: Session = Depends(get_db)):
    rec = db.get(Record, record_id)
    if rec is None:
        raise HTTPException(404, "Record not found")
    current = RecordIn.model_validate(RecordOut.model_validate(rec).model_dump()).model_dump()
    allowed = set(RecordIn.model_fields)
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(422, f"Unknown fields: {', '.join(sorted(unknown))}")
    try:
        merged = RecordIn.model_validate({**current, **body})
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False))
    data = merged.model_dump(mode="python")
    if data.get("comparables") is not None:
        data["comparables"] = merged.model_dump(mode="json")["comparables"]
    for key in body:
        setattr(rec, key, data[key])
    # Recompute derived values that depend on changed inputs.
    if {"size_sqm", "capital_value"} & set(body) and "value_per_sqm" not in body:
        rec.value_per_sqm = None
    if {"size_sqm", "annual_rent"} & set(body) and "rent_per_sqm" not in body:
        rec.rent_per_sqm = None
    if {"size_sqm", "total_cost"} & set(body) and "cost_per_sqm" not in body:
        rec.cost_per_sqm = None
    if {"capital_value", "annual_rent"} & set(body) and "yield_pct" not in body:
        rec.yield_pct = None
    _derive(rec)
    db.commit()
    log_activity(db, user, "record_updated", f"record:{rec.id}", ", ".join(sorted(body)), request)
    return rec


@router.delete("/{record_id}", status_code=204)
def delete_record(record_id: int, request: Request, user: User = Depends(require_role("senior")),
                  db: Session = Depends(get_db)):
    rec = db.get(Record, record_id)
    if rec is None:
        raise HTTPException(404, "Record not found")
    db.delete(rec)
    db.commit()
    log_activity(db, user, "record_deleted", f"record:{record_id}", request=request)
