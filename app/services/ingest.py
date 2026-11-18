"""Run extraction for an uploaded report: Claude when configured, rules otherwise."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Report
from . import llm, rules
from .text_extract import extract_text

log = logging.getLogger(__name__)


def process_report(db: Session, report: Report) -> Report:
    # Work on a temporary copy: the original lives in the database.
    suffix = Path(report.filename).suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"report{suffix}"
        path.write_bytes(report.file_data or b"")
        return _process(db, report, path)


def _process(db: Session, report: Report, path: Path) -> Report:
    try:
        text = extract_text(path, report.file_type)
    except Exception as exc:  # unreadable file
        log.exception("Text extraction failed")
        report.status = "failed"
        report.error = f"Could not read file: {exc}"
        db.commit()
        return report
    report.raw_text = text

    draft = None
    warning = None
    if settings.ai_enabled:
        try:
            draft = llm.extract_report(path, report.file_type, text, settings.default_currency)
            report.extraction_method = "ai"
        except llm.AIError as exc:
            warning = f"AI extraction unavailable ({exc}); used rule-based extraction instead."
            log.warning("AI extraction failed for report %s: %s", report.id, exc)

    if draft is None:
        if not text.strip():
            report.status = "failed"
            report.error = (warning + " " if warning else "") + (
                "No readable text found. For scanned documents configure the Claude API key "
                "(or install Tesseract OCR), or enter the data manually."
            )
            report.draft = {"report_type": None, "title": None, "summary": None, "records": []}
            db.commit()
            return report
        draft = rules.extract(text, settings.default_currency)
        report.extraction_method = "rules"

    if warning:
        draft["warning"] = warning
    for rec in draft.get("records", []):
        rec.setdefault("currency", settings.default_currency)
        rec.setdefault("category", draft.get("report_type") or "valuation")
    report.draft = draft
    report.report_type = draft.get("report_type")
    report.title = (draft.get("title") or report.filename)[:500]
    report.status = "extracted"
    report.error = None
    db.commit()
    return report
