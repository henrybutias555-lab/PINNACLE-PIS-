"""Structured data: search, filter, create, edit, tag and delete records."""
from __future__ import annotations

import re

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


RATING_FIELDS = ("location_rating", "accessibility_rating", "neighbourhood_rating", "overall_marketability")


TOP_GRADE = "Excellent / Very good"


def normalise_rating(value: str | None, kind: str = "rating") -> str | None:
    """'The property ... appears to be in a good structural and tenantable condition.' -> 'Good'."""
    if value is None or not str(value).strip():
        return None
    v = str(value).strip()
    low = v.lower()
    if kind == "conformity":
        if re.search(r"non[- ]?conform|not conform|does not conform|contravene", low):
            return "Non-conforming"
        if re.search(r"partly|partially", low) and "conform" in low:
            return "Partly conforming"
        if "conform" in low or "in line with" in low or "compatible" in low:
            return "Conforming"
        return v[:255]
    if low == TOP_GRADE.lower():
        return TOP_GRADE
    # Reports use "very good" and "excellent" for the same top grade: one grade here.
    words = ["very good", "excellent", "outstanding", "good", "fair", "poor", "dilapidated"] if kind == "repair" else \
        ["very good", "excellent", "outstanding", "good", "fair", "poor"]
    for w in words:
        if re.search(rf"\b{w}\b", low):
            return TOP_GRADE if w in ("very good", "excellent", "outstanding") else w.capitalize()
    if kind == "repair":
        if re.search(r"newly built|new building|brand new|well maintained|tenantable|sound", low):
            return "Good"
        if re.search(r"dilapidat|derelict|ruin", low):
            return "Dilapidated"
        if re.search(r"bad|deteriorat|needs? repair|defect", low):
            return "Poor"
    return v[:255]


def _fit_text(rec: Record) -> None:
    """Never let an over-long value break a save: trim to the column's size."""
    from sqlalchemy import String

    for column in Record.__table__.columns:
        length = getattr(column.type, "length", None)
        if isinstance(column.type, String) and length:
            value = getattr(rec, column.name)
            if isinstance(value, str) and len(value) > length:
                setattr(rec, column.name, value[: length - 1].rstrip() + "…")


def _derive(rec: Record) -> None:
    """Fill derivable figures (rates per m², yield) when the inputs exist."""
    for field in RATING_FIELDS:
        setattr(rec, field, normalise_rating(getattr(rec, field)))
    rec.state_of_repair = normalise_rating(rec.state_of_repair, "repair")
    rec.user_conformity = normalise_rating(rec.user_conformity, "conformity")
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
    if rec.basis_of_value:
        from ..services.rules import normalise_basis
        rec.basis_of_value = normalise_basis(rec.basis_of_value)
    _fit_text(rec)
    if rec.comparables:
        comps = []
        for c in rec.comparables:
            c = dict(c or {})
            if c.get("price") and c.get("size_sqm") and not c.get("price_per_sqm"):
                c["price_per_sqm"] = round(float(c["price"]) / float(c["size_sqm"]), 2)
            comps.append(c)
        rec.comparables = comps
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


def apply_changes(rec: Record, body: dict) -> None:
    """Validate and apply a partial update, recomputing figures derived from changed inputs.
    Raises pydantic.ValidationError for invalid values."""
    current = RecordIn.model_validate(RecordOut.model_validate(rec).model_dump()).model_dump()
    merged = RecordIn.model_validate({**current, **body})
    data = merged.model_dump(mode="python")
    if data.get("comparables") is not None:
        data["comparables"] = merged.model_dump(mode="json")["comparables"]
    for key in body:
        setattr(rec, key, data[key])

    def same(a, b):
        return a == b or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) < 0.005)

    # A calculated figure is recalculated when one of its inputs changed, unless the user typed a new
    # value for it. (Forms send every field back, so an unchanged old figure must not block this.)
    for derived, inputs in (("value_per_sqm", ("size_sqm", "capital_value")), ("rent_per_sqm", ("size_sqm", "annual_rent")),
                            ("cost_per_sqm", ("size_sqm", "total_cost")), ("yield_pct", ("capital_value", "annual_rent"))):
        inputs_changed = any(k in body and not same(current.get(k), data.get(k)) for k in inputs)
        user_set_it = derived in body and not same(current.get(derived), data.get(derived))
        if inputs_changed and not user_set_it:
            setattr(rec, derived, None)
    if "basis_of_value" in body and rec.basis_of_value:
        from ..services.rules import normalise_basis
        rec.basis_of_value = normalise_basis(rec.basis_of_value)
    _derive(rec)


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


LOCKED_MESSAGE = ("This record comes from approved report #{rid} and is locked. To change it, open that report "
                  "in Upload & Review, tap Re-run extraction, check the new reading and tap Update saved records.")


def _refuse_if_locked(rec: Record) -> None:
    """Records saved from an approved report change only through a new extraction of that report."""
    if rec.report_id:
        raise HTTPException(423, LOCKED_MESSAGE.format(rid=rec.report_id))


@router.patch("/{record_id}", response_model=RecordOut)
def update_record(record_id: int, body: dict, request: Request, user: User = Depends(require_role("junior")),
                  db: Session = Depends(get_db)):
    rec = db.get(Record, record_id)
    if rec is None:
        raise HTTPException(404, "Record not found")
    _refuse_if_locked(rec)
    body = {k: v for k, v in body.items() if k != "locked"}   # read-only, echoed back by forms
    unknown = set(body) - set(RecordIn.model_fields)
    if unknown:
        raise HTTPException(422, f"Unknown fields: {', '.join(sorted(unknown))}")
    try:
        apply_changes(rec, body)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False))
    db.commit()
    log_activity(db, user, "record_updated", f"record:{rec.id}", ", ".join(sorted(body)), request)
    return rec


@router.delete("/{record_id}", status_code=204)
def delete_record(record_id: int, request: Request, user: User = Depends(require_role("senior")),
                  db: Session = Depends(get_db)):
    rec = db.get(Record, record_id)
    if rec is None:
        raise HTTPException(404, "Record not found")
    _refuse_if_locked(rec)
    db.delete(rec)
    db.commit()
    log_activity(db, user, "record_deleted", f"record:{record_id}", request=request)
