"""Files attached to a record: photographs, site plan, title documents, allocation note,
valuation certificate and supporting documents."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, log_activity, require_role
from ..database import get_db
from ..models import ATTACHMENT_KINDS, Attachment, Record, User

router = APIRouter(prefix="/api", tags=["attachments"])

MAX_FILE_MB = 10
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt",
           ".tif", ".tiff"}
INLINE = {"image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf", "text/plain"}


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    record_id: int
    kind: str
    filename: str
    content_type: str
    size: int


@router.get("/records/{record_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(record_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.get(Record, record_id) is None:
        raise HTTPException(404, "Record not found")
    return list(db.scalars(select(Attachment).where(Attachment.record_id == record_id).order_by(Attachment.id)))


@router.post("/records/{record_id}/attachments", response_model=list[AttachmentOut], status_code=201)
async def add_attachments(record_id: int, request: Request, kind: str = Form(...), files: list[UploadFile] = File(...),
                          user: User = Depends(require_role("junior")), db: Session = Depends(get_db)):
    if db.get(Record, record_id) is None:
        raise HTTPException(404, "Record not found")
    if kind not in ATTACHMENT_KINDS:
        raise HTTPException(422, f"kind must be one of: {', '.join(ATTACHMENT_KINDS)}")
    saved = []
    for f in files:
        name = Path(f.filename or "file").name
        if Path(name).suffix.lower() not in ALLOWED:
            raise HTTPException(415, f"{name}: use photos (JPG/PNG/HEIC), PDF, Word or Excel files")
        data = await f.read()
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"{name} is over {MAX_FILE_MB} MB; please compress it")
        ctype = f.content_type if f.content_type and f.content_type != "application/octet-stream" else (
            mimetypes.guess_type(name)[0] or "application/octet-stream")
        att = Attachment(record_id=record_id, kind=kind, filename=name, content_type=ctype, size=len(data),
                         data=data, uploaded_by_id=user.id)
        db.add(att)
        saved.append(att)
    db.commit()
    log_activity(db, user, "attachment_added", f"record:{record_id}",
                 f"{kind}: {', '.join(a.filename for a in saved)}", request)
    return saved


@router.get("/attachments/{attachment_id}")
def get_attachment(attachment_id: int, download: bool = False, _: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "Attachment not found")
    disposition = "inline" if (att.content_type in INLINE and not download) else "attachment"
    return Response(att.data, media_type=att.content_type,
                    headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(att.filename)}",
                             "Cache-Control": "private, max-age=3600"})


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(attachment_id: int, request: Request, user: User = Depends(require_role("senior")),
                      db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "Attachment not found")
    detail = f"{att.kind}: {att.filename}"
    record_id = att.record_id
    db.delete(att)
    db.commit()
    log_activity(db, user, "attachment_deleted", f"record:{record_id}", detail, request)
