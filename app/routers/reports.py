"""Report ingestion: upload, AI extraction, review and confirmation."""
from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user, log_activity, require_role
from ..config import settings
from ..database import SessionLocal, get_db
from ..models import Record, Report, User
from ..schemas import ConfirmIn, RecordOut, ReportOut
from ..services.ingest import process_report
from ..services.text_extract import file_type_for
from .records import record_from_payload

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _out(db: Session, report: Report) -> ReportOut:
    out = ReportOut.model_validate(report)
    out.record_count = db.scalar(select(func.count()).select_from(Record).where(Record.report_id == report.id)) or 0
    return out


def _process_in_background(report_id: int) -> None:
    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        if report is not None:
            process_report(db, report)
    finally:
        db.close()


@router.post("/upload", response_model=list[ReportOut], status_code=201)
async def upload(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    sync: bool = False,
    user: User = Depends(require_role("junior")),
    db: Session = Depends(get_db),
):
    """Upload one or many reports. Extraction runs in the background unless ``sync=true``
    (always synchronous on serverless hosts such as Vercel)."""
    created: list[Report] = []
    limit = int(settings.max_upload_mb * 1024 * 1024)
    for f in files:
        ftype = file_type_for(f.filename or "")
        if ftype is None:
            raise HTTPException(415, f"Unsupported file type: {f.filename}. Use PDF, DOCX, TXT or an image.")
        data = await f.read()
        if len(data) > limit:
            raise HTTPException(413, f"{f.filename} exceeds the {settings.max_upload_mb:g} MB upload limit")
        safe_name = Path(f.filename).name
        report = Report(filename=safe_name, file_data=data, file_size=len(data), file_type=ftype,
                        uploaded_by_id=user.id)
        db.add(report)
        db.commit()
        created.append(report)
        log_activity(db, user, "report_uploaded", safe_name, f"report_id={report.id}", request)

    for report in created:
        if sync or settings.sync_extraction:
            process_report(db, report)
        else:
            background.add_task(_process_in_background, report.id)
    return [_out(db, r) for r in created]


@router.get("", response_model=list[ReportOut])
def list_reports(status: str | None = None, limit: int = 100, _: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    stmt = select(Report).order_by(Report.id.desc()).limit(min(limit, 500))
    if status:
        stmt = stmt.where(Report.status == status)
    return [_out(db, r) for r in db.scalars(stmt)]


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    return _out(db, report)


@router.get("/{report_id}/file")
def download_file(report_id: int, request: Request, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None or not report.file_data:
        raise HTTPException(404, "File not found")
    log_activity(db, user, "report_downloaded", report.filename, request=request)
    media = mimetypes.guess_type(report.filename)[0] or "application/octet-stream"
    quoted = quote(report.filename)
    return Response(report.file_data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})


@router.post("/{report_id}/reprocess", response_model=ReportOut)
def reprocess(report_id: int, request: Request, user: User = Depends(require_role("junior")),
              db: Session = Depends(get_db)):
    """Run extraction again. For an approved report the saved records are left untouched:
    the new result is kept as a draft (``draft.reextracted``) until someone applies it
    with ``/update-records``."""
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.status != "confirmed":
        process_report(db, report)
        log_activity(db, user, "report_reprocessed", report.filename, request=request)
        return _out(db, report)

    previous_draft, previous_type, previous_title = report.draft, report.report_type, report.title
    process_report(db, report)
    if report.status == "failed":
        error = report.error
        report.status, report.draft, report.error = "confirmed", previous_draft, None
        report.report_type, report.title = previous_type, previous_title
        db.commit()
        raise HTTPException(422, f"Re-extraction failed: {error}")
    report.status = "confirmed"
    report.report_type, report.title = previous_type, previous_title or report.title
    report.draft = {**(report.draft or {}), "reextracted": True,
                    "reextracted_at": datetime.now(timezone.utc).isoformat(),
                    "reextracted_by": user.email}
    db.commit()
    log_activity(db, user, "report_reextracted", report.filename,
                 f"method={report.extraction_method}; saved records unchanged until applied", request)
    return _out(db, report)


@router.post("/{report_id}/discard-reextraction", response_model=ReportOut)
def discard_reextraction(report_id: int, request: Request, user: User = Depends(require_role("junior")),
                         db: Session = Depends(get_db)):
    """Drop a pending re-extraction and keep the saved records as they are."""
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    saved = list(db.scalars(select(Record).where(Record.report_id == report.id).order_by(Record.id)))
    report.draft = {"report_type": report.report_type, "title": report.title,
                    "summary": (report.draft or {}).get("summary"),
                    "records": [RecordOut.model_validate(r).model_dump(mode="json") for r in saved]}
    db.commit()
    log_activity(db, user, "report_reextraction_discarded", report.filename, request=request)
    return _out(db, report)


@router.post("/{report_id}/update-records", response_model=ReportOut)
def update_records(report_id: int, body: ConfirmIn, request: Request, user: User = Depends(require_role("junior")),
                   db: Session = Depends(get_db)):
    """Replace an approved report's saved records with reviewed values (e.g. after re-extraction).
    Existing records are updated in place so their reference numbers stay the same."""
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.status != "confirmed":
        raise HTTPException(409, "Only approved reports can be updated; use confirm for new reports")
    if not body.records:
        raise HTTPException(400, "Provide at least one record")
    existing = list(db.scalars(select(Record).where(Record.report_id == report.id).order_by(Record.id)))
    updated = created = 0
    for i, payload in enumerate(body.records):
        fresh = record_from_payload(payload, user)
        if i < len(existing):
            rec = existing[i]
            for column in Record.__table__.columns.keys():
                if column in ("id", "report_id", "created_by_id", "created_at", "updated_at"):
                    continue
                setattr(rec, column, getattr(fresh, column))
            updated += 1
        else:
            fresh.report_id = report.id
            db.add(fresh)
            created += 1
    removed = 0
    for rec in existing[len(body.records):]:
        db.delete(rec)
        removed += 1
    report.draft = {**{k: v for k, v in (report.draft or {}).items()
                       if k not in ("reextracted", "reextracted_at", "reextracted_by", "warning")},
                    "records": [r.model_dump(mode="json") for r in body.records]}
    report.report_type = body.records[0].category
    db.commit()
    log_activity(db, user, "report_records_updated", report.filename,
                 f"{updated} updated, {created} added, {removed} removed", request)
    return _out(db, report)


@router.post("/{report_id}/confirm", response_model=ReportOut)
def confirm(report_id: int, body: ConfirmIn, request: Request, user: User = Depends(require_role("junior")),
            db: Session = Depends(get_db)):
    """Save the reviewed (and possibly edited) records to the database."""
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.status == "confirmed":
        raise HTTPException(409, "Report already confirmed")
    if not body.records:
        raise HTTPException(400, "Provide at least one record to save")
    for payload in body.records:
        rec = record_from_payload(payload, user)
        rec.report_id = report.id
        db.add(rec)
    report.status = "confirmed"
    report.confirmed_by_id = user.id
    report.confirmed_at = datetime.now(timezone.utc)
    report.draft = {**(report.draft or {}), "records": [r.model_dump(mode="json") for r in body.records]}
    if body.records:
        report.report_type = body.records[0].category
    db.commit()
    log_activity(db, user, "report_confirmed", report.filename, f"{len(body.records)} record(s)", request)
    return _out(db, report)


@router.post("/{report_id}/reject", response_model=ReportOut)
def reject(report_id: int, request: Request, user: User = Depends(require_role("junior")),
           db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.status == "confirmed":
        raise HTTPException(409, "Report already confirmed; delete its records instead")
    report.status = "rejected"
    db.commit()
    log_activity(db, user, "report_rejected", report.filename, request=request)
    return _out(db, report)


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: int, request: Request, user: User = Depends(require_role("senior")),
                  db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    for rec in list(report.records):
        db.delete(rec)
    db.delete(report)
    db.commit()
    log_activity(db, user, "report_deleted", report.filename, request=request)
