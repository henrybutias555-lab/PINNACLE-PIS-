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
    assert rows[0][:3] == ("Ref", "Date", "Address")
    data = rows[1:]
    assert len(data) == 3, data
    assert all(isinstance(x[7], (int, float)) for x in data)  # capital values are numbers, not text


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
