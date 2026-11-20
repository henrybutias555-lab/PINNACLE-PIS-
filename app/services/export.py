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
        ("Date", "record_date"), ("GPS address", "gps_address"), ("Address", "address"), ("Location", "location"),
        ("District (MMDA)", "district"), ("Type", "property_type"), ("Size (m²)", "size_sqm"),
        ("Purpose of valuation", "purpose"), ("Basis of value", "basis_of_value"), ("Tenure", "tenure"), ("Capital value", "capital_value"),
        ("Value/m²", "value_per_sqm"), ("Forced sale value", "forced_sale_value"), ("Annual rent", "annual_rent"),
        ("Yield %", "yield_pct"),
        ("Method", "method"), ("Currency", "currency"),
    ],
    "cost": [
        ("Date", "record_date"), ("Project", "title"), ("Location", "location"), ("District (MMDA)", "district"),
        ("Type", "property_type"),
        ("GFA (m²)", "size_sqm"), ("Specification", "specification"), ("Total cost", "total_cost"),
        ("Cost/m²", "cost_per_sqm"), ("Currency", "currency"),
    ],
    "housing": [
        ("Date", "record_date"), ("Area", "location"), ("District (MMDA)", "district"), ("City", "city"),
        ("Type", "property_type"),
        ("Avg price", "avg_price"), ("Avg monthly rent", "avg_monthly_rent"), ("Supply (units)", "supply_units"),
        ("Demand", "demand_level"), ("Affordability", "affordability_ratio"), ("Currency", "currency"),
    ],
}

TITLES = {
    "valuation": "Schedule of Comparable Evidence",
    "cost": "Schedule of Construction Cost Evidence",
    "housing": "Schedule of Housing Market Evidence",
}


BLANK = "—"   # shown in exports where the report did not state a value


def _cell(value) -> str:
    if value is None or value == "":
        return BLANK
    if isinstance(value, float):
        return f"{value:,.2f}" if abs(value) < 100 else f"{value:,.0f}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def evidence_csv(records: list[Record], category: str) -> str:
    cols = COLUMNS[category]
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel reads ² and ₵ correctly
    writer = csv.writer(buf)
    writer.writerow(["Ref"] + [c[0] for c in cols])
    for r in records:
        writer.writerow([r.id] + [BLANK if getattr(r, f) in (None, "") else getattr(r, f) for _, f in cols])
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


# ---------------------------------------------------------------- Excel (.xlsx)

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

RECORD_COLUMNS = [
    ("Ref", "id", None), ("Category", "category", None), ("Date", "record_date", "yyyy-mm-dd"),
    ("Title / project", "title", None), ("GPS address", "gps_address", None), ("Address", "address", None),
    ("Location", "location", None),
    ("District (MMDA)", "district", None), ("City", "city", None), ("Region", "region", None), ("Property type", "property_type", None),
    ("Purpose", "purpose", None), ("Client", "client", None), ("Currency", "currency", None),
    ("Size / GFA (m²)", "size_sqm", "#,##0.00"), ("Property valued", "property_valued", None),
    ("Basis of value", "basis_of_value", None), ("Assumptions / special assumptions", "assumptions", None),
    ("Valuer", "valuer", None), ("Capital / rental", "valuation_basis", None), ("Method", "method", None),
    ("Capital value (opinion of value)", "capital_value", "#,##0"), ("Value per m²", "value_per_sqm", "#,##0"),
    ("Annual rent", "annual_rent", "#,##0"), ("Rent per m² p.a.", "rent_per_sqm", "#,##0.00"),
    ("Yield %", "yield_pct", "0.00"), ("Total construction cost", "total_cost", "#,##0"),
    ("Cost per m²", "cost_per_sqm", "#,##0"), ("External works rate per m²", "external_works_rate", "#,##0"),
    ("Land value", "land_value", "#,##0"), ("Land area (m²)", "land_area_sqm", "#,##0.00"),
    ("Specification", "specification", None),
    ("Avg house price", "avg_price", "#,##0"), ("Avg monthly rent", "avg_monthly_rent", "#,##0"),
    ("Supply (units)", "supply_units", "#,##0"), ("Demand", "demand_level", None),
    ("Affordability ratio", "affordability_ratio", "0.0"),
    ('Property name / number', "property_name", None),
    ('Owner', "owner", None),
    ('Use / occupancy', "use_occupancy", None),
    ('Highest & best use', "highest_best_use", None),
    ('GPS coordinates', "gps_coordinates", None),
    ('Land size', "land_size", None),
    ('Accessibility', "accessibility", None),
    ('Building type', "building_type", None),
    ('Accommodation', "accommodation", None),
    ('Construction details', "construction_details", None),
    ('Services', "services", None),
    ('Floor areas', "floor_areas", None),
    ('Ancillary structures', "ancillary_structures", None),
    ('Ownership', "ownership", None),
    ('Tenure', "tenure", None),
    ('Source of title', "source_of_title", None),
    ('Unexpired term', "unexpired_term", None),
    ('Planning status', "planning_status", None),
    ('Date of inspection', "inspection_date", "yyyy-mm-dd"),
    ('Forced sale value', "forced_sale_value", "#,##0"),
    ('Location rating', "location_rating", None),
    ('Accessibility rating', "accessibility_rating", None),
    ('State of repair', "state_of_repair", None),
    ('User conformity', "user_conformity", None),
    ('Neighbourhood rating', "neighbourhood_rating", None),
    ('Overall marketability', "overall_marketability", None),
    ('Encumbrances', "encumbrances", None),
    ('Legal disclaimer', "legal_disclaimer", None),
    ('Inspection limitation', "inspection_limitation", None),
    ('Purpose restriction', "purpose_restriction", None),
    ("Tags", "tags", None), ("Notes", "notes", None),
]

_MONEY_KEYS = {"forced_sale_value", "capital_value", "value_per_sqm", "annual_rent", "rent_per_sqm", "cost_per_sqm", "total_cost",
               "avg_price", "avg_monthly_rent"}


def _xl_value(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    return value


class _Book:
    """Small helper around openpyxl for consistently styled sheets."""

    def __init__(self):
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        self.wb = Workbook()
        self.wb.remove(self.wb.active)
        self.head_font = Font(bold=True, color="FFFFFF")
        self.head_fill = PatternFill("solid", fgColor="0A2A66")       # Pinnacle navy
        self.title_font = Font(bold=True, size=14, color="0A2A66")
        self.wrap = Alignment(wrap_text=True, vertical="top")

    def sheet(self, name: str, title: str | None = None, subtitle: str | None = None):
        ws = self.wb.create_sheet(name[:31])
        if title:
            ws.append([title])
            ws["A1"].font = self.title_font
            if subtitle:
                ws.append([subtitle])
            ws.append([])
        return ws

    def table(self, ws, headers: list[str], rows: list[list], formats: list[str | None] | None = None,
              widths: list[int] | None = None, blank: str | None = None):
        """blank: text written into empty cells (record tables use "—" = not stated)."""
        from openpyxl.utils import get_column_letter

        ws.append(headers)
        start = ws.max_row
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=start, column=c)
            cell.font, cell.fill, cell.alignment = self.head_font, self.head_fill, self.wrap
        for row in rows:
            ws.append([blank if blank is not None and v in (None, "", [], {}) else _xl_value(v) for v in row])
        if formats:
            for c, fmt in enumerate(formats, start=1):
                if fmt:
                    for r in range(start + 1, start + 1 + len(rows)):
                        ws.cell(row=r, column=c).number_format = fmt
        for c, h in enumerate(headers, start=1):
            longest = max([len(str(h))] + [len(str(_xl_value(r[c - 1]) or "")) for r in rows[:200]])
            ws.column_dimensions[get_column_letter(c)].width = (widths[c - 1] if widths else min(max(10, longest + 2), 45))
        if rows:
            ws.auto_filter.ref = f"A{start}:{get_column_letter(len(headers))}{start + len(rows)}"
        ws.freeze_panes = ws.cell(row=start + 1, column=1)
        return start

    def bytes(self) -> bytes:
        buf = io.BytesIO()
        self.wb.save(buf)
        return buf.getvalue()


def _records_sheet(book: _Book, records: list[Record], name: str = "Records"):
    ws = book.sheet(name)
    book.table(ws, [c[0] for c in RECORD_COLUMNS],
               [[getattr(r, f) for _, f, _ in RECORD_COLUMNS] for r in records],
               [fmt for _, _, fmt in RECORD_COLUMNS], blank=BLANK)


def evidence_xlsx(records: list[Record], category: str, scope: str) -> bytes:
    book = _Book()
    cols = COLUMNS[category]
    ws = book.sheet("Evidence", TITLES[category],
                    f"Pinnacle Estate Surveyors · {scope} · prepared {date.today().isoformat()}")
    fmts = [None] + ["yyyy-mm-dd" if f == "record_date" else "#,##0" if f in _MONEY_KEYS or f in ("total_cost",)
                     else "#,##0.00" if f in ("size_sqm",) else "0.00" if f in ("yield_pct", "affordability_ratio")
                     else None for _, f in cols]
    book.table(ws, ["Ref"] + [c[0] for c in cols], [[r.id] + [getattr(r, f) for _, f in cols] for r in records], fmts,
               blank=BLANK)
    comparables_sheet(book, records)
    cost_items_sheet(book, records)
    _records_sheet(book, records, "All fields")
    return book.bytes()


def analysis_xlsx(*, scope: str, summary: dict, breakdown_rows: list[dict], group_by: list[str],
                  trends: dict[str, dict], cross_rows: list[dict], records: list[Record],
                  commentary: str | None = None) -> bytes:
    """Full market & cost analysis workbook with native Excel charts."""
    from openpyxl.chart import BarChart, LineChart, Reference

    book = _Book()
    cur = summary.get("currency") or ""
    stamp = f"Pinnacle Estate Surveyors · prepared {date.today().isoformat()} from the Pinnacle Intelligence System"

    # Summary
    ws = book.sheet("Summary", f"Market & cost analysis: {scope}", stamp)
    dr = summary.get("date_range")
    info = [["Records analysed", summary["record_count"]],
            ["Date range", f"{dr[0]} to {dr[1]}" if dr else "n/a"],
            ["Currency of figures", cur or "n/a"]]
    for cat, n in summary.get("by_category", {}).items():
        info.append([f"{cat.title()} records", n])
    excluded = summary.get("other_currencies_excluded") or {}
    if excluded:
        info.append(["Excluded (other currency)", ", ".join(f"{n} {c}" for c, n in excluded.items())])
    for row in info:
        ws.append(row)
    ws.append([])
    metric_rows = [[m["label"], m["count"], m["mean"], m["median"], m["min"], m["max"]]
                   for m in summary.get("metrics", {}).values()]
    book.table(ws, ["Metric", "Records", "Average", "Median", "Minimum", "Maximum"], metric_rows,
               [None, "0", "#,##0.00", "#,##0.00", "#,##0.00", "#,##0.00"], [34, 10, 16, 16, 16, 16])
    if commentary:
        ws.append([])
        ws.append(["Market commentary (draft)"])
        ws.cell(row=ws.max_row, column=1).font = book.title_font
        for para in commentary.split("\n\n"):
            ws.append([para.strip()])
            cell = ws.cell(row=ws.max_row, column=1)
            cell.alignment = book.wrap
            ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=6)
            ws.row_dimensions[ws.max_row].height = max(30, 15 * (len(para) // 110 + 1))

    # By area & type
    ws = book.sheet("By area & type", "Averages by " + " and ".join(g.replace("_", " ") for g in group_by),
                    f"Figures in {cur}" if cur else None)
    cols = [("capital_value", "Valuation opinion"), ("annual_rent", "Annual rent"),
            ("yield_pct", "Yield %"), ("cost_per_sqm", "Cost per m²"), ("avg_price", "Avg house price"),
            ("avg_monthly_rent", "Avg monthly rent")]
    book.table(ws, [g.replace("_", " ").title() for g in group_by] + ["Records"] + [l for _, l in cols],
               [[r.get(g) for g in group_by] + [r["count"]] + [r.get(k) for k, _ in cols] for r in breakdown_rows],
               [None] * len(group_by) + ["0"] + ["0.00" if k == "yield_pct" else "#,##0" for k, _ in cols])

    # Trends: one block per metric, each with a line chart
    ws = book.sheet("Trends", "Trends over time (quarterly)", f"Figures in {cur}" if cur else None)
    for key, t in trends.items():
        series = t.get("series") or []
        if not series:
            continue
        ws.append([t["label"]])
        ws.cell(row=ws.max_row, column=1).font = book.title_font
        start = book.table(ws, ["Period", "Average", "Median", "Records", "Change vs previous %"],
                           [[p["period"], p["mean"], p["median"], p["count"], p["change_pct"]] for p in series],
                           [None, "#,##0.00", "#,##0.00", "0", "0.0"], [14, 16, 16, 10, 20])
        if len(series) >= 2:
            chart = LineChart()
            chart.title, chart.height, chart.width = t["label"], 7, 16
            chart.y_axis.title = cur if key not in ("yield_pct", "affordability_ratio") else ""
            data = Reference(ws, min_col=2, max_col=3, min_row=start, max_row=start + len(series))
            chart.add_data(data, titles_from_data=True)
            chart.set_categories(Reference(ws, min_col=1, min_row=start + 1, max_row=start + len(series)))
            ws.add_chart(chart, f"G{start}")
        ws.append([])
        for _ in range(max(0, 15 - len(series))):  # leave room for the chart
            ws.append([])
    ws.freeze_panes = None

    # Cost vs value
    ws = book.sheet("Construction cost", "Construction cost per m²: main building and external works",
                    f"Figures in {cur}" if cur else None)
    start = book.table(ws, ["Period", "Main building per m²", "External works per m²", "Main building records",
                            "External works records"],
                       [[r["period"], r["cost_per_sqm"], r["external_works_rate"], r["cost_count"], r["external_count"]]
                        for r in cross_rows],
                       [None, "#,##0", "#,##0", "0", "0"], [14, 22, 22, 22, 22])
    if len(cross_rows) >= 1:
        chart = BarChart()
        chart.title, chart.height, chart.width = "Construction cost per m²", 8, 16
        chart.add_data(Reference(ws, min_col=2, max_col=3, min_row=start, max_row=start + len(cross_rows)),
                       titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=start + 1, max_row=start + len(cross_rows)))
        ws.add_chart(chart, f"G{start}")

    comparables_sheet(book, records)
    cost_items_sheet(book, records)
    _records_sheet(book, records)
    return book.bytes()


def records_csv(records: list[Record]) -> str:
    """Every field of every record, one row each (Excel opens this directly)."""
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel reads ², ₵ and accents correctly
    writer = csv.writer(buf)
    writer.writerow([c[0] for c in RECORD_COLUMNS])
    for r in records:
        writer.writerow([BLANK if getattr(r, f) in (None, "", [], {}) else _xl_value(getattr(r, f)) for _, f, _ in RECORD_COLUMNS])
    return buf.getvalue()


def records_xlsx(records: list[Record], scope: str) -> bytes:
    """All matching records: one sheet with every field, plus an evidence sheet per category."""
    book = _Book()
    ws = book.sheet("All records", "Pinnacle Intelligence System: records",
                    f"Pinnacle Estate Surveyors · {scope} · {len(records)} records · prepared {date.today().isoformat()}")
    book.table(ws, [c[0] for c in RECORD_COLUMNS], [[getattr(r, f) for _, f, _ in RECORD_COLUMNS] for r in records],
               [fmt for _, _, fmt in RECORD_COLUMNS], blank=BLANK)
    for category, name in (("valuation", "Valuation evidence"), ("cost", "Cost evidence"), ("housing", "Housing data")):
        subset = [r for r in records if r.category == category]
        if not subset:
            continue
        cols = COLUMNS[category]
        ws = book.sheet(name, TITLES[category])
        fmts = [None] + ["yyyy-mm-dd" if f == "record_date" else "#,##0" if f in _MONEY_KEYS
                         else "#,##0.00" if f == "size_sqm" else "0.00" if f in ("yield_pct", "affordability_ratio")
                         else None for _, f in cols]
        book.table(ws, ["Ref"] + [c[0] for c in cols], [[r.id] + [getattr(r, f) for _, f in cols] for r in subset], fmts,
                   blank=BLANK)
    comparables_sheet(book, records)
    cost_items_sheet(book, records)
    return book.bytes()


COMPARABLE_COLUMNS = [
    ("Ref", None, None), ("Comparable no.", None, "0"), ("Subject location", "location", None),
    ("Subject MMDA", "district", None), ("Purpose of valuation", "purpose", None),
    ("Basis of value", "basis_of_value", None), ("Date of valuation", "record_date", "yyyy-mm-dd"),
    ("Comparable address / location", "c:address", None), ("Comparable type", "c:property_type", None),
    ("Comparable date", "c:date", None), ("Price", "c:price", "#,##0"), ("Rent", "c:rent", "#,##0"),
    ("Size (m²)", "c:size_sqm", "#,##0.00"), ("Price per m²", "c:price_per_sqm", "#,##0"), ("Remarks", "c:notes", None),
    ("Currency", "currency", None),
]


def cost_items_sheet(book, records: list[Record]) -> None:
    """Every rate adopted (cost approach / DRC), one row per part of the building."""
    rows = []
    for r in records:
        for i, c in enumerate(r.cost_items or [], start=1):
            c = c or {}
            rows.append([r.id, i, r.location or r.address, r.property_type, c.get("item"), c.get("part"), c.get("area_sqm"),
                         c.get("rate"), c.get("amount"), r.cost_per_sqm, r.external_works_rate, r.record_date, r.currency])
    if not rows:
        return
    ws = book.sheet("Rates adopted", "Rates adopted (cost approach / DRC)",
                    "Each line with its area, rate and amount; 'Construction cost / m²' is the floor-area-weighted "
                    "rate of the main building (its floors); external works are listed but not averaged in")
    book.table(ws, ["Ref", "Line", "Location", "Property type", "Item / floor", "Part", "Area (m²)", "Rate per m²", "Amount",
                    "Main building cost / m²", "External works cost / m²", "Date of valuation", "Currency"], rows,
               [None, "0", None, None, None, None, "#,##0.00", "#,##0", "#,##0", "#,##0", "#,##0", "yyyy-mm-dd", None], blank=BLANK)


def comparables_sheet(book, records: list[Record]) -> None:
    """Every comparable listed inside the valuation reports, with the subject's purpose and basis."""
    rows = []
    for r in records:
        for i, c in enumerate(r.comparables or [], start=1):
            row = []
            for label, field, _ in COMPARABLE_COLUMNS:
                if label == "Ref":
                    row.append(r.id)
                elif label == "Comparable no.":
                    row.append(i)
                elif field.startswith("c:"):
                    row.append((c or {}).get(field[2:]))
                else:
                    row.append(getattr(r, field))
            rows.append(row)
    if not rows:
        return
    ws = book.sheet("Comparables", "Comparables from valuation reports",
                    "Each comparable with the purpose and basis of the valuation it supports")
    book.table(ws, [c[0] for c in COMPARABLE_COLUMNS], rows, [c[2] for c in COMPARABLE_COLUMNS], blank=BLANK)
