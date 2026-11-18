"""CSV and Word exports: comparable/cost evidence tables and market commentary."""
from __future__ import annotations

import csv
import io
from datetime import date

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from ..models import Record

COLUMNS = {
    "valuation": [
        ("Date", "record_date"), ("Address", "address"), ("Location", "location"), ("Type", "property_type"),
        ("Size (m²)", "size_sqm"), ("Basis", "valuation_basis"), ("Capital value", "capital_value"),
        ("Value/m²", "value_per_sqm"), ("Annual rent", "annual_rent"), ("Yield %", "yield_pct"),
        ("Method", "method"), ("Currency", "currency"),
    ],
    "cost": [
        ("Date", "record_date"), ("Project", "title"), ("Location", "location"), ("Type", "property_type"),
        ("GFA (m²)", "size_sqm"), ("Specification", "specification"), ("Total cost", "total_cost"),
        ("Cost/m²", "cost_per_sqm"), ("Currency", "currency"),
    ],
    "housing": [
        ("Date", "record_date"), ("Area", "location"), ("City", "city"), ("Type", "property_type"),
        ("Avg price", "avg_price"), ("Avg monthly rent", "avg_monthly_rent"), ("Supply (units)", "supply_units"),
        ("Demand", "demand_level"), ("Affordability", "affordability_ratio"), ("Currency", "currency"),
    ],
}

TITLES = {
    "valuation": "Schedule of Comparable Evidence",
    "cost": "Schedule of Construction Cost Evidence",
    "housing": "Schedule of Housing Market Evidence",
}


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}" if abs(value) < 100 else f"{value:,.0f}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def evidence_csv(records: list[Record], category: str) -> str:
    cols = COLUMNS[category]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Ref"] + [c[0] for c in cols])
    for r in records:
        writer.writerow([r.id] + ["" if getattr(r, f) is None else getattr(r, f) for _, f in cols])
    return buf.getvalue()


def _doc_header(document: docx.Document, title: str, subtitle: str) -> None:
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)
    p = document.add_paragraph()
    run = p.add_run("PINNACLE ESTATE SURVEYORS")
    run.bold = True
    run.font.size = Pt(9)
    document.add_heading(title, level=1)
    sub = document.add_paragraph(subtitle)
    sub.runs[0].italic = True


def evidence_docx(records: list[Record], category: str, scope: str) -> bytes:
    cols = COLUMNS[category]
    document = docx.Document()
    _doc_header(document, TITLES[category], f"Scope: {scope}. Prepared {date.today().isoformat()} from the Pinnacle Intelligence System.")
    table = document.add_table(rows=1, cols=len(cols) + 1)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "Ref"
    for i, (label, _) in enumerate(cols, start=1):
        hdr[i].text = label
    for r in records:
        row = table.add_row().cells
        row[0].text = str(r.id)
        for i, (_, field) in enumerate(cols, start=1):
            row[i].text = _cell(getattr(r, field))
    for row in table.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(8)
    document.add_paragraph()
    note = document.add_paragraph(
        "Source: Pinnacle Estate Surveyors internal database. Evidence should be verified before reliance."
    )
    note.runs[0].font.size = Pt(8)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def commentary_docx(text: str, scope: str) -> bytes:
    document = docx.Document()
    _doc_header(document, "Market Commentary", f"Scope: {scope}. Draft prepared {date.today().isoformat()}; review before issue.")
    for para in text.split("\n\n"):
        p = document.add_paragraph(para.strip())
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()
