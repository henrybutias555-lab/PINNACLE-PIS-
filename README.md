# Pinnacle Intelligence System (PIS)

An AI-powered internal platform for **Pinnacle Estate Surveyors (PES)**. It extracts, stores, organises and analyses data from valuation, construction cost and housing reports. The aim is faster, more consistent, data-driven valuation and cost advisory work.

Built from the PRD (v1.0). This repository holds a working **Phase 1 MVP**.

## Features (mapped to the PRD)

| PRD section | What is implemented |
|---|---|
| **4.1 Report ingestion** | Single or bulk upload of PDF, Word (.docx), text and scanned images. Extraction runs in the background. Claude reads the document (including scanned PDFs and images) and returns structured data. Nothing is saved until a surveyor **reviews, edits and confirms** the draft. Reports can be re-run, rejected or deleted. |
| **4.2 Data organisation & search** | Structured storage for valuation, cost and housing records: values, rents, yields, rates per m², elemental cost breakdowns, comparables, housing indicators. Search and filter by free text, location, city, property type, date range, value/cost range, report type, purpose and tag, with sorting and paging. |
| **4.3 Market & cost intelligence** | Location-based summaries (mean, median, range, count), averages by area × property type, monthly/quarterly/yearly trend charts, a comparable-evidence finder ranked by similarity, **cross-analysis** of construction cost/m² against capital value/m², and **suggested market commentary** (Claude-drafted, with a template fallback). |
| **4.4 Report support tools** | One-click comparable / cost / housing evidence tables in **Word** (firm-branded), **Excel** or **CSV**. The full market & cost analysis (summary, area breakdown, trends with charts, cost vs value, records) exports to **Excel**. Commentary exports to Word or text. |
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
pip install -r requirements-dev.txt
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

## Deploying on Render (recommended)

1. In Render choose **New + → Blueprint**, connect GitHub and pick this repository. Render reads `render.yaml`.
2. When asked, fill in:
   - `DATABASE_URL`: the Supabase **Transaction pooler** connection string
   - `PIS_ADMIN_EMAIL` and `PIS_ADMIN_PASSWORD`: the first admin login

   `PIS_SECRET_KEY` is generated automatically.
3. Click **Apply**. Render builds and deploys, and every push to `main` redeploys automatically.

The free plan sleeps after 15 minutes without traffic, so the first visit after that takes about a minute. Add `ANTHROPIC_API_KEY` under **Environment** to enable Claude.

## Deploying on Vercel

The repository is ready for Vercel: `api/index.py` is the serverless entry point and `vercel.json` sends every route to it.

1. **Import the repo:** in Vercel choose *Add New → Project*, import this repository, and leave *Framework Preset* as **Other**. Set **Settings → Git → Production Branch** to `main`.
2. **Add a database:** open *Storage → Create Database → Neon (Postgres)* and connect it to the project. This sets `DATABASE_URL` automatically. A database is required, because Vercel has no permanent disk.
3. **Set environment variables:** under *Settings → Environment Variables* (Production), add:
   - `PIS_SECRET_KEY`: a random string of 40+ characters (e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
   - `PIS_ADMIN_EMAIL` and `PIS_ADMIN_PASSWORD`: the first admin login
   - `ANTHROPIC_API_KEY` (optional): enables Claude extraction and commentary
4. **Redeploy:** go to *Deployments → ⋯ → Redeploy*, open the site and sign in.

**Using Supabase instead of Neon:**
1. In the Supabase project click **Connect**, choose the **Transaction pooler** connection string (port `6543`, host `…pooler.supabase.com`) and copy the URI.
2. Replace `[YOUR-PASSWORD]` with the database password. Characters such as `@ # / ?` in the password must be URL-encoded.
3. Add the result to Vercel as `DATABASE_URL`.

Don't use the "Direct connection" string: it is IPv6-only and Vercel cannot reach it. The app turns off prepared statements so it works through the pooler.

Until steps 2–3 are done the site shows a "setup required" page listing what is missing, rather than running insecurely.

Limits on Vercel:
- Each uploaded file can be at most about 4.4 MB (Vercel's request size limit). Split or compress larger PDFs.
- Extraction runs during the upload request, with a 300-second time limit.
- Originals are stored in the database.

## AI configuration

Set one API key to turn on AI extraction and commentary:

| Provider | Key | Model setting (default) |
|---|---|---|
| Google Gemini | `GEMINI_API_KEY` (from Google AI Studio) | `PIS_GEMINI_MODEL` (`gemini-2.5-flash`) |
| Anthropic Claude | `ANTHROPIC_API_KEY` | `PIS_CLAUDE_MODEL` (`claude-opus-5-5`) |

If both keys are set, Gemini is used. Set `PIS_AI_PROVIDER=claude` (or `gemini`) to choose explicitly.

- **Extraction:** the PDF (or image, or the document's text) goes to the AI, which returns JSON matching a fixed schema. Values not stated in the report are left blank; the prompt forbids estimating figures. Location, city and region are separated (e.g. East Legon Hills / Accra / Greater Accra), and houses on a plot are classed as residential with tags such as "mansion" and "5-bedroom".
- **Commentary:** the AI writes prose only from the statistics the system computed, and the surveyor edits it before use.
- **Without a key,** or if an AI call fails, the system uses rule-based extraction and template commentary. The rules know Ghanaian cities and neighbourhoods (`app/services/places.py`). The UI shows which method produced each draft.

Without a key, scanned documents need OCR. Install Tesseract plus `pip install pytesseract pdf2image` and it is used automatically.

> Uploaded reports are sent to the chosen AI provider when AI is enabled. Confirm this fits the firm's data-handling policy and client confidentiality terms before turning it on. On Gemini's free tier, Google may use submitted content to improve its products; use a paid (billing-enabled) key for client documents.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PIS_SECRET_KEY` | insecure dev key | Signs login tokens. **Set a long random value in production.** |
| `PIS_DATABASE_URL` / `DATABASE_URL` | `sqlite:///./data/pis.db` | Database URL. `postgres://` URLs (Neon, Vercel) are accepted as-is. |
| `PIS_DEFAULT_CURRENCY` | `GHS` | Used when a report states no currency. |
| `PIS_ADMIN_EMAIL` / `PIS_ADMIN_PASSWORD` | see `.env.example` | First admin account, created only when there are no users. |
| `PIS_TOKEN_MINUTES` | `480` | Session length. |
| `PIS_MAX_UPLOAD_MB` | `40` (`4.4` on Vercel) | Per-file upload limit. |
| `ANTHROPIC_API_KEY` | — | Enables Claude. |
| `PIS_CLAUDE_MODEL` | `claude-opus-5-5` | Claude model. |

Statistics only mix records in the same currency. The dominant currency in the filtered set is used, and the UI reports how many other-currency records were left out.

## Project layout

```
app/
  main.py              FastAPI app, startup (DB tables, first admin), deployment guard
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
api/index.py           Vercel serverless entry point (with vercel.json)
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
- Back up the database regularly (it also holds the original uploaded reports).
- Locally, extraction runs in the background in-process; on Vercel it runs during the upload request. For heavy bulk ingestion, move it to a task queue (e.g. Celery or RQ).

## Phase 2 (from the PRD, not yet built)

Mobile site-inspection capture, predictive valuation and cost models, an optional client portal, and integration with external data sources.
