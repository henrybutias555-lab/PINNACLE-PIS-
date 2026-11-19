import io

import docx

VALUATION_TXT = """VALUATION REPORT
Property Address: House No. 12, Lagos Avenue, East Legon
Location: East Legon
City: Accra
Client: Example Bank Ltd
Purpose: Mortgage
Date of Valuation: 15 March 2025

The subject is a 4-bedroom detached residential house with a gross floor area of 320 sq m.
We have adopted the comparison method.
Open Market Value: GHS 3,200,000
Annual rent: GHS 192,000
"""

COST_TXT = """CONSTRUCTION COST ESTIMATE
Project: Office block
Location: Airport City
City: Accra
Date: 2024-06-30
Gross floor area: 1,500 sq m
Preliminaries: GHS 900,000
Substructure: GHS 1,800,000
Finishes: GHS 2,100,000
Total construction cost: GHS 12,000,000
This commercial office development is priced from the bill of quantities.
"""


def test_health(client):
    r = client.get("/api/health")
    assert r.json()["status"] == "ok"
    assert r.json()["ai_enabled"] is False


def test_auth_required(client):
    assert client.get("/api/records").status_code == 401
    assert client.post("/api/auth/login", json={"email": "admin@test.local", "password": "nope"}).status_code == 401


def test_upload_review_confirm_flow(client, admin):
    files = [("files", ("valuation.txt", VALUATION_TXT.encode(), "text/plain"))]
    r = client.post("/api/reports/upload?sync=true", headers=admin, files=files)
    assert r.status_code == 201, r.text
    report = r.json()[0]
    assert report["status"] == "extracted"
    assert report["extraction_method"] == "rules"
    rec = report["draft"]["records"][0]
    assert rec["category"] == "valuation"
    assert rec["capital_value"] == 3_200_000
    assert rec["currency"] == "GHS"
    assert rec["location"] == "East Legon"
    assert rec["size_sqm"] == 320
    assert rec["record_date"] == "2025-03-15"
    assert rec["yield_pct"] == 6.0

    # Nothing is stored until confirmed
    assert client.get("/api/records", headers=admin).json()["total"] == 0

    rec["tags"] = ["Mortgage", "detached"]
    r = client.post(f"/api/reports/{report['id']}/confirm", headers=admin, json={"records": [rec]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    assert r.json()["record_count"] == 1

    r = client.get("/api/records?location=east legon", headers=admin).json()
    assert r["total"] == 1
    saved = r["items"][0]
    assert saved["value_per_sqm"] == 10000
    assert saved["tags"] == ["detached", "mortgage"]

    # Confirming twice is rejected
    assert client.post(f"/api/reports/{report['id']}/confirm", headers=admin, json={"records": [rec]}).status_code == 409


def test_docx_cost_upload(client, admin):
    d = docx.Document()
    for line in COST_TXT.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    files = [("files", ("cost.docx", buf.getvalue(), "application/octet-stream"))]
    report = client.post("/api/reports/upload?sync=true", headers=admin, files=files).json()[0]
    rec = report["draft"]["records"][0]
    assert rec["category"] == "cost"
    assert rec["total_cost"] == 12_000_000
    assert rec["cost_per_sqm"] == 8000
    assert rec["cost_breakdown"]["preliminaries"] == 900_000
    r = client.post(f"/api/reports/{report['id']}/confirm", headers=admin, json={"records": [rec]})
    assert r.status_code == 200


def test_unsupported_file(client, admin):
    r = client.post("/api/reports/upload", headers=admin, files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 415


def _seed(client, headers):
    rows = [
        {"category": "valuation", "location": "Cantonments", "city": "Accra", "property_type": "residential",
         "record_date": "2023-02-01", "size_sqm": 400, "capital_value": 4_000_000, "annual_rent": 240_000},
        {"category": "valuation", "location": "Cantonments", "city": "Accra", "property_type": "residential",
         "record_date": "2024-02-01", "size_sqm": 380, "capital_value": 4_560_000},
        {"category": "valuation", "location": "Cantonments", "city": "Accra", "property_type": "commercial",
         "record_date": "2024-05-01", "size_sqm": 1000, "capital_value": 15_000_000, "annual_rent": 1_200_000},
        {"category": "cost", "location": "Cantonments", "city": "Accra", "property_type": "residential",
         "record_date": "2023-03-01", "size_sqm": 300, "total_cost": 1_800_000},
        {"category": "cost", "location": "Cantonments", "city": "Accra", "property_type": "residential",
         "record_date": "2024-03-01", "size_sqm": 300, "total_cost": 2_100_000},
        {"category": "housing", "location": "Cantonments", "city": "Accra", "property_type": "residential",
         "record_date": "2024-06-01", "avg_price": 3_500_000, "avg_monthly_rent": 18_000, "supply_units": 120,
         "demand_level": "high", "affordability_ratio": 12.5},
    ]
    for row in rows:
        r = client.post("/api/records", headers=headers, json=row)
        assert r.status_code == 201, r.text


def test_analytics(client, admin):
    _seed(client, admin)
    s = client.get("/api/analytics/summary?location=Cantonments", headers=admin).json()
    assert s["record_count"] == 6
    assert s["metrics"]["cost_per_sqm"]["mean"] == 6500
    assert s["metrics"]["value_per_sqm"]["count"] == 3

    t = client.get("/api/analytics/trends?location=Cantonments&metric=value_per_sqm&interval=year", headers=admin).json()
    assert [p["period"] for p in t["series"]] == ["2023", "2024"]
    assert t["series"][0]["mean"] == 10000

    c = client.get("/api/analytics/cross?location=Cantonments&interval=year", headers=admin).json()
    y2023 = next(r for r in c["rows"] if r["period"] == "2023")
    assert y2023["cost_per_sqm"] == 6000 and y2023["value_per_sqm"] == 10000
    assert y2023["cost_to_value_pct"] == 60.0

    b = client.get("/api/analytics/breakdown?location=Cantonments&group_by=property_type", headers=admin).json()
    assert {r["property_type"] for r in b["rows"]} == {"residential", "commercial"}

    com = client.post("/api/analytics/commentary", headers=admin, json={"location": "Cantonments"}).json()
    assert com["method"] == "template"
    assert "Cantonments" in com["commentary"]

    r = client.post("/api/analytics/commentary/export", headers=admin, json={"commentary": com["commentary"]})
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_comparables_and_exports(client, admin):
    res = client.get("/api/comparables?location=Cantonments&property_type=residential&size_sqm=390", headers=admin).json()
    assert res and res[0]["record"]["location"] == "Cantonments"
    assert res[0]["record"]["property_type"] == "residential"

    r = client.get("/api/exports/evidence?category=valuation&location=Cantonments&format=csv", headers=admin)
    assert r.status_code == 200 and "Cantonments" in r.text
    r = client.get("/api/exports/evidence?category=cost&format=docx", headers=admin)
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_search_filters(client, admin):
    r = client.get("/api/records?category=valuation&min_amount=5000000", headers=admin).json()
    assert all(i["capital_value"] >= 5_000_000 for i in r["items"]) and r["total"] >= 1
    r = client.get("/api/records?tag=mortgage", headers=admin).json()
    assert r["total"] == 1
    r = client.get("/api/records?date_from=2024-01-01&date_to=2024-12-31&category=cost", headers=admin).json()
    assert r["total"] == 2
    f = client.get("/api/records/facets", headers=admin).json()
    assert "Cantonments" in f["locations"]


def test_record_update_recomputes(client, admin):
    rid = client.post("/api/records", headers=admin, json={
        "category": "valuation", "location": "Osu", "size_sqm": 100, "capital_value": 1_000_000}).json()["id"]
    r = client.patch(f"/api/records/{rid}", headers=admin, json={"capital_value": 2_000_000})
    assert r.status_code == 200, r.text
    assert r.json()["value_per_sqm"] == 20000
    assert client.patch(f"/api/records/{rid}", headers=admin, json={"bogus": 1}).status_code == 422


def test_roles(client, admin, make_user):
    viewer = make_user("viewer")
    junior = make_user("junior")
    senior = make_user("senior")
    # Viewer: read-only
    assert client.get("/api/records", headers=viewer).status_code == 200
    assert client.post("/api/records", headers=viewer, json={"category": "cost"}).status_code == 403
    # Junior can add but not delete
    rid = client.post("/api/records", headers=junior, json={"category": "cost", "location": "Tema"}).json()["id"]
    assert client.delete(f"/api/records/{rid}", headers=junior).status_code == 403
    assert client.delete(f"/api/records/{rid}", headers=senior).status_code == 204
    # Only admin manages users / sees activity
    assert client.get("/api/users", headers=senior).status_code == 403
    log = client.get("/api/activity", headers=admin).json()
    actions = {a["action"] for a in log}
    assert {"login", "report_confirmed", "record_deleted"} <= actions


def test_ai_extraction_path(client, admin, monkeypatch):
    """When Claude is configured its draft is used; on failure we fall back to rules."""
    from app.config import Settings
    from app.services import llm

    monkeypatch.setattr(Settings, "ai_enabled", property(lambda self: True))
    monkeypatch.setattr(llm, "extract_report", lambda *a, **k: {
        "report_type": "housing", "title": "Accra housing review", "summary": "Test",
        "records": [{"category": "housing", "location": "Spintex", "avg_price": 900000, "currency": "GHS"}]})
    files = [("files", ("h.txt", b"housing market review", "text/plain"))]
    rep = client.post("/api/reports/upload?sync=true", headers=admin, files=files).json()[0]
    assert rep["extraction_method"] == "ai" and rep["report_type"] == "housing"

    def boom(*a, **k):
        raise llm.AIError("Claude API rate limit reached")
    monkeypatch.setattr(llm, "extract_report", boom)
    rep = client.post("/api/reports/upload?sync=true", headers=admin,
                      files=[("files", ("v.txt", VALUATION_TXT.encode(), "text/plain"))]).json()[0]
    assert rep["extraction_method"] == "rules"
    assert "rate limit" in rep["draft"]["warning"]


def test_extraction_schema_is_strict():
    from app.services.llm import EXTRACTION_SCHEMA

    def walk(node):
        if isinstance(node, dict):
            if "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(EXTRACTION_SCHEMA)


def test_vercel_setup_guard(client, monkeypatch):
    """On Vercel, insecure defaults block the app with a setup page instead of running."""
    from app import config

    monkeypatch.setattr(config, "ON_VERCEL", True)
    monkeypatch.setattr(config.settings, "secret_key", config.DEFAULT_SECRET)
    monkeypatch.setattr(config.settings, "admin_password", config.DEFAULT_ADMIN_PASSWORD)
    monkeypatch.setattr(config.settings, "database_url", "sqlite:////tmp/pis.db")
    r = client.get("/")
    assert r.status_code == 503 and "setup required" in r.text
    assert len(r.text.split("<li>")) == 4
    assert client.get("/api/health").status_code == 503

    monkeypatch.setattr(config.settings, "secret_key", "x" * 40)
    monkeypatch.setattr(config.settings, "admin_password", "A-strong-password-1")
    monkeypatch.setattr(config.settings, "database_url", "postgresql+psycopg://u@h/db")
    assert config.settings.deployment_problems() == []


def test_database_url_normalisation(monkeypatch):
    from app import config

    for var in ("PIS_DATABASE_URL", "POSTGRES_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql://postgres.abc:pw@aws-0-eu.pooler.supabase.com:6543/postgres?pgbouncer=true&sslmode=require")
    assert config._database_url() == ("postgresql+psycopg://postgres.abc:pw@aws-0-eu.pooler.supabase.com:6543"
                                      "/postgres?sslmode=require")


def test_password_with_special_characters(monkeypatch):
    from sqlalchemy.engine import make_url

    from app import config

    for var in ("PIS_DATABASE_URL", "POSTGRES_URL"):
        monkeypatch.delenv(var, raising=False)
    host = "@pooler.example.com:6543/postgres"
    cases = {"Sample@99.": "Sample@99.", "Sample%4099.": "Sample@99.", "p/ss?w#rd@x": "p/ss?w#rd@x"}
    for raw, expected in cases.items():
        monkeypatch.setenv("DATABASE_URL", "postgresql://postgres.abc:" + raw + host)
        url = make_url(config._database_url())
        assert url.host == "pooler.example.com"
        assert url.port == 6543 and url.database == "postgres"
        assert url.password == expected


def test_database_failure_shows_page_not_crash(client, monkeypatch):
    """An unreachable database yields a 503 explanation page, with the password hidden."""
    import app.main as main

    monkeypatch.setattr(main.settings, "database_url", "postgresql+psycopg://u:FakeSecret1@db.invalid/x")
    monkeypatch.setattr(main, "_initialised", False)

    def fail():
        raise RuntimeError("password authentication failed for FakeSecret1")
    monkeypatch.setattr(main, "init_db", fail)
    r = client.get("/")
    assert r.status_code == 503
    assert "Could not connect to the database" in r.text
    assert "FakeSecret1" not in r.text and "****" in r.text


def test_render_guard_and_healthz(client, monkeypatch):
    """On Render the same safety checks apply, but /healthz always answers for health checks."""
    from app import config

    monkeypatch.setattr(config, "PUBLIC_DEPLOY", True)
    monkeypatch.setattr(config.settings, "secret_key", config.DEFAULT_SECRET)
    assert client.get("/").status_code == 503
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "alive"


def test_index_busts_asset_cache(client):
    from app.main import ASSET_VERSION

    r = client.get("/")
    assert r.headers["cache-control"] == "no-cache"
    assert f"/static/app.js?v={ASSET_VERSION}" in r.text
    assert f"/static/styles.css?v={ASSET_VERSION}" in r.text


GHANA_REPORT = """VALUATION REPORT
Property Address: Quartey Papafio Hills
Location: Santeo - East Legon Hills, Accra
Region: Greater Accra Region
Date of Valuation: 1 August 2023
The subject property is a 5-bedroom mansion with boys quarters, built on a plot of land measuring
approximately 0.25 acres (land area 1,011.7 sq m). The land title is registered.
Gross floor area: 206.55 sq m. We have adopted the Cost Approach. Market value: GHS 2,300,000"""


def test_rules_ghana_places_and_property_type():
    from app.services import rules

    r = rules.extract(GHANA_REPORT)["records"][0]
    assert (r["location"], r["city"], r["region"]) == ("Santeo - East Legon Hills", "Accra", "Greater Accra")
    assert r["property_type"] == "residential"
    assert r["tags"] == ["mansion", "5-bedroom"]

    land = rules.extract("Location: Oyibi\nA parcel of vacant land of 0.5 acres. Market value: GHS 400,000")["records"][0]
    assert land["property_type"] == "land" and land["city"] == "Accra"
    tema = rules.extract("Property Address: House No. 12, Community 25, Tema\nThree bedroom detached house. "
                         "Market value: GHS 1,200,000")["records"][0]
    assert (tema["city"], tema["property_type"]) == ("Tema", "residential")
    assert "3-bedroom" in tema["tags"]
    kumasi = rules.extract("Valuation of an office block at Ahodwo. Market value: GHS 9,000,000")["records"][0]
    assert (kumasi["location"], kumasi["city"], kumasi["property_type"]) == ("Ahodwo", "Kumasi", "commercial")


def test_ai_provider_selection(monkeypatch):
    from app.config import settings

    for k in ("PIS_DISABLE_AI", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
              "PIS_AI_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    assert settings.ai_provider is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert settings.ai_provider == "claude"
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    assert settings.ai_provider == "gemini" and settings.ai_model == settings.gemini_model
    monkeypatch.setenv("PIS_AI_PROVIDER", "claude")
    assert settings.ai_provider == "claude"


def test_gemini_extraction_with_mocked_api(monkeypatch, tmp_path):
    """The Gemini request is well-formed and its JSON answer is cleaned into a draft."""
    import json as _json
    from types import SimpleNamespace

    from google.genai import types

    from app.services import gemini

    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            assert isinstance(config, types.GenerateContentConfig)
            captured.update(model=model, contents=contents, config=config)
            answer = {"report_type": "valuation", "title": "Valuation", "summary": "",
                      "records": [{"category": "valuation", "location": "East Legon Hills", "city": "Accra",
                                   "property_type": "residential", "capital_value": 2300000, "yield_pct": 0,
                                   "client": "", "tags": ["mansion", "5-bedroom"], "comparables": []}]}
            return SimpleNamespace(text=_json.dumps(answer),
                                   candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)])

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    f = tmp_path / "report.docx"
    f.write_bytes(b"x")
    draft = gemini.extract_report(f, "docx", GHANA_REPORT, "GHS")
    rec = draft["records"][0]
    assert rec["city"] == "Accra" and rec["capital_value"] == 2300000
    assert "yield_pct" not in rec and "client" not in rec and "comparables" not in rec  # empties dropped
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].response_json_schema["required"] == ["report_type", "records"]
    assert "Santeo" in captured["contents"][0]

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    gemini.extract_report(pdf, "pdf", "", "GHS")
    assert isinstance(captured["contents"][0], types.Part)  # PDFs go to Gemini directly (scans too)


def test_gemini_schema_has_no_nullable_types():
    from app.services.gemini import GEMINI_SCHEMA

    def walk(node):
        if isinstance(node, dict):
            assert not isinstance(node.get("type"), list) and "anyOf" not in node
            assert None not in node.get("enum", [])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(GEMINI_SCHEMA)


def test_analysis_excel_export(client, admin):
    """The analysis downloads as a real Excel workbook with numbers, tables and charts."""
    import io as _io

    import openpyxl

    r = client.post("/api/exports/analysis", headers=admin,
                    json={"location": "Cantonments", "group_by": "property_type", "commentary": "Draft text.\n\nSecond."})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml")
    wb = openpyxl.load_workbook(_io.BytesIO(r.content))
    assert wb.sheetnames == ["Summary", "By area & type", "Trends", "Cost vs value", "Records"]
    summary = [row for row in wb["Summary"].iter_rows(values_only=True)]
    assert ("Records analysed", 6) in [tuple(x[:2]) for x in summary]
    cps = next(x for x in summary if x[0] == "Construction cost per m²")
    assert cps[2] == 6500 and isinstance(cps[2], (int, float))  # a number, not text
    assert any(x[0] == "Draft text." for x in summary)
    types_ = {row[0] for row in wb["By area & type"].iter_rows(min_row=4, values_only=True) if row[0]}
    assert {"residential", "commercial"} <= types_
    assert len(wb["Trends"]._charts) >= 1 and len(wb["Cost vs value"]._charts) == 1
    assert wb["Records"].max_row == 7  # header + 6 records
    assert wb["Records"].auto_filter.ref  # filter arrows on

    assert client.post("/api/exports/analysis", headers=admin, json={"group_by": "bogus"}).status_code == 422


def test_evidence_excel_export(client, admin):
    import io as _io

    import openpyxl

    r = client.get("/api/exports/evidence?category=valuation&location=Cantonments&format=xlsx", headers=admin)
    assert r.status_code == 200
    wb = openpyxl.load_workbook(_io.BytesIO(r.content))
    assert wb.sheetnames == ["Evidence", "All fields"]
    rows = list(wb["Evidence"].iter_rows(min_row=4, values_only=True))
    assert rows[0][:4] == ("Ref", "Date", "GPS address", "Address")
    data = rows[1:]
    assert len(data) == 3, data
    col = rows[0].index("Capital value")
    assert "District (MMDA)" in rows[0]
    assert all(isinstance(x[col], (int, float)) for x in data)  # capital values are numbers, not text


def test_export_all_categories(client, admin):
    """With Category = All, Excel and CSV export every matching record."""
    import io as _io

    import openpyxl

    r = client.get("/api/exports/evidence?format=xlsx&location=Cantonments", headers=admin)
    assert r.status_code == 200
    wb = openpyxl.load_workbook(_io.BytesIO(r.content))
    assert wb.sheetnames == ["All records", "Valuation evidence", "Cost evidence", "Housing data"]
    assert wb["All records"].max_row == 4 + 6  # title, subtitle, blank, header + 6 records
    r = client.get("/api/exports/evidence?format=csv&location=Cantonments", headers=admin)
    assert r.status_code == 200 and r.text.startswith("﻿Ref,Category")
    assert len(r.text.strip().splitlines()) == 7
    assert client.get("/api/exports/evidence?format=docx", headers=admin).status_code == 422


def test_reextract_approved_report_and_update(client, admin, monkeypatch):
    from app.config import Settings
    from app.services import llm

    rep = client.post("/api/reports/upload?sync=true", headers=admin,
                      files=[("files", ("santeo.txt", GHANA_REPORT.encode(), "text/plain"))]).json()[0]
    rec = dict(rep["draft"]["records"][0], property_type="land", city=None)  # what was approved originally
    client.post(f"/api/reports/{rep['id']}/confirm", headers=admin, json={"records": [rec]})
    saved = client.get(f"/api/records?report_id={rep['id']}", headers=admin).json()["items"]
    assert len(saved) == 1 and saved[0]["property_type"] == "land"

    # Re-run with "Gemini": the approved record must not change yet.
    monkeypatch.setattr(Settings, "ai_enabled", property(lambda self: True))
    monkeypatch.setattr(llm, "extract_report", lambda *a, **k: {
        "report_type": "valuation", "title": "Santeo", "records": [dict(
            category="valuation", location="Santeo - East Legon Hills", city="Accra", region="Greater Accra",
            property_type="residential", capital_value=2300000, size_sqm=206.55, tags=["mansion", "5-bedroom"],
            comparables=[{"address": "Plot 4, East Legon Hills", "price": 2100000}])]})
    r = client.post(f"/api/reports/{rep['id']}/reprocess", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed" and body["extraction_method"] == "ai" and body["draft"]["reextracted"]
    assert client.get(f"/api/records/{saved[0]['id']}", headers=admin).json()["property_type"] == "land"

    # Apply: the same record (same id) is updated.
    new = body["draft"]["records"]
    r = client.post(f"/api/reports/{rep['id']}/update-records", headers=admin, json={"records": new})
    assert r.status_code == 200, r.text
    assert "reextracted" not in r.json()["draft"]
    after = client.get(f"/api/records?report_id={rep['id']}", headers=admin).json()["items"]
    assert [a["id"] for a in after] == [saved[0]["id"]]
    assert (after[0]["property_type"], after[0]["city"]) == ("residential", "Accra")
    assert after[0]["comparables"][0]["price"] == 2100000
    acts = {a["action"] for a in client.get("/api/activity", headers=admin).json()}
    assert {"report_reextracted", "report_records_updated"} <= acts

    # Re-run again, then keep the saved version: nothing changes.
    client.post(f"/api/reports/{rep['id']}/reprocess", headers=admin)
    r = client.post(f"/api/reports/{rep['id']}/discard-reextraction", headers=admin)
    assert r.status_code == 200 and "reextracted" not in r.json()["draft"]

    # A failed re-run leaves the approved report as it was.
    def boom(*a, **k):
        raise RuntimeError("unreadable")
    from app.services import ingest
    monkeypatch.setattr(ingest, "extract_text", boom)
    r = client.post(f"/api/reports/{rep['id']}/reprocess", headers=admin)
    assert r.status_code == 422
    assert client.get(f"/api/reports/{rep['id']}", headers=admin).json()["status"] == "confirmed"


def test_mmda_detection():
    from app.services import places, rules

    assert places.find_mmda("The property lies within the Adentan Municipal Assembly.") == "Adentan Municipal"
    assert places.find_mmda("under La Nkwantanang-Madina Municipal Assembly") == "La Nkwantanang-Madina Municipal"
    assert places.find_mmda("Sekondi-Takoradi Metropolitan Assembly") == "Sekondi-Takoradi Metropolitan"
    assert places.find_mmda("The Local Government Act") is None
    r = places.resolve
    assert r("Osu", None, None, None, "")["district"] == "Korle Klottey Municipal"
    assert r("Madina", None, None, None, "")["district"] == "La Nkwantanang-Madina Municipal"
    assert r(None, None, "Tamale", None, "")["district"] == "Tamale Metropolitan"
    # Look-alikes stay blank rather than guess wrong
    assert r("Santeo - East Legon Hills, Accra", None, None, None, "")["district"] is None
    assert r("Community 25, Tema", None, None, None, "")["district"] is None
    assert r("Community 4, Tema", None, None, None, "")["district"] == "Tema Metropolitan"
    # An assembly named in the report wins
    rec = rules.extract(GHANA_REPORT + "\nThe site falls within the Adentan Municipal Assembly.")["records"][0]
    assert rec["district"] == "Adentan Municipal" and rec["city"] == "Accra"


def test_district_field_filter_and_group(client, admin):
    r = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Osu", "city": "Accra",
                                                        "district": "Korle Klottey Municipal", "capital_value": 900000,
                                                        "record_date": "2025-02-01"})
    assert r.status_code == 201 and r.json()["district"] == "Korle Klottey Municipal"
    assert client.get("/api/records?district=korle klottey", headers=admin).json()["total"] == 1
    assert client.get("/api/records?q=Klottey", headers=admin).json()["total"] == 1
    rows = client.get("/api/analytics/breakdown?group_by=district", headers=admin).json()["rows"]
    assert "Korle Klottey Municipal" in {x["district"] for x in rows}
    facets = client.get("/api/records/facets", headers=admin).json()
    assert "Adentan Municipal" in facets["districts"] and "Ayawaso West Municipal" in facets["districts"]


def test_add_missing_columns_upgrades_old_database(tmp_path):
    """An existing database without the new column gets it added, keeping its data."""
    from sqlalchemy import create_engine, inspect, text

    import pytest

    from app.database import DB_SCHEMA, Base, add_missing_columns

    if DB_SCHEMA:
        pytest.skip("SQLite-only check; Postgres upgrades are rehearsed against a real database")
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(text("DROP INDEX ix_records_district"))
        c.execute(text("ALTER TABLE records DROP COLUMN district"))
        c.execute(text("INSERT INTO records (category, currency, location, created_at, updated_at) VALUES ('valuation', 'GHS', 'Osu', '2025-01-01', '2025-01-01')"))
    assert "district" not in {c["name"] for c in inspect(eng).get_columns("records")}
    assert add_missing_columns(eng) == ["records.district"]
    assert "district" in {c["name"] for c in inspect(eng).get_columns("records")}
    assert "ix_records_district" in {i["name"] for i in inspect(eng).get_indexes("records")}
    with eng.connect() as c:
        assert c.execute(text("SELECT location FROM records")).scalar() == "Osu"
    assert add_missing_columns(eng) == []  # idempotent


def test_new_valuation_and_address_fields_extracted():
    from app.services import rules

    text = GHANA_REPORT + """
GPS Address: GA-183-8164
Client: Example Bank Ltd
Purpose of valuation: Mortgage
Property valued: A 5-bedroom mansion with boys quarters on 0.25 acres
Basis of value: Market Value and Forced Sale Value
Assumptions: Title is good and marketable
Special assumptions: Building completed as per approved plans
Valuer: Kofi Mensah, MGhIS, Pinnacle Estate Surveyors"""
    r = rules.extract(text)["records"][0]
    assert r["gps_address"] == "GA-183-8164"
    assert r["client"] == "Example Bank Ltd" and r["purpose"] == "Mortgage / loan security"
    assert r["property_valued"].startswith("A 5-bedroom mansion")
    assert r["basis_of_value"] == "Market Value"  # one basis; the forced sale value is a separate figure
    assert "Special: Building completed" in r["assumptions"]
    assert r["valuer"].startswith("Kofi Mensah")
    assert rules.find_gps("digital address ga 039 5028") == "GA-039-5028"


def test_excel_round_trip_import(client, admin):
    """Export to Excel, edit the file as a user would in Excel, upload it back."""
    import io as _io

    import openpyxl

    rid = client.post("/api/records", headers=admin, json={
        "category": "valuation", "location": "Osu", "city": "Accra", "size_sqm": 100, "capital_value": 1000000,
        "tags": ["office"], "record_date": "2025-03-01"}).json()["id"]
    xlsx = client.get(f"/api/exports/evidence?format=xlsx&q=Osu", headers=admin).content
    wb = openpyxl.load_workbook(_io.BytesIO(xlsx))
    ws = wb["All records"]
    header = [c.value for c in ws[4]]
    col = {name: header.index(name) + 1 for name in header}
    row = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, col["Ref"]).value == rid)
    ws.cell(row, col["Capital value (opinion of value)"]).value = 1500000      # edit a figure
    ws.cell(row, col["District (MMDA)"]).value = "Korle Klottey Municipal"     # fill a blank
    ws.cell(row, col["GPS address"]).value = "GA-100-2000"
    new_row = ws.max_row + 1                                                   # add a brand-new record
    ws.cell(new_row, col["Category"]).value = "cost"
    ws.cell(new_row, col["Location"]).value = "Spintex"
    ws.cell(new_row, col["Total construction cost"]).value = 2400000
    ws.cell(new_row, col["Size / GFA (m²)"]).value = 300
    bad_row = ws.max_row + 1                                                   # a row with a mistake
    ws.cell(bad_row, col["Ref"]).value = 999999
    buf = _io.BytesIO(); wb.save(buf)
    files = lambda: {"file": ("edited.xlsx", buf.getvalue(), "application/octet-stream")}

    plan = client.post("/api/imports/records/preview", headers=admin, files=files()).json()
    upd = next(u for u in plan["updates"] if u["id"] == rid)
    assert upd["changes"]["capital_value"] == [1000000, 1500000]
    assert upd["changes"]["district"] == [None, "Korle Klottey Municipal"]
    assert len(plan["creates"]) == 1 and plan["creates"][0]["values"]["location"] == "Spintex"
    assert any("999999" in e["error"] for e in plan["errors"])
    # preview saves nothing
    assert client.get(f"/api/records/{rid}", headers=admin).json()["capital_value"] == 1000000

    res = client.post("/api/imports/records/apply", headers=admin, files=files()).json()
    assert res["updated"] >= 1 and res["created"] == 1
    rec = client.get(f"/api/records/{rid}", headers=admin).json()
    assert rec["capital_value"] == 1500000 and rec["value_per_sqm"] == 15000  # derived figure recomputed
    assert rec["district"] == "Korle Klottey Municipal" and rec["gps_address"] == "GA-100-2000"
    assert rec["tags"] == ["office"]  # untouched cells unchanged
    spintex = client.get("/api/records?q=Spintex&category=cost", headers=admin).json()["items"]
    assert spintex and spintex[0]["cost_per_sqm"] == 8000

    # Uploading the same file again changes nothing for the edited record
    again = client.post("/api/imports/records/preview", headers=admin, files=files()).json()
    assert not any(u["id"] == rid for u in again["updates"])
    assert again["creates"] == [] and any("already imported" in e["error"] for e in again["errors"])
    # Viewers cannot import; non-Excel files are rejected clearly
    assert client.post("/api/imports/records/preview", headers=admin,
                       files={"file": ("x.xlsx", b"not excel", "application/octet-stream")}).status_code == 400


FULL_REPORT = """VALUATION REPORT
Property name: Plot 12, Block C, Santeo Estate
Owner: Mr. Kwame Asante
Use / occupancy: Residential, owner-occupied
Highest and best use: Residential
Address: Quartey Papafio Hills, Santeo
GPS Address: GA-183-8164
GPS coordinates: 5.6396 N, 0.1676 W
Land size: 0.25 acres (1,011.7 sq m)
Accessibility: Tarred road, 2 km from the Adjiringanor road
Building type: Two-storey detached house
Accommodation: 5 bedrooms en-suite, living, dining, kitchen
Construction details: Sandcrete block walls, concrete floors, aluminium roofing
Services: Pipe-borne water, ECG electricity, septic tank
Floor areas: Ground 110 sq m, First 96.55 sq m
Ancillary structures: Boys quarters, garage, fence wall
Ownership: Private
Tenure: Leasehold
Source of title: Land Title Certificate No. GA 12345
Unexpired term: 87 years
Planning status: Building permit obtained
Purpose of valuation: Mortgage
Basis of value: Market Value and Forced Sale Value
Date of inspection: 20 July 2023
Date of valuation: 1 August 2023
Market value: GHS 2,300,000
Forced sale value: GHS 1,610,000
Location rating: Good
State of repair: Good
Overall marketability: Very good
Encumbrances: None known
Legal disclaimer: This report is not a legal opinion on title
Inspection limitation: No structural survey was carried out
Purpose restriction: For mortgage purposes only
Assumptions: Title is good and marketable"""


def test_report_sections_extracted():
    from app.services import rules

    r = rules.extract(FULL_REPORT)["records"][0]
    expect = {
        "property_name": "Plot 12, Block C, Santeo Estate", "owner": "Mr. Kwame Asante",
        "use_occupancy": "Residential, owner-occupied", "highest_best_use": "Residential",
        "gps_address": "GA-183-8164", "gps_coordinates": "5.6396, -0.1676",
        "land_size": "0.25 acres (1,011.7 sq m)", "building_type": "Two-storey detached house",
        "ancillary_structures": "Boys quarters, garage, fence wall", "ownership": "Private", "tenure": "Leasehold",
        "source_of_title": "Land Title Certificate No. GA 12345", "unexpired_term": "87 years",
        "planning_status": "Building permit obtained", "inspection_date": "2023-07-20", "record_date": "2023-08-01",
        "capital_value": 2300000, "forced_sale_value": 1610000, "location_rating": "Good",
        "state_of_repair": "Good", "overall_marketability": "Very good", "encumbrances": "None known",
        "purpose_restriction": "For mortgage purposes only", "property_type": "residential",
    }
    wrong = {k: (r.get(k), v) for k, v in expect.items() if r.get(k) != v}
    assert not wrong, wrong


def test_attachments(client, admin, make_user):
    rid = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Osu",
                                                        "tenure": "Leasehold", "forced_sale_value": 700000,
                                                        "inspection_date": "2025-01-10"}).json()["id"]
    rec = client.get(f"/api/records/{rid}", headers=admin).json()
    assert rec["tenure"] == "Leasehold" and rec["forced_sale_value"] == 700000 and rec["inspection_date"] == "2025-01-10"
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    r = client.post(f"/api/records/{rid}/attachments", headers=admin, data={"kind": "photo"},
                    files=[("files", ("front.png", png, "image/png")), ("files", ("rear.png", png, "image/png"))])
    assert r.status_code == 201, r.text
    client.post(f"/api/records/{rid}/attachments", headers=admin, data={"kind": "site_plan"},
                files=[("files", ("site plan.pdf", b"%PDF-1.4 x", "application/pdf"))])
    items = client.get(f"/api/records/{rid}/attachments", headers=admin).json()
    assert [(a["kind"], a["filename"]) for a in items] == [("photo", "front.png"), ("photo", "rear.png"),
                                                           ("site_plan", "site plan.pdf")]
    got = client.get(f"/api/attachments/{items[0]['id']}", headers=admin)
    assert got.content == png and got.headers["content-type"] == "image/png"
    assert "attachment;" in client.get(f"/api/attachments/{items[2]['id']}?download=true", headers=admin).headers["content-disposition"]
    # rules: bad kind, bad type, viewer can't upload, junior can't delete
    assert client.post(f"/api/records/{rid}/attachments", headers=admin, data={"kind": "selfie"},
                       files=[("files", ("a.png", png, "image/png"))]).status_code == 422
    assert client.post(f"/api/records/{rid}/attachments", headers=admin, data={"kind": "photo"},
                       files=[("files", ("a.exe", b"MZ", "application/octet-stream"))]).status_code == 415
    viewer, junior = make_user("viewer"), make_user("junior")
    assert client.get(f"/api/records/{rid}/attachments", headers=viewer).status_code == 200
    assert client.post(f"/api/records/{rid}/attachments", headers=viewer, data={"kind": "photo"},
                       files=[("files", ("a.png", png, "image/png"))]).status_code == 403
    assert client.delete(f"/api/attachments/{items[1]['id']}", headers=junior).status_code == 403
    assert client.delete(f"/api/attachments/{items[1]['id']}", headers=admin).status_code == 204
    # deleting the record removes its attachments
    assert client.delete(f"/api/records/{rid}", headers=admin).status_code == 204
    assert client.get(f"/api/attachments/{items[0]['id']}", headers=admin).status_code == 404


def test_new_sections_in_excel_and_import(client, admin):
    import io as _io

    import openpyxl

    rid = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Labone",
                                                        "tenure": "Leasehold"}).json()["id"]
    wb = openpyxl.load_workbook(_io.BytesIO(client.get("/api/exports/evidence?format=xlsx&q=Labone", headers=admin).content))
    ws = wb["All records"]
    header = [c.value for c in ws[4]]
    for name in ("Owner", "Tenure", "Date of inspection", "Forced sale value", "Overall marketability", "Encumbrances"):
        assert name in header, name
    row = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value == rid)
    ws.cell(row, header.index("Forced sale value") + 1).value = 650000
    ws.cell(row, header.index("Date of inspection") + 1).value = "2025-02-14"
    buf = _io.BytesIO(); wb.save(buf)
    res = client.post("/api/imports/records/apply", headers=admin,
                      files={"file": ("e.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    assert res["updated"] == 1, res
    rec = client.get(f"/api/records/{rid}", headers=admin).json()
    assert rec["forced_sale_value"] == 650000 and rec["inspection_date"] == "2025-02-14"


def test_mmda_rejects_area_descriptions():
    from app.services import places, rules

    ex = lambda t: rules.extract("VALUATION REPORT\n" + t + "\nMarket value: GHS 1,000,000")["records"][0]
    r = ex("Location: Central Business District, Accra")
    assert (r["location"], r["district"]) == ("Central Business District", "Accra Metropolitan")
    r = ex("District: Central Business District\nCity: Accra")
    assert (r["location"], r["district"]) == ("Central Business District", "Accra Metropolitan")
    r = ex("The property is located in the Central Business District of Accra.")
    assert (r["location"], r["district"], r["city"]) == ("Central Business District", "Accra Metropolitan", "Accra")
    assert ex("District: adentan municipal")["district"] == "Adentan Municipal"
    assert "location" not in ex("District: adentan municipal")
    assert ex("Shai-Osudoku District Assembly")["district"] == "Shai-Osudoku District"
    for bad in ("Central Business District", "Industrial Area", "Residential District", "Commercial District"):
        assert places.normalise_mmda(bad) is None, bad
    assert places.normalise_mmda("accra metropolitan assembly") == "Accra Metropolitan"
    assert places.normalise_mmda("Nkoranza South Municipal") == "Nkoranza South Municipal"  # any real-looking assembly
    assert places.normalise_mmda("Bosomtwe District") == "Bosomtwe District"  # one of the 261 official MMDAs
    assert places.normalise_mmda("Abcdefg District") is None  # unknown "X District" needs "Assembly"
    assert places.normalise_mmda("Abcdefg District Assembly", said_assembly=True) == "Abcdefg District"
    assert len(places.ALL_MMDAS) == 261
    assert places.normalise_mmda("Kumasi Metropolis") == "Kumasi Metropolitan"
    assert places.normalise_mmda("Korle-Klottey Municipal Assembly") == "Korle Klottey Municipal"
    assert places.normalise_mmda("Adenta Municipal") == "Adentan Municipal"
    r = ex("Location: Santeo\nThe site is about 20km from the Central Business District of Accra.")
    assert r["location"] == "Santeo" and r.get("district") is None


def test_ai_mmda_answer_is_checked(client, admin, monkeypatch):
    from app.config import Settings
    from app.services import llm

    monkeypatch.setattr(Settings, "ai_enabled", property(lambda self: True))
    monkeypatch.setattr(llm, "extract_report", lambda *a, **k: {"report_type": "valuation", "records": [
        {"category": "valuation", "location": "Adabraka", "city": "Accra", "district": "Central Business District",
         "capital_value": 5000000}]})
    rep = client.post("/api/reports/upload?sync=true", headers=admin, files=[("files", (
        "cbd.txt", b"VALUATION REPORT\nThe property lies within the Korle Klottey Municipal Assembly.", "text/plain"))]).json()[0]
    rec = rep["draft"]["records"][0]
    assert rec["district"] == "Korle Klottey Municipal"  # wrong AI value replaced by the assembly named in the report
    assert rec["location"] == "Adabraka"


def test_startup_corrects_saved_non_assembly_mmda(client, admin):
    from app.main import _fix_mmda_values

    rid = client.post("/api/records", headers=admin, json={"category": "valuation", "city": "Accra",
                                                        "district": "Central Business District"}).json()["id"]
    keep = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Osu",
                                                         "district": "Korle Klottey Municipal"}).json()["id"]
    assert _fix_mmda_values() >= 1
    r = client.get(f"/api/records/{rid}", headers=admin).json()
    assert (r["location"], r["district"]) == ("Central Business District", "Accra Metropolitan")
    assert client.get(f"/api/records/{keep}", headers=admin).json()["district"] == "Korle Klottey Municipal"
    assert _fix_mmda_values() == 0  # nothing left to fix
    assert "mmda_corrected" in {a["action"] for a in client.get("/api/activity", headers=admin).json()}


def test_money_amounts_and_currency():
    from app.services import rules

    head = "VALUATION REPORT\nExchange rate used: $1 = GH¢12.5\nBasis of value: Market Value and Forced Sale Value\n"
    cases = [
        ("Open Market Value as at 1st August 2023: GH¢2,300,000.00 (Two Million Three Hundred Thousand Ghana Cedis)", 2300000, None, "GHS"),
        ("Market Value (2023): GHS 2,300,000", 2300000, None, "GHS"),
        ("We are of the opinion that the market value of the property as at 1st August, 2023 is GH¢2,300,000.00", 2300000, None, "GHS"),
        ("Market value: 2,300,000.00 Ghana Cedis", 2300000, None, "GHS"),
        ("Market Value: GHS 2.3 million\nForced Sale Value: GHS 1.61 million", 2300000, 1610000, "GHS"),
        ("Market Value:\nGH¢ 2,300,000.00\nForced Sale Value:\nGH¢ 1,610,000.00", 2300000, 1610000, "GHS"),
        ("Market value: US$ 180,000 (equivalent GH¢2,250,000)", 180000, None, "USD"),
    ]
    for text, mv, fsv, cur in cases:
        r = rules.extract(head + text)["records"][0]
        assert (r.get("capital_value"), r.get("forced_sale_value"), r["currency"], r["basis_of_value"]) == (
            mv, fsv, cur, "Market Value"), (text, r)


def test_all_261_mmdas_available_to_the_page(client, admin):
    import json as _json
    import re as _re

    page = client.get("/").text
    data = _json.loads(_re.search(r"window\.PIS_MMDAS = (\[.*?\]);</script>", page, _re.S).group(1))
    assert len(data) == 261 and len({r[1] for r in data}) == 16
    assert ["Adentan Municipal", "Greater Accra", "Adenta"] in data
    api = client.get("/api/mmdas", headers=admin).json()
    assert len(api) == 261 and {"name", "region", "capital"} <= set(api[0])


def _comparables_docx() -> bytes:
    import io as _io

    import docx

    d = docx.Document()
    for line in ("VALUATION REPORT", "Location: East Legon, Accra", "Purpose of valuation: Mortgage",
                 "Basis of value: Market Value VALUATION OPINION", "COMPARABLE EVIDENCE"):
        d.add_paragraph(line)
    rows = [["No.", "Location", "Property Type", "Date of Sale", "Price (GH¢)", "Remarks"],
            ["1", "Plot 5, East Legon Hills", "4-bedroom house", "March 2023", "2,100,000.00", "Similar size"],
            ["2", "Santeo Estate", "5-bedroom house", "June 2023", "2,450,000.00", "Superior finishes"],
            ["3", "Oyarifa", "4-bedroom house", "Jan 2023", "1,850,000", "Older building"]]
    t = d.add_table(rows=len(rows), cols=len(rows[0]))
    for r, vals in zip(t.rows, rows):
        for c, v in zip(r.cells, vals):
            c.text = v
    d.add_paragraph("Market Value: GH¢2,300,000.00")
    buf = _io.BytesIO(); d.save(buf)
    return buf.getvalue()


def test_comparables_from_word_table_and_excel(client, admin):
    import io as _io

    import openpyxl

    rep = client.post("/api/reports/upload?sync=true", headers=admin,
                      files=[("files", ("comps.docx", _comparables_docx(), "application/octet-stream"))]).json()[0]
    rec = rep["draft"]["records"][0]
    assert rec["basis_of_value"] == "Market Value"          # not "Market Value VALUATION OPINION"
    assert rec["purpose"] == "Mortgage / loan security" and rec["capital_value"] == 2300000
    comps = rec["comparables"]
    assert [c["address"] for c in comps] == ["Plot 5, East Legon Hills", "Santeo Estate", "Oyarifa"]
    assert comps[0] == {"address": "Plot 5, East Legon Hills", "property_type": "4-bedroom house",
                        "date": "March 2023", "price": 2100000, "notes": "Similar size"}
    # user edits: drops one comparable, adds one
    rec["comparables"] = comps[:2] + [{"address": "Trasacco Valley", "price": 3100000, "size_sqm": 400}]
    client.post(f"/api/reports/{rep['id']}/confirm", headers=admin, json={"records": [rec]})
    saved = client.get(f"/api/records?report_id={rep['id']}", headers=admin).json()["items"][0]
    assert len(saved["comparables"]) == 3 and saved["comparables"][2]["price_per_sqm"] == 7750

    wb = openpyxl.load_workbook(_io.BytesIO(client.get(
        f"/api/exports/evidence?category=valuation&ids={saved['id']}&format=xlsx", headers=admin).content))
    assert "Comparables" in wb.sheetnames
    ev = list(wb["Evidence"].iter_rows(min_row=4, values_only=True))
    assert "Purpose of valuation" in ev[0] and "Basis of value" in ev[0]
    row = dict(zip(ev[0], ev[1]))
    assert (row["Purpose of valuation"], row["Basis of value"]) == ("Mortgage / loan security", "Market Value")
    cs = list(wb["Comparables"].iter_rows(min_row=4, values_only=True))
    head, body = cs[0], [dict(zip(cs[0], r)) for r in cs[1:]]
    assert len(body) == 3
    assert body[0]["Purpose of valuation"] == "Mortgage / loan security" and body[0]["Basis of value"] == "Market Value"
    assert body[0]["Comparable address / location"] == "Plot 5, East Legon Hills" and body[0]["Price"] == 2100000


def test_basis_of_value_always_single(client, admin):
    from app.main import _fix_basis_values
    from app.services.rules import normalise_basis

    assert normalise_basis("Market Value VALUATION OPINION") == "Market Value"
    assert normalise_basis("Market Value and Forced Sale Value") == "Market Value"
    assert normalise_basis("Open Market Value") == "Market Value"
    assert normalise_basis("Forced Sale Value") == "Forced Sale Value"
    rid = client.post("/api/records", headers=admin, json={"category": "valuation",
                                                        "basis_of_value": "Market Value VALUATION OPINION"}).json()["id"]
    assert client.get(f"/api/records/{rid}", headers=admin).json()["basis_of_value"] == "Market Value"
    r = client.patch(f"/api/records/{rid}", headers=admin, json={"basis_of_value": "market rent (valuation opinion)"})
    assert r.json()["basis_of_value"] == "Market Rent"
    assert _fix_basis_values() == 0  # nothing messy left


def test_gap_filler_infers_only_blanks():
    from app.services import gaps

    rec = {"category": "valuation", "location": "Madina", "address": "House 7, GA-415-2284, Madina Estates",
           "building_type": "Two-storey detached house", "accommodation": "4 bedrooms",
           "size_sqm": 200, "capital_value": 1800000, "annual_rent": 108000, "city": "Accra (my note)"}
    filled = gaps.infer(rec, "")
    got = {k: v for k, (v, _) in filled.items()}
    assert got["district"] == "La Nkwantanang-Madina Municipal"
    assert got["region"] == "Greater Accra"
    assert "city" not in got                                    # never overwrite a value already there
    assert got["gps_address"] == "GA-415-2284"
    assert got["property_type"] == "residential" and "4-bedroom" in got["tags"]
    assert got["value_per_sqm"] == 9000 and got["yield_pct"] == 6.0 and got["valuation_basis"] == "both"
    assert got["property_valued"] == "Two-storey detached house at Madina"
    for judgement in ("overall_marketability", "highest_best_use", "basis_of_value", "purpose", "forced_sale_value"):
        assert judgement not in got
    assert all(reason for _, reason in filled.values())          # every fill says why
    # MMDA known but no location: region and city follow from the MMDA
    got2 = {k: v for k, (v, _) in gaps.infer({"category": "valuation", "district": "Bosomtwe District"}).items()}
    assert got2["region"] == "Ashanti" and got2["city"] == "Kuntanase"


def test_fill_gaps_endpoint_uses_report_text(client, admin):
    rep = client.post("/api/reports/upload?sync=true", headers=admin, files=[("files", ("gap.txt", (
        "VALUATION REPORT\nLocation: Osu, Accra\nTenure: Leasehold\nUnexpired term: 45 years\n"
        "Building type: Three-storey office block\n"
        "Market value: GHS 3,000,000\nGross floor area: 300 sq m").encode(), "text/plain"))]).json()[0]
    draft = rep["draft"]["records"][0]
    assert draft["district"] == "Korle Klottey Municipal"
    assert draft["property_valued"] == "Three-storey office block at Osu"   # gap filled on upload ...
    assert draft["autofilled"]["property_valued"].startswith("summarised")  # ... and marked for checking
    # the reviewer cleared tenure and the MMDA; Fill gaps brings them back from the report
    current = dict(draft, tenure=None, district=None, unexpired_term=None)
    current.pop("autofilled")
    res = client.post("/api/gaps/fill", headers=admin, json={"record": current, "report_id": rep["id"]}).json()
    assert res["record"]["tenure"] == "Leasehold" and res["record"]["unexpired_term"] == "45 years"
    assert res["record"]["district"] == "Korle Klottey Municipal"
    assert res["filled"]["tenure"]["reason"] == "from the report text"


def test_fill_gaps_bulk(client, admin, make_user):
    rid = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Dansoman",
                                                        "size_sqm": 150, "capital_value": 900000}).json()["id"]
    prev = client.get("/api/gaps/bulk/preview?q=Dansoman", headers=admin).json()
    item = next(r for r in prev["records"] if r["id"] == rid)
    assert item["filled"]["district"]["value"] == "Ablekuma West Municipal"
    assert client.get(f"/api/records/{rid}", headers=admin).json()["district"] is None   # preview saves nothing
    assert client.post("/api/gaps/bulk/apply?q=Dansoman", headers=make_user("viewer")).status_code == 403
    res = client.post("/api/gaps/bulk/apply?q=Dansoman", headers=admin).json()
    assert res["records"] >= 1
    rec = client.get(f"/api/records/{rid}", headers=admin).json()
    assert (rec["district"], rec["region"], rec["city"]) == ("Ablekuma West Municipal", "Greater Accra", "Accra")
    assert rec["capital_value"] == 900000                     # existing values untouched
    again = client.get("/api/gaps/bulk/preview?q=Dansoman", headers=admin).json()
    assert not any(r["id"] == rid for r in again["records"])  # nothing left to fill


def test_long_text_in_short_fields_saves(client, admin):
    """Regression: a sentence in 'State of repair' made saving fail with a 500 on Postgres."""
    sentence = "The property is newly built and appears to be in a good structural and tenantable condition."
    rep = client.post("/api/reports/upload?sync=true", headers=admin, files=[("files", ("repair.txt", (
        "VALUATION REPORT\nLocation: Osu, Accra\nCondition: " + sentence + "\nMarket value: GH¢ 2 300 000.00"
    ).encode(), "text/plain"))]).json()[0]
    draft = rep["draft"]["records"][0]
    assert draft["state_of_repair"] == "Good" and draft["capital_value"] == 2300000
    r = client.post(f"/api/reports/{rep['id']}/confirm", headers=admin, json={"records": [draft]})
    assert r.status_code == 200, r.text
    rid = client.get(f"/api/records?report_id={rep['id']}", headers=admin).json()["items"][0]["id"]
    # a long sentence typed straight into a rating, and an over-long GPS address, still save
    r = client.patch(f"/api/records/{rid}", headers=admin, json={
        "state_of_repair": sentence, "overall_marketability": "Overall, marketability is considered very good " * 10,
        "user_conformity": "The current use conforms to the zoning regulations of the area",
        "gps_address": "GA-183-8164 " * 12})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert (rec["state_of_repair"], rec["overall_marketability"], rec["user_conformity"]) == (
        "Good", "Very good", "Conforming")
    assert len(rec["gps_address"]) <= 80
    # update after a re-run (the exact request that failed live)
    client.post(f"/api/reports/{rep['id']}/reprocess", headers=admin)
    body = client.get(f"/api/reports/{rep['id']}", headers=admin).json()["draft"]["records"]
    body[0]["state_of_repair"] = sentence
    assert client.post(f"/api/reports/{rep['id']}/update-records", headers=admin,
                       json={"records": body}).status_code == 200


def test_unexpected_errors_are_explained(client, admin, monkeypatch):
    from app.routers import records as records_router

    def boom(*a, **k):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(records_router, "record_from_payload", boom)
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/records", headers=admin, json={"category": "valuation"})
    assert r.status_code == 500 and "nothing was saved" in r.json()["detail"]


def test_value_opinion_formats_and_words():
    from app.services import rules

    assert rules.words_to_number("Two Million, Three Hundred Thousand") == 2300000
    assert rules.words_to_number("Six Hundred and Fifty Thousand Five Hundred") == 650500
    head = "VALUATION REPORT\nGross floor area: 206.55 sq m\nBasis of value: Market Value and Forced Sale Value\n"
    for text in ("Market Value: GH¢2,300,\n000.00",                       # number split over two lines
                 "Market Value: GH¢ 2,300’000.00",                       # apostrophe separators
                 "Market Value: GH¢2.300.000,00",                        # dot separators
                 "Market Value (GH¢'000) | 2,300",                       # figure given in thousands
                 "MARKET VALUE\nTwo Million, Three Hundred Thousand Ghana Cedis (GH¢2,300,000.00)",
                 "Market Value: GH¢2,300 (Two Million, Three Hundred Thousand Ghana Cedis)"):  # digits cut short
        r = rules.extract(head + text)["records"][0]
        assert r["capital_value"] == 2300000, (text, r.get("capital_value"))
        assert r.get("forced_sale_value") is None, text


def test_dashboard_leaves_out_misread_values(client, admin):
    bad = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Zz Test Area",
                                                         "size_sqm": 206.55, "capital_value": 2300}).json()["id"]
    good = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Zz Test Area",
                                                          "size_sqm": 200, "capital_value": 2000000}).json()["id"]
    s = client.get("/api/analytics/summary?location=Zz Test Area", headers=admin).json()
    assert [x["id"] for x in s["suspect_values"]] == [bad]
    assert s["metrics"]["capital_value"]["mean"] == 2000000          # the 2,300 is not averaged in
    assert s["metrics"]["value_per_sqm"]["mean"] == 10000
    assert s["metrics"]["capital_value"]["label"] == "Value opinion"
    client.patch(f"/api/records/{bad}", headers=admin, json={"capital_value": 2300000})  # corrected
    s = client.get("/api/analytics/summary?location=Zz Test Area", headers=admin).json()
    assert s["suspect_values"] == [] and s["metrics"]["capital_value"]["count"] == 2
    assert client.get(f"/api/records/{good}", headers=admin).status_code == 200


def test_form_save_recalculates_rates(client, admin):
    """Forms send every field back; an old value-per-m² must not survive a corrected market value."""
    rid = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Recalc",
                                                        "size_sqm": 206.55, "capital_value": 2300}).json()["id"]
    rec = client.get(f"/api/records/{rid}", headers=admin).json()
    assert rec["value_per_sqm"] == 11.14
    body = {k: v for k, v in rec.items() if k not in ("id", "report_id", "created_at", "updated_at")}
    body["capital_value"] = 2300000          # corrected; value_per_sqm still 11.14 in the form
    rec = client.patch(f"/api/records/{rid}", headers=admin, json=body).json()
    assert rec["value_per_sqm"] == 11135.32
    body = dict(body, capital_value=2300000, value_per_sqm=12000)   # a figure the user typed on purpose
    assert client.patch(f"/api/records/{rid}", headers=admin, json=body).json()["value_per_sqm"] == 12000


def _build_report(script: str, tmp_path):
    import runpy
    import sys as _sys
    out = tmp_path / (script + ".docx")
    argv = _sys.argv
    try:
        _sys.argv = ["x", str(out)]
        runpy.run_path(str(__import__("pathlib").Path(__file__).parent / "fixtures" / f"{script}.py"), run_name="__main__")
    finally:
        _sys.argv = argv
    return out


def test_reads_a_narrative_report_without_ai(tmp_path):
    """A report written in numbered sections and sentences (no 'Label: value' lines)."""
    from app.services import gaps, rules
    from app.services.text_extract import extract_text

    text = extract_text(_build_report("make_santeo_report", tmp_path), "docx")
    rec = rules.extract(text)["records"][0]
    rec.update({k: v for k, (v, _) in gaps.infer(rec, text).items()})
    expect = {
        "purpose": "Mortgage / loan security", "client": "GCB Bank PLC", "basis_of_value": "Market Value",
        "inspection_date": "2023-07-19", "record_date": "2023-08-01", "location": "Santeo",
        "district": "La Nkwantanang-Madina Municipal", "city": "Accra", "region": "Greater Accra",
        "gps_address": "GA-183-8164", "gps_coordinates": "5.639611, -0.167528", "land_size": "0.10 acre (404.69 sq m)",
        "tenure": "Leasehold", "unexpired_term": "91 years (calculated: 99-year term from 2015)",
        "source_of_title": "Land Title Certificate No. GA 45678 Vol. 12 Folio 34", "owner": "Miss Leticia Otoo",
        "ownership": "Private (individual)", "building_type": "two-storey detached house", "size_sqm": 206.55,
        "state_of_repair": "Good", "method": "Sales Comparison Approach & Cost Approach (DRC)",
        "capital_value": 2300000, "forced_sale_value": 1610000, "valuer": "Audrey Darkwah, BSc, MGhIS",
        "property_type": "residential",
    }
    wrong = {k: (rec.get(k), v) for k, v in expect.items() if rec.get(k) != v}
    assert not wrong, wrong
    for k, needle in {"accommodation": "four bedrooms", "services": "ECG electricity", "construction_details": "sandcrete",
                      "planning_status": "building permit", "use_occupancy": "owner-occupied",
                      "accessibility": "tarred road", "inspection_limitation": "No structural survey",
                      "purpose_restriction": "sole use", "legal_disclaimer": "no responsibility",
                      "assumptions": "assumed"}.items():
        assert needle.lower() in (rec.get(k) or "").lower(), (k, rec.get(k))
    assert len(rec["comparables"]) == 3


def test_reads_a_differently_written_report(tmp_path):
    from app.services import gaps, rules
    from app.services.text_extract import extract_text

    text = extract_text(_build_report("make_kumasi_report", tmp_path), "docx")
    rec = rules.extract(text)["records"][0]
    rec.update({k: v for k, (v, _) in gaps.infer(rec, text).items()})
    expect = {"client": "Ecobank Ghana Limited", "purpose": "Mortgage / loan security", "record_date": "2024-03-15",
              "gps_address": "AK-039-5028", "district": "Kumasi Metropolitan", "region": "Ashanti", "city": "Kumasi",
              "tenure": "Leasehold", "unexpired_term": "31 years (calculated: 50-year term from 2005)",
              "land_size": "0.35 acres", "owner": "Adom Properties Limited", "building_type": "three-storey office block",
              "source_of_title": "Indenture dated 12th May 2005", "method": "Investment / Income Approach",
              "capital_value": 6500000, "forced_sale_value": 4550000, "valuer": "Kwame Mensah, FGhIS, MRICS",
              "property_type": "commercial"}
    wrong = {k: (rec.get(k), v) for k, v in expect.items() if rec.get(k) != v}
    assert not wrong, wrong


def test_pdf_layout_tables_and_wrapped_sentences():
    from app.services.text_extract import tidy_pdf_text

    raw = """16.0 VALUATION

Having regard to the foregoing, we are of the opinion that the Market Value of the leasehold
interest in the property as at 1st August, 2023 is GH¢2,300,000.00 (Two Million, Three Hundred
Thousand Ghana Cedis).

No.        Location              Description          Date              Price (GH¢)

1          East Legon Hills      4-bedroom            March 2023        2,100,000
                                 house

2          Santeo                5-bedroom            June 2023         2,450,000
                                 house
"""
    out = tidy_pdf_text(raw).splitlines()
    assert out[0] == "16.0 VALUATION"
    assert out[1].startswith("Having regard") and out[1].endswith("Ghana Cedis).")
    assert out[2] == "No. | Location | Description | Date | Price (GH¢)"
    assert out[3] == "1 | East Legon Hills | 4-bedroom house | March 2023 | 2,100,000"
    assert out[4] == "2 | Santeo | 5-bedroom house | June 2023 | 2,450,000"


def test_gemini_self_check(monkeypatch):
    import json as _json
    from types import SimpleNamespace

    from google.genai import errors, types

    from app.config import settings
    from app.services import gemini

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-old")
    answer = {"report_type": "valuation", "records": [{"category": "valuation", "capital_value": 3000000,
                                                        "district": "Korle Klottey Municipal", "tenure": "Leasehold"}]}

    class FakeModels:
        def generate_content(self, model, contents, config):
            if model == "gemini-old":
                raise errors.ClientError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
            return SimpleNamespace(text=_json.dumps(answer),
                                   candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)])

        def list(self):
            return [SimpleNamespace(name="models/gemini-9.0-flash", supported_actions=["generateContent"]),
                    SimpleNamespace(name="models/gemini-9.0-flash-preview", supported_actions=["generateContent"]),
                    SimpleNamespace(name="models/text-embedding-004", supported_actions=["embedContent"])]

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    status = gemini.self_check()
    assert status["ok"] is True and status["model"] == "gemini-9.0-flash"
    assert "chosen over gemini-old" in status["detail"] and "3,000,000" in status["detail"]

    class BadKey(FakeModels):
        def generate_content(self, model, contents, config):
            raise errors.ClientError(403, {"error": {"code": 403, "message": "API key not valid", "status": "PERMISSION_DENIED"}})

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=BadKey()))
    status = gemini.self_check()
    assert status["ok"] is False and "invalid" in status["detail"]


def test_averages_by_basis_of_value(client, admin):
    rows = [
        {"basis_of_value": "Market Value", "capital_value": 1_000_000, "size_sqm": 200, "forced_sale_value": 700_000},
        {"basis_of_value": "Market Value", "capital_value": 3_000_000, "size_sqm": 300, "annual_rent": 120_000},
        {"basis_of_value": "Fair Value", "capital_value": 2_000_000},
        {"basis_of_value": "Marriage Value", "capital_value": 5_000_000},
        {"capital_value": 900_000},
    ]
    for row in rows:
        r = client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Basistown", **row})
        assert r.status_code in (200, 201), r.text
    s = client.get("/api/analytics/summary?location=Basistown", headers=admin).json()
    by = {b["basis"]: b for b in s["by_basis"]}
    assert by["Market Value"]["count"] == 2 and by["Market Value"]["mean"] == 2_000_000
    assert by["Market Value"]["per_sqm"] == 7_500
    assert by["Fair Value"]["mean"] == 2_000_000 and by["Marriage Value"]["count"] == 1
    assert by["Forced Sale Value"]["mean"] == 700_000          # stated alongside market value
    assert by["Market Rent"]["mean"] == 120_000 and by["Market Rent"]["per_year"]
    assert by["Not stated"]["count"] == 1
    assert s["by_basis"][0]["basis"] == "Market Value"

    mv = client.get("/api/analytics/summary?location=Basistown&basis_of_value=Market Value", headers=admin).json()
    assert mv["metrics"]["capital_value"]["mean"] == 2_000_000 and "basis of value: Market Value" in mv["scope"]
    fv = client.get("/api/records?location=Basistown&basis_of_value=Fair Value", headers=admin).json()
    assert fv["total"] == 1
    fsv = client.get("/api/records?location=Basistown&basis_of_value=Forced Sale Value", headers=admin).json()
    assert fsv["total"] == 1
    br = client.get("/api/analytics/breakdown?group_by=basis_of_value", headers=admin)
    assert br.status_code == 200


def test_basis_aliases():
    from app.services.rules import normalise_basis
    assert normalise_basis("Worth") == "Investment Value"
    assert normalise_basis("Open Market Value") == "Market Value"
    assert normalise_basis("Synergistic Value") == "Synergistic Value"
    assert normalise_basis("Marriage value of the merged plots") == "Marriage Value"
    assert normalise_basis("rental value") == "Market Rent"
    assert normalise_basis("Market Value VALUATION OPINION") == "Market Value"


def test_basis_analysis_endpoint(client, admin):
    rows = [
        {"location": "Rentville", "property_type": "Residential", "basis_of_value": "Market Value",
         "capital_value": 2_000_000, "annual_rent": 100_000, "size_sqm": 200},
        {"location": "Rentville", "property_type": "Commercial", "basis_of_value": "Market Value",
         "capital_value": 4_000_000, "annual_rent": 300_000, "size_sqm": 400},
        {"location": "Rentville", "property_type": "Residential", "basis_of_value": "Fair Value", "capital_value": 1_000_000},
    ]
    for row in rows:
        assert client.post("/api/records", headers=admin, json={"category": "valuation", **row}).status_code == 201
    a = client.get("/api/analytics/basis?location=Rentville&basis=Market Value", headers=admin).json()
    assert a["overall"]["Market Value"]["mean"] == 3_000_000 and a["overall"]["Market Value"]["per_sqm"] == 10_000
    types = {r["name"].lower(): r for r in a["by_property_type"]}
    assert types["residential"]["Market Value"]["count"] == 1      # Fair Value record not mixed in
    both = client.get("/api/analytics/basis?location=Rentville&basis=Market Value&compare=Market Rent", headers=admin).json()
    assert both["overall"]["Market Rent"]["mean"] == 200_000
    assert both["yield_pct"] == round(200_000 / 3_000_000 * 100, 2)
    assert both["by_location"][0]["yield_pct"] is not None
    rent = client.get("/api/records?location=Rentville&basis_of_value=Market Rent", headers=admin).json()
    assert rent["total"] == 2


def test_basis_analysis_period(client, admin):
    for d, v in (("2023-03-01", 1_000_000), ("2025-06-01", 3_000_000)):
        client.post("/api/records", headers=admin, json={"category": "valuation", "location": "Datetown",
                    "basis_of_value": "Market Value", "capital_value": v, "record_date": d})
    a = client.get("/api/analytics/basis?location=Datetown&basis=Market Value", headers=admin).json()
    assert a["date_range"] == ["2023-03-01", "2025-06-01"] and a["overall"]["Market Value"]["count"] == 2
    b = client.get("/api/analytics/basis?location=Datetown&basis=Market Value&date_from=2025-01-01&interval=year",
                   headers=admin).json()
    assert b["overall"]["Market Value"]["mean"] == 3_000_000 and b["trend"]["Market Value"][0]["period"] == "2025"


def test_not_stated_saved_as_blank(client, admin):
    r = client.post("/api/records", headers=admin, json={
        "category": "valuation", "location": "Blankville", "tenure": "Not stated", "size_sqm": "N/A",
        "inspection_date": "—", "capital_value": 2_000_000, "encumbrances": "None"})
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["tenure"] is None and rec["size_sqm"] is None and rec["inspection_date"] is None
    assert rec["encumbrances"] == "None"          # a real answer, kept
    u = client.patch(f"/api/records/{rec['id']}", headers=admin, json={"owner": "not stated"})
    assert u.status_code == 200 and u.json()["owner"] is None


def test_review_form_marks_empty_fields_not_stated(client):
    js = client.get("/static/app.js").text
    assert 'const NS = "Not stated"' in js and "ns-tag" in js
