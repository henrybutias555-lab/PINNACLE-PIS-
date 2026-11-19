"""Excel round trip: upload an edited PIS workbook to update records.

Flow: export Excel -> edit it in Excel (phone or laptop) -> upload it here.
``preview`` shows exactly what would change; ``apply`` makes the changes.
Rows are matched by the "Ref" column; rows without a Ref are added as new records.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..auth import log_activity, require_role
from ..database import get_db
from ..models import Record, User
from ..schemas import RecordIn, RecordOut
from ..schemas import is_not_stated
from ..services.export import COLUMNS, RECORD_COLUMNS
from .records import apply_changes, record_from_payload

router = APIRouter(prefix="/api/imports", tags=["imports"])

MAX_ROWS = 5000
EDITABLE = set(RecordIn.model_fields) - {"comparables", "cost_breakdown", "cost_items"}
DATE_FIELDS = {"record_date", "inspection_date"}
NUMERIC = {"forced_sale_value", "size_sqm", "capital_value", "annual_rent", "yield_pct", "value_per_sqm", "rent_per_sqm", "total_cost",
           "cost_per_sqm", "external_works_rate", "avg_price", "avg_monthly_rent", "affordability_ratio"}
INTEGER = {"supply_units"}
# Figures the system calculates: always recomputed from their inputs when the inputs are present,
# so a stale value in an old export can never overwrite a fresh one.
DERIVED = {"value_per_sqm": ("capital_value", "size_sqm"), "rent_per_sqm": ("annual_rent", "size_sqm"),
           "cost_per_sqm": ("total_cost", "size_sqm"), "yield_pct": ("annual_rent", "capital_value")}


def _drop_derived(values: dict, current: dict | None) -> None:
    for field, inputs in DERIVED.items():
        if field not in values:
            continue
        final = [values.get(i, (current or {}).get(i)) for i in inputs]
        if all(v not in (None, 0) for v in final):
            del values[field]


def _duplicate_of(db: Session, values: dict) -> int | None:
    """An existing record that a 'new' row duplicates (e.g. the same file uploaded twice)."""
    from sqlalchemy import select

    keys = ("category", "location", "address", "record_date", "capital_value", "total_cost", "avg_price", "size_sqm")
    probe = {k: values.get(k) for k in keys if values.get(k) not in (None, "")}
    if len(probe) < 3:
        return None
    stmt = select(Record.id)
    for k, v in probe.items():
        stmt = stmt.where(getattr(Record, k) == v)
    return db.scalar(stmt.limit(1))


def _norm(label) -> str:
    return re.sub(r"[^a-z0-9²]+", " ", str(label or "").lower()).strip()


# Header label (as exported) -> field name. Old labels are kept so older exports still import.
HEADER_FIELDS: dict[str, str] = {"ref": "id"}
for _label, _field, _fmt in RECORD_COLUMNS:
    HEADER_FIELDS[_norm(_label)] = _field
for _cols in COLUMNS.values():
    for _label, _field in _cols:
        HEADER_FIELDS.setdefault(_norm(_label), _field)
HEADER_FIELDS.update({_norm("Capital value"): "capital_value", _norm("Basis"): "valuation_basis",
                      _norm("Project"): "title", _norm("Area"): "location", _norm("Type"): "property_type",
                      _norm("GFA (m²)"): "size_sqm", _norm("Avg price"): "avg_price",
                      _norm("Avg monthly rent"): "avg_monthly_rent", _norm("Affordability"): "affordability_ratio"})


def _cell(field: str, value):
    """Convert a spreadsheet cell into the field's Python value (None for blank)."""
    if value is None or (isinstance(value, str) and (not value.strip() or is_not_stated(value))):
        return None   # blank, "—" or "Not stated" in the exported workbook
    if field in DATE_FIELDS:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                pass
        raise ValueError(f"'{text}' is not a date (use YYYY-MM-DD)")
    if field in NUMERIC or field in INTEGER:
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            cleaned = re.sub(r"[^0-9.\-]", "", str(value))
            if not cleaned:
                raise ValueError(f"'{value}' is not a number")
            number = float(cleaned)
        return int(round(number)) if field in INTEGER else number
    if field == "tags":
        return [t.strip() for t in str(value).split(",") if t.strip()] or None
    if field == "category":
        return str(value).strip().lower()
    return str(value).strip()


def _read_rows(content: bytes):
    """Find the sheet with a 'Ref' header and return (sheet name, header fields, rows)."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        raise HTTPException(400, "This is not an Excel (.xlsx) file the system can read") from exc
    preferred = ["All records", "Records", "All fields", "Evidence", "Valuation evidence", "Cost evidence", "Housing data"]
    names = [n for n in preferred if n in wb.sheetnames] + [n for n in wb.sheetnames if n not in preferred]
    for name in names:
        rows = list(wb[name].iter_rows(values_only=True))
        for i, row in enumerate(rows[:15]):
            labels = [_norm(c) for c in row]
            if "ref" in labels:
                fields = [HEADER_FIELDS.get(lbl) for lbl in labels]
                data = [r for r in rows[i + 1:] if any(c not in (None, "") for c in r)]
                if len(data) > MAX_ROWS:
                    raise HTTPException(413, f"Too many rows (max {MAX_ROWS})")
                return name, fields, data, i + 2  # first data row number in Excel
    raise HTTPException(400, "No sheet with a 'Ref' column found. Upload a workbook exported from PIS.")


def _plan(db: Session, content: bytes) -> dict:
    sheet, fields, rows, first_row = _read_rows(content)
    plan = {"sheet": sheet, "updates": [], "creates": [], "unchanged": 0, "errors": []}
    for offset, row in enumerate(rows):
        excel_row = first_row + offset
        values, problems = {}, []
        for field, raw in zip(fields, row):
            if field is None or (field not in EDITABLE and field != "id"):
                continue
            try:
                values[field] = _cell(field, raw)
            except ValueError as exc:
                problems.append(f"{field.replace('_', ' ')}: {exc}")
        if problems:
            plan["errors"].append({"row": excel_row, "error": "; ".join(problems)})
            continue
        ref = values.pop("id", None)
        if ref is not None:
            try:
                rec = db.get(Record, int(ref))
            except (TypeError, ValueError):
                rec = None
            if rec is None:
                plan["errors"].append({"row": excel_row, "error": f"Ref {ref} not found"})
                continue
            current = RecordOut.model_validate(rec).model_dump(mode="python")
            _drop_derived(values, current)
            changes = {}
            for field, new in values.items():
                old = current.get(field)
                same = (old == new) or (old in (None, "", []) and new in (None, "", [])) or (
                    isinstance(old, (int, float)) and isinstance(new, (int, float)) and abs(old - new) < 0.005)
                if field == "tags" and old and new:
                    same = sorted(t.lower() for t in old) == sorted(t.lower() for t in new)
                if not same:
                    changes[field] = [old, new]
            if changes and rec.report_id:
                plan["errors"].append({"row": excel_row, "error": f"Ref {rec.id} comes from approved report "
                                       f"#{rec.report_id} and is locked; re-run that report's extraction to change it"})
                continue
            if changes:
                try:
                    RecordIn.model_validate({**current, **{k: new for k, (_, new) in changes.items()}})
                except ValidationError as exc:
                    first = exc.errors()[0]
                    where = ".".join(str(x) for x in first.get("loc", []))
                    plan["errors"].append({"row": excel_row, "error": f"Ref {rec.id}: {where} {first.get('msg', '')}".strip()})
                    continue
                plan["updates"].append({"id": rec.id, "row": excel_row,
                                        "label": rec.address or rec.title or rec.location or f"Record {rec.id}",
                                        "changes": changes})
            else:
                plan["unchanged"] += 1
        else:
            payload = {k: v for k, v in values.items() if v is not None}
            _drop_derived(payload, None)
            dup = _duplicate_of(db, payload)
            if dup:
                plan["errors"].append({"row": excel_row, "error": f"Looks like existing record Ref {dup} "
                                       "(already imported?). Put the Ref in the first column to update it instead."})
                continue
            try:
                RecordIn.model_validate(payload)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(x) for x in first.get("loc", []))
                plan["errors"].append({"row": excel_row, "error": f"New record: {where} {first.get('msg', '')}".strip()})
                continue
            plan["creates"].append({"row": excel_row, "values": payload})
    return plan


def _json(plan: dict) -> dict:
    def conv(v):
        return v.isoformat() if isinstance(v, (date, datetime)) else v
    return {
        **plan,
        "updates": [{**u, "changes": {k: [conv(a), conv(b)] for k, (a, b) in u["changes"].items()}} for u in plan["updates"]],
        "creates": [{**c, "values": {k: conv(v) for k, v in c["values"].items()}} for c in plan["creates"]],
    }


async def _content(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 20 MB)")
    return data


@router.post("/records/preview")
async def preview(file: UploadFile = File(...), _: User = Depends(require_role("junior")), db: Session = Depends(get_db)):
    """What uploading this workbook would change. Nothing is saved."""
    return _json(_plan(db, await _content(file)))


@router.post("/records/apply")
async def apply(request: Request, file: UploadFile = File(...), user: User = Depends(require_role("junior")),
                db: Session = Depends(get_db)):
    plan = _plan(db, await _content(file))
    for update in plan["updates"]:
        rec = db.get(Record, update["id"])
        apply_changes(rec, {k: new for k, (_, new) in update["changes"].items()})
    created = []
    for item in plan["creates"]:
        rec = record_from_payload(RecordIn.model_validate(item["values"]), user)
        db.add(rec)
        created.append(rec)
    db.commit()
    detail = f"{len(plan['updates'])} updated, {len(created)} added, {len(plan['errors'])} rows skipped"
    log_activity(db, user, "excel_imported", plan["sheet"], detail, request)
    return {"updated": len(plan["updates"]), "created": len(created), "unchanged": plan["unchanged"],
            "errors": plan["errors"], "sheet": plan["sheet"]}
