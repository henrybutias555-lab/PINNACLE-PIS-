"""Gap filling: complete blank fields from what a record and its report already say."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user, log_activity, require_role
from ..config import settings
from ..database import get_db
from ..models import Record, Report, User
from ..schemas import RecordOut
from ..services import gaps, llm
from ..services.query import RecordFilters, filtered_records, filters_dep
from .records import apply_changes

router = APIRouter(prefix="/api/gaps", tags=["gap filling"])


class FillIn(BaseModel):
    record: dict
    report_id: int | None = None
    use_ai: bool = True


def _report_text(db: Session, report_id: int | None) -> tuple[Report | None, str]:
    if not report_id:
        return None, ""
    report = db.get(Report, report_id)
    return report, (report.raw_text or "") if report else ""


def _ai_fill(report: Report, current: dict) -> dict[str, tuple[object, str]]:
    """Ask the AI to read the report again and keep only values for blank fields."""
    suffix = Path(report.filename).suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"report{suffix}"
        path.write_bytes(report.file_data or b"")
        draft = llm.extract_report(path, report.file_type, report.raw_text or "", settings.default_currency)
    records = draft.get("records") or []
    same = [r for r in records if r.get("category") == current.get("category")] or records
    if not same:
        return {}
    name = {"gemini": "Gemini", "claude": "Claude"}.get(settings.ai_provider or "", "AI")
    out = {}
    for field, value in same[0].items():
        if field in ("category", "autofilled") or not gaps._blank(current.get(field)) or gaps._blank(value):
            continue
        out[field] = (value, f"{name} read it from the report")
    return out


@router.post("/fill")
def fill(body: FillIn, user: User = Depends(require_role("junior")), db: Session = Depends(get_db)):
    """Fill the blank fields of a record (as currently shown in the form). Nothing is saved."""
    report, text = _report_text(db, body.report_id)
    current = dict(body.record)
    filled = gaps.infer(current, text)
    merged = gaps.apply(current, filled)
    warning = None
    if body.use_ai and settings.ai_enabled and report is not None:
        try:
            ai = _ai_fill(report, merged)
            filled.update(ai)
            merged = gaps.apply(merged, ai)
            more = gaps.infer(merged, text)  # e.g. region from an MMDA the AI found
            filled.update(more)
            merged = gaps.apply(merged, more)
        except llm.AIError as exc:
            warning = f"AI could not help this time ({exc}); filled what the built-in reader could."
    return {"record": merged, "filled": {k: {"value": v, "reason": r} for k, (v, r) in filled.items()},
            "ai_used": bool(settings.ai_enabled and report is not None and body.use_ai), "warning": warning}


def _bulk_plan(db: Session, f: RecordFilters) -> list[dict]:
    plan = []
    texts: dict[int, str] = {}
    for rec in filtered_records(db, f, limit=2000):
        current = RecordOut.model_validate(rec).model_dump(mode="json")
        if rec.report_id and rec.report_id not in texts:
            texts[rec.report_id] = _report_text(db, rec.report_id)[1]
        filled = gaps.infer(current, texts.get(rec.report_id, ""))
        if filled:
            plan.append({"id": rec.id, "label": rec.address or rec.title or rec.location or f"Record {rec.id}",
                         "filled": {k: {"value": v, "reason": r} for k, (v, r) in filled.items()}})
    return plan


@router.get("/bulk/preview")
def bulk_preview(f: RecordFilters = Depends(filters_dep), _: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """What filling gaps would change across the filtered records. Nothing is saved."""
    plan = _bulk_plan(db, f)
    return {"records": plan, "fields": sum(len(p["filled"]) for p in plan)}


@router.post("/bulk/apply")
def bulk_apply(request: Request, f: RecordFilters = Depends(filters_dep), user: User = Depends(require_role("junior")),
               db: Session = Depends(get_db)):
    plan = _bulk_plan(db, f)
    count = 0
    for item in plan:
        rec = db.get(Record, item["id"])
        changes = {k: v["value"] for k, v in item["filled"].items()}
        try:
            apply_changes(rec, changes)
            count += len(changes)
        except Exception as exc:  # one bad record must not stop the rest
            raise HTTPException(422, f"Record {item['id']}: {exc}") from exc
    db.commit()
    log_activity(db, user, "gaps_filled", f"{len(plan)} records", f"{count} fields filled; {f.describe()}"[:2000], request)
    return {"records": len(plan), "fields": count}
