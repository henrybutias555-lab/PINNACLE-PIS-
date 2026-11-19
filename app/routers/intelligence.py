"""Market & cost intelligence, comparable evidence and report-support exports."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..auth import get_current_user, log_activity
from ..config import settings
from ..database import get_db
from ..models import User
from ..schemas import AnalysisExportIn, CommentaryIn, RecordOut
from ..services import analytics, export, llm
from ..services.query import RecordFilters, filtered_records, filters_dep

router = APIRouter(prefix="/api", tags=["intelligence"])


def _records(db: Session, f: RecordFilters):
    return filtered_records(db, f)


@router.get("/analytics/summary")
def summary(f: RecordFilters = Depends(filters_dep), _: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    data = analytics.summary(_records(db, f))
    data["scope"] = f.describe()
    return data


@router.get("/analytics/basis")
def basis_analysis(
    f: RecordFilters = Depends(filters_dep),
    basis: str = Query("Market Value", description="Basis of value to analyse"),
    compare: str | None = Query(None, description="Optional second basis, e.g. Market Rent"),
    interval: str = Query("quarter", pattern="^(month|quarter|year)$"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f.basis_of_value = None   # the basis is chosen here, not by the filter
    data = analytics.basis_analysis(_records(db, f), basis, compare, interval)
    data["scope"] = f.describe()
    return data


@router.get("/analytics/land")
def land_trends(
    f: RecordFilters = Depends(filters_dep),
    districts: str = Query("", description="Comma list of MMDAs; empty = all MMDAs together"),
    metric: str = Query("per_acre", pattern="^(per_acre|per_sqm|total)$"),
    interval: str = Query("quarter", pattern="^(month|quarter|year)$"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Average land values over time by MMDA (land valuations, and land values stated in other reports)."""
    from ..services.land import land_trend

    wanted = [d.strip() for d in districts.split(",") if d.strip()][:4]
    f.district = None          # the MMDAs are chosen here
    data = land_trend(_records(db, f), wanted, metric, interval)
    data["scope"] = f.describe()
    return data


@router.get("/analytics/breakdown")
def breakdown(
    f: RecordFilters = Depends(filters_dep),
    group_by: str = Query("location,property_type", description="Comma list of: location, city, property_type, category, purpose"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    fields = [g.strip() for g in group_by.split(",") if g.strip()]
    allowed = {"location", "district", "city", "property_type", "category", "purpose", "region", "basis_of_value"}
    if not fields or set(fields) - allowed:
        raise HTTPException(422, f"group_by must use: {', '.join(sorted(allowed))}")
    records = _records(db, f)
    return {"currency": analytics.dominant_currency(records), "rows": analytics.breakdown(records, fields)}


@router.get("/analytics/trends")
def trends(
    f: RecordFilters = Depends(filters_dep),
    metric: str = "capital_value",
    interval: str = Query("quarter", pattern="^(month|quarter|year)$"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if metric not in analytics.METRICS:
        raise HTTPException(422, f"metric must be one of: {', '.join(analytics.METRICS)}")
    records = _records(db, f)
    return {"metric": metric, "label": analytics.METRICS[metric][1], "interval": interval,
            "currency": analytics.dominant_currency(records), "series": analytics.trend(records, metric, interval)}


@router.get("/analytics/trends-by")
def trends_by(
    f: RecordFilters = Depends(filters_dep),
    metric: str = "capital_value",
    group: str = Query("property_type", pattern="^(property_type|district|location|city|basis_of_value|purpose)$"),
    interval: str = Query("quarter", pattern="^(month|quarter|year)$"),
    top: int = Query(4, ge=1, le=8),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A trend line per group, e.g. average valuation opinion by property type."""
    if metric not in analytics.METRICS:
        raise HTTPException(422, f"metric must be one of: {', '.join(analytics.METRICS)}")
    records = _records(db, f)
    bad = {r.id for r in analytics.suspect_values(records)}
    records = [r for r in records if r.id not in bad]
    return {"metric": metric, "label": analytics.METRICS[metric][1], "group": group, "interval": interval,
            "currency": analytics.dominant_currency(records), "lines": analytics.trend_by(records, metric, interval, group, top)}


@router.get("/analytics/cross")
def cross(
    f: RecordFilters = Depends(filters_dep),
    interval: str = Query("year", pattern="^(month|quarter|year)$"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    f.category = None  # cross-analysis needs both valuation and cost records
    records = _records(db, f)
    return {"currency": analytics.dominant_currency(records), "rows": analytics.cross_analysis(records, interval)}


@router.post("/analytics/commentary")
def commentary(body: CommentaryIn, request: Request, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    f = RecordFilters(category=body.category, location=body.location, district=body.district, city=body.city,
                      property_type=body.property_type, purpose=body.purpose, basis_of_value=body.basis_of_value,
                      date_from=body.date_from, date_to=body.date_to)
    records = _records(db, f)
    stats = analytics.summary(records)
    trend_data = {m: analytics.trend(records, m, "quarter") for m in ("capital_value", "cost_per_sqm", "yield_pct", "avg_price")}
    scope = f.describe()
    method = "template"
    warning = None
    text = None
    if settings.ai_enabled and records:
        try:
            text = llm.market_commentary({"summary": stats, "quarterly_trends": trend_data}, scope, body.tone)
            method = "ai"
        except llm.AIError as exc:
            warning = f"AI commentary unavailable ({exc}); showing template commentary."
    if text is None:
        text = analytics.template_commentary(stats, trend_data, scope)
    log_activity(db, user, "commentary_generated", scope[:255], method, request)
    return {"scope": scope, "method": method, "warning": warning, "commentary": text, "record_count": len(records)}


@router.post("/analytics/commentary/export")
def commentary_export(body: dict, _: User = Depends(get_current_user)):
    text = (body.get("commentary") or "").strip()
    if not text:
        raise HTTPException(400, "commentary is required")
    fmt = body.get("format", "docx")
    scope = body.get("scope") or "selected data"
    if fmt == "txt":
        return Response(text, media_type="text/plain",
                        headers={"Content-Disposition": 'attachment; filename="market-commentary.txt"'})
    return Response(export.commentary_docx(text, scope),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="market-commentary.docx"'})


@router.get("/comparables")
def comparables(
    category: str = Query("valuation", pattern="^(valuation|cost|housing)$"),
    location: str | None = None,
    city: str | None = None,
    property_type: str | None = None,
    size_sqm: float | None = None,
    ref_date: date | None = None,
    date_from: date | None = None,
    limit: int = Query(10, ge=1, le=50),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rank stored evidence by similarity to a subject property."""
    candidates = filtered_records(db, RecordFilters(category=category, city=city, date_from=date_from))
    ranked = analytics.find_comparables(candidates, location=location, property_type=property_type,
                                        size_sqm=size_sqm, ref_date=ref_date, limit=limit)
    return [{"score": r["score"], "reasons": r["reasons"],
             "record": RecordOut.model_validate(r["record"]).model_dump(mode="json")} for r in ranked]


@router.get("/exports/evidence")
def evidence_export(
    request: Request,
    f: RecordFilters = Depends(filters_dep),
    ids: str | None = Query(None, description="Comma-separated record ids; overrides filters"),
    format: str = Query("docx", pattern="^(docx|csv|xlsx)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One-click comparable / cost / housing evidence table.

    Without a category, Excel and CSV export every matching record (all categories)."""
    if not f.category:
        if format == "docx":
            raise HTTPException(422, "Choose a category (valuation, cost or housing) for the Word evidence table")
        records = filtered_records(db, f, limit=5000)
        scope = f.describe()
        log_activity(db, user, "records_exported", scope[:255], f"{len(records)} rows, {format}", request)
        if format == "xlsx":
            return Response(export.records_xlsx(records, scope), media_type=export.XLSX_MEDIA,
                            headers={"Content-Disposition": 'attachment; filename="pis-records.xlsx"'})
        return Response(export.records_csv(records), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="pis-records.csv"'})
    if ids:
        wanted = {int(x) for x in ids.split(",") if x.strip().isdigit()}
        records = [r for r in filtered_records(db, RecordFilters(category=f.category)) if r.id in wanted]
    else:
        records = filtered_records(db, f, limit=500)
    scope = f.describe()
    log_activity(db, user, "evidence_exported", scope[:255], f"{len(records)} rows, {format}", request)
    name = {"valuation": "comparable-evidence", "cost": "cost-evidence", "housing": "housing-evidence"}[f.category]
    if format == "xlsx":
        return Response(export.evidence_xlsx(records, f.category, scope), media_type=export.XLSX_MEDIA,
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    if format == "csv":
        return Response(export.evidence_csv(records, f.category), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    return Response(export.evidence_docx(records, f.category, scope),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}.docx"'})


ANALYSIS_GROUPS = {"location", "district", "city", "property_type", "category", "purpose", "region", "basis_of_value"}
ANALYSIS_TRENDS = ["capital_value", "annual_rent", "yield_pct", "cost_per_sqm", "avg_price",
                   "avg_monthly_rent"]


@router.post("/exports/analysis")
def analysis_export(body: AnalysisExportIn, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """The market & cost analysis for the current filters as an Excel workbook."""
    groups = [g.strip() for g in (body.group_by or "location,property_type").split(",") if g.strip()]
    if not groups or set(groups) - ANALYSIS_GROUPS:
        raise HTTPException(422, f"group_by must use: {', '.join(sorted(ANALYSIS_GROUPS))}")
    f = RecordFilters(category=body.category, location=body.location, district=body.district, city=body.city,
                      property_type=body.property_type, purpose=body.purpose, basis_of_value=body.basis_of_value,
                      date_from=body.date_from, date_to=body.date_to)
    records = _records(db, f)
    trend_data = {}
    for metric in ANALYSIS_TRENDS:
        series = analytics.trend(records, metric, body.interval)
        if series:
            trend_data[metric] = {"label": analytics.METRICS[metric][1], "series": series}
    cross_records = _records(db, RecordFilters(**{**f.__dict__, "category": None}))
    data = export.analysis_xlsx(
        scope=f.describe(), summary=analytics.summary(records),
        breakdown_rows=analytics.breakdown(records, groups), group_by=groups, trends=trend_data,
        cross_rows=analytics.cross_analysis(cross_records, "year"), records=records,
        commentary=(body.commentary or "").strip() or None,
    )
    log_activity(db, user, "analysis_exported", f.describe()[:255], f"{len(records)} records, xlsx", request)
    return Response(data, media_type=export.XLSX_MEDIA,
                    headers={"Content-Disposition": 'attachment; filename="pis-market-analysis.xlsx"'})
