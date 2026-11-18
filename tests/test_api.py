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
