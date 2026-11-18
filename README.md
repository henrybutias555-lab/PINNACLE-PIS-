# Pinnacle Intelligence System (PIS)

An AI-powered internal platform for **Pinnacle Estate Surveyors (PES)**. It extracts, stores, organises and analyses data from valuation, construction cost and housing reports. The aim is faster, more consistent, data-driven valuation and cost advisory work.

Built from the PRD (v1.0). This repository holds a working **Phase 1 MVP**.

## Features (mapped to the PRD)

| PRD section | What is implemented |
|---|---|
| **4.1 Report ingestion** | Single or bulk upload of PDF, Word (.docx), text and scanned images. Extraction runs in the background. Claude reads the document (including scanned PDFs and images) and returns structured data. Nothing is saved until a surveyor **reviews, edits and confirms** the draft. Reports can be re-run, rejected or deleted. |
| **4.2 Data organisation & search** | Structured storage for valuation, cost and housing records: values, rents, yields, rates per m², elemental cost breakdowns, comparables, housing indicators. Search and filter by free text, location, city, property type, date range, value/cost range, report type, purpose and tag, with sorting and paging. |
| **4.3 Market & cost intelligence** | Location-based summaries (mean, median, range, count), averages by area × property type, monthly/quarterly/yearly trend charts, a comparable-evidence finder ranked by similarity, **cross-analysis** of construction cost/m² against capital value/m², and **suggested market commentary** (Claude-drafted, with a template fallback). |
| **4.4 Report support tools** | One-click comparable / cost / housing evidence tables in **Word** (firm-branded) or **CSV**. Commentary exports to Word or text. |
| **4.5 User management & security** | Roles: **Admin, Senior Surveyor, Junior Surveyor, Viewer**. JWT sign-in with bcrypt-hashed passwords, plus an activity log covering logins, failed logins, uploads, confirmations, edits, deletions and exports. |
| **AI requirements** | Document AI/OCR and LLM summarisation through Claude. Statistics in Python. Similarity search for comparables. |

### Role permissions

| Action | Viewer | Junior | Senior | Admin |
|---|:-:|:-:|:-:|:-:|
| Search, analytics, commentary, exports | ✓ | ✓ | ✓ | ✓ |
| Upload, review and confirm reports; add and edit records | | ✓ | ✓ | ✓ |
| Delete records and reports | | | ✓ | ✓ |
| Manage users, view activity log | | | | ✓ |

## Quick start

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then edit: set PIS_SECRET_KEY, admin password, and optionally ANTHROPIC_API_KEY
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 and sign in with the admin account from `.env` (default `admin@pinnacle.local` / `ChangeMe123!`). **Change this password straight away** using "Change password" in the sidebar.

To see the dashboards with illustrative data:

```bash
python -m scripts.seed_demo          # adds records tagged "demo"
python -m scripts.seed_demo --clear  # removes them again
```

The interactive API documentation is at http://localhost:8000/docs.

## AI configuration

Set `ANTHROPIC_API_KEY` in `.env` to turn on Claude. The model defaults to `claude-opus-5-5` and can be changed with `PIS_CLAUDE_MODEL`.

- **Extraction:** the PDF (or image, or the document's text) goes to Claude, which must return JSON matching a strict schema. Missing values come back as `null`; the prompt tells Claude never to estimate figures.
- **Commentary:** Claude writes prose only from the statistics the system computed, and the surveyor edits it before use.
- Requests use server-side refusal fallbacks (`fallbacks: "default"`).
- **Without a key,** or if an AI call fails, the system falls back to rule-based extraction and template commentary. The UI shows which method produced each draft.

Without a key, scanned documents need OCR. Install Tesseract plus `pip install pytesseract pdf2image` and it is used automatically.

> Uploaded reports are sent to the Anthropic API when AI is enabled. Confirm this fits the firm's data-handling policy and client confidentiality terms before turning it on.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PIS_SECRET_KEY` | insecure dev key | Signs login tokens. **Set a long random value in production.** |
| `PIS_DATABASE_URL` | `sqlite:///./data/pis.db` | Any SQLAlchemy URL, e.g. PostgreSQL for production. |
| `PIS_UPLOAD_DIR` | `./data/uploads` | Where original reports are stored. |
| `PIS_DEFAULT_CURRENCY` | `GHS` | Used when a report states no currency. |
| `PIS_ADMIN_EMAIL` / `PIS_ADMIN_PASSWORD` | see `.env.example` | First admin account, created only when there are no users. |
| `PIS_TOKEN_MINUTES` | `480` | Session length. |
| `PIS_MAX_UPLOAD_MB` | `40` | Per-file upload limit. |
| `ANTHROPIC_API_KEY` | — | Enables Claude. |
| `PIS_CLAUDE_MODEL` | `claude-opus-5-5` | Claude model. |

Statistics only mix records in the same currency. The dominant currency in the filtered set is used, and the UI reports how many other-currency records were left out.

## Project layout

```
app/
  main.py              FastAPI app, startup (DB tables, first admin)
  config.py            settings from environment / .env
  models.py            User, Report, Record, ActivityLog
  auth.py              JWT auth, role checks, activity logging
  routers/             auth & users, reports (ingestion), records (search/CRUD), intelligence (analytics/exports)
  services/
    text_extract.py    PDF / DOCX / text / OCR text extraction
    llm.py             Claude extraction + commentary
    rules.py           offline rule-based extraction
    ingest.py          extraction pipeline with AI → rules fallback
    analytics.py       summaries, trends, cross-analysis, comparables, template commentary
    query.py           shared search filters
    export.py          Word / CSV evidence tables and commentary
  static/              single-page web UI (no build step; Chart.js bundled locally)
scripts/seed_demo.py   demo data
tests/                 pytest suite
```

## Tests

```bash
pytest -q
```

## Production notes

- Run behind HTTPS (e.g. nginx or Caddy) and set a strong `PIS_SECRET_KEY`.
- Use PostgreSQL for multi-user production (`PIS_DATABASE_URL=postgresql+psycopg://...`). Schema changes after go-live should go through migrations (Alembic).
- Back up the database and the upload directory together.
- Background extraction runs in-process. For heavy bulk ingestion, move it to a task queue (e.g. Celery or RQ).

## Phase 2 (from the PRD, not yet built)

Mobile site-inspection capture, predictive valuation and cost models, an optional client portal, and integration with external data sources.
