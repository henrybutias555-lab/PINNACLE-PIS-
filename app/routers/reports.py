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
from ..schemas import ConfirmIn, ReportOut
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
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.status == "confirmed":
        raise HTTPException(409, "Report already confirmed")
    process_report(db, report)
    log_activity(db, user, "report_reprocessed", report.filename, request=request)
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
