"""Run extraction for an uploaded report: AI (Gemini or Claude) when configured, rules otherwise."""
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


def _derive_rates(rec: dict) -> None:
    """Fill rates the report implies (per m², yield) so the draft matches what will be saved."""
    def num(key):
        try:
            return float(rec[key]) if rec.get(key) not in (None, "") else None
        except (TypeError, ValueError):
            return None

    size, value, rent, cost = num("size_sqm"), num("capital_value"), num("annual_rent"), num("total_cost")
    if size:
        if value and not rec.get("value_per_sqm"):
            rec["value_per_sqm"] = round(value / size, 2)
        if rent and not rec.get("rent_per_sqm"):
            rec["rent_per_sqm"] = round(rent / size, 2)
        if cost and not rec.get("cost_per_sqm"):
            rec["cost_per_sqm"] = round(cost / size, 2)
    if value and rent and not rec.get("yield_pct"):
        rec["yield_pct"] = round(rent / value * 100, 2)


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
                "No readable text found. For scanned documents configure an AI key (GEMINI_API_KEY) "
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
        _derive_rates(rec)
    report.draft = draft
    report.report_type = draft.get("report_type")
    report.title = (draft.get("title") or report.filename)[:500]
    report.status = "extracted"
    report.error = None
    db.commit()
    return report
