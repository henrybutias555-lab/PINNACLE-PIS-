"""Run extraction for an uploaded report: AI (Gemini or Claude) when configured, rules otherwise."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Report
from . import gaps, llm, places, rules
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


def _check_forced_sale(rec: dict) -> None:
    """A forced sale value is always below the market value; one equal to or above it is a mis-read."""
    fsv, mv = rec.get("forced_sale_value"), rec.get("capital_value")
    try:
        bad = fsv is not None and mv is not None and float(fsv) >= float(mv)
    except (TypeError, ValueError):
        bad = False
    if bad:
        rec["forced_sale_value"] = None
        rec.setdefault("warnings", []).append("The forced sale value read was not below the market value, so it "
                                              "was left blank: check the report.")


def _grades_only(rec: dict) -> None:
    """Rating boxes hold a grade (Excellent / Very good, Good, Fair, Poor, Dilapidated), never a sentence."""
    from ..routers.records import RATING_FIELDS
    grades = {g.lower() for _, g in rules._GRADES}
    for field in (*RATING_FIELDS, "state_of_repair"):
        if rec.get(field) and str(rec[field]).lower() not in grades:
            rec.pop(field, None)
            (rec.get("autofilled") or {}).pop(field, None)


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
        rules.clean_gps_fields(rec, report.raw_text or text or "")
        _check_forced_sale(rec)
        if rec.get("basis_of_value"):
            rec["basis_of_value"] = rules.normalise_basis(rec["basis_of_value"])
        if rec.get("purpose"):
            from .reader import standard_purpose
            rec["purpose"] = standard_purpose(rec["purpose"])
        if rec.get("land_size"):
            from .land import land_area_sqm
            rec["land_area_sqm"] = land_area_sqm(rec["land_size"])
        # Construction cost per m² follows the rates adopted for each part of the building.
        from_text = rules.cost_items_from_text(report.raw_text or text or "")
        items = rules.tidy_cost_items(from_text or rec.get("cost_items") or [])
        if items:
            rec["cost_items"] = items
            rec["external_works_rate"] = rules.external_works_rate(items)
            rate = rules.adopted_rate(items)
            if rate and rec.get("cost_per_sqm") != rate:
                parts = "; ".join(f"{(r.get('item') or 'item')} {r['rate']:,.0f}" for r in items[:6])
                rec.setdefault("autofilled", {})["cost_per_sqm"] = f"from the rates adopted ({parts})"
                rec["cost_per_sqm"] = rate
        if not rec.get("cost_per_sqm"):
            rate = rules.construction_rate(report.raw_text or text or "")
            if rate:
                rec["cost_per_sqm"] = rate
                rec.setdefault("autofilled", {})["cost_per_sqm"] = "the rate adopted in the report"
        # The method as the report words it (not the reading's own description).
        from .reader import methods_as_written
        written = methods_as_written(report.raw_text or text or "")
        if written and (rec.get("category") or "valuation") == "valuation" and rec.get("method") != written:
            if rec.get("method"):
                rec.setdefault("autofilled", {})["method"] = "as written in the report"
            rec["method"] = written
        from ..routers.records import RATING_FIELDS, normalise_rating
        for field in RATING_FIELDS:
            if rec.get(field):
                rec[field] = normalise_rating(rec[field])
        if rec.get("state_of_repair"):
            rec["state_of_repair"] = normalise_rating(rec["state_of_repair"], "repair")
        grades = {g.lower() for _, g in rules._GRADES}
        for field in (*RATING_FIELDS, "state_of_repair"):
            if rec.get(field) and str(rec[field]).lower() not in grades:
                rec.pop(field, None)       # e.g. a whole sentence about demand: not a grade
        if rec.get("user_conformity"):
            rec["user_conformity"] = normalise_rating(rec["user_conformity"], "conformity")
        # The report's own grade wins (e.g. the reading said Good, the report says Very good).
        for field, grade in rules.ratings_from_text(report.raw_text or text or "").items():
            if not rec.get(field):
                rec.setdefault("autofilled", {})[field] = "the grade written in the report"
                rec[field] = grade
            elif rec[field] != grade and str(rec[field]).lower() in {g.lower() for _, g in rules._GRADES}:
                rec.setdefault("autofilled", {})[field] = f"corrected to the report's grade ({grade}; the reading said {rec[field]})"
                rec[field] = grade
        if report.extraction_method == "ai":
            # Check the AI's place fields and fill blanks from the report text with the built-in rules.
            place = places.resolve(rec.get("location"), rec.get("address"), rec.get("city"), rec.get("region"),
                                   report.raw_text or "", rec.get("district"))
            for key, value in place.items():
                if key == "district" or not rec.get(key):
                    if value:
                        rec[key] = value
                    elif key == "district":
                        rec.pop("district", None)
    # Fill remaining blanks from what the report already says (MMDA from location, region from
    # MMDA, calculations ...); each filled field is listed with its reason for the reviewer.
    for rec in draft.get("records", []):
        try:
            filled = gaps.infer(rec, report.raw_text or "")
        except Exception:
            log.exception("gap filling failed for report %s", report.id)
            filled = {}
        for field, (value, reason) in filled.items():
            rec[field] = value
        if filled:
            rec["autofilled"] = {**(rec.get("autofilled") or {}), **{field: reason for field, (_, reason) in filled.items()}}
        _grades_only(rec)
    report.draft = draft
    report.report_type = draft.get("report_type")
    report.title = (draft.get("title") or report.filename)[:500]
    report.status = "extracted"
    report.error = None
    db.commit()
    return report
