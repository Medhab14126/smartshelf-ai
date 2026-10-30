# SmartShelf AI

Inventory and expiry tracking for cafes, bakeries and small food businesses, using self-hosted AI only.

Staff upload a supplier invoice. SmartShelf reads the supplier, line items, quantities and prices. Staff photograph product packaging, and SmartShelf reads manufacturing, expiry and best-before dates, batch numbers and barcodes. Every extracted value is reviewed by a person before it is used. A background job then watches the **confirmed** expiry dates of each inventory batch and emails the team before products expire.

All AI runs on your own machine (Tesseract or PaddleOCR, plus an optional local LLM via Ollama). No invoice, photo or business data is sent to an external AI service. No paid API key is required, and the app stays fully usable for manual entry when no model is installed.

**Project documents:** [CLAUDE.md](CLAUDE.md) (codebase guide and invariants) · [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (architecture) · [docs/DECISIONS.md](docs/DECISIONS.md) (design decisions)

---

## Contents

1. [Main features](#main-features)
2. [Architecture](#architecture)
3. [Technology stack](#technology-stack)
4. [Prerequisites](#prerequisites)
5. [Installation & start (Docker)](#installation--start-docker)
6. [Environment variables](#environment-variables)
7. [Database migrations](#database-migrations)
8. [Local LLM setup (optional)](#local-llm-setup-optional)
9. [Local development without Docker](#local-development-without-docker)
10. [Using the app](#using-the-app)
11. [Testing email with Mailpit](#testing-email-with-mailpit)
12. [Running the tests](#running-the-tests)
13. [API overview](#api-overview)
14. [Replacing the OCR engine or LLM](#replacing-the-ocr-engine-or-llm)
15. [Scaling beyond one instance](#scaling-beyond-one-instance)
16. [Known limitations](#known-limitations)
17. [Troubleshooting](#troubleshooting)
18. [Model and dependency licences](#model-and-dependency-licences)

---

## Main features

| Area | What it does |
|---|---|
| **Accounts & workspaces** | Registration creates a user and a business workspace (name, timezone, currency, date order). Passwords are hashed with Argon2. Sessions are server-side and revocable, using an HttpOnly cookie for the browser or a Bearer token for API clients. Tenant isolation is enforced in every backend query. |
| **Invoice scanner** | Accepts PDF/JPEG/PNG uploads. Files are validated by magic bytes, size and page count, and duplicate files are detected. Digital PDFs use their text layer; scanned PDFs and photos go through OCR. Extraction uses the local LLM with a JSON schema when available, plus a deterministic rule-based parser that always runs and serves as the fallback. LLM output is **grounded**: values not found in the document text are discarded. Arithmetic checks compare qty × price with the line total, and subtotal + tax with the total. The app also warns about ambiguous dates, duplicate invoice numbers and near-identical product names. A review screen lets staff edit, exclude or add lines before confirming. |
| **Packaging scanner** | Takes a photo from the phone camera or an upload. OCR text, barcode (EAN/UPC/QR via OpenCV) and **deterministic** date parsing feed a review form. MFG, packing, expiry/use-by and best-before dates are told apart by their printed labels. Unlabelled dates are never assumed to be the expiry date. Day/month ambiguity (03/04/2026), month-only dates, OCR digit confusion and "best before 6 months from manufacture" are detected and flagged. The user must tick an explicit confirmation before an expiry date can trigger reminders. |
| **Inventory** | Inventory is tracked per batch, so one product can have many batches with different expiry dates. Batches can be added manually, from invoice lines (one line can be split into several batches) or from scans. Stock changes (consumed, sold, discarded, returned, received, counted correction) go through a row-locked ledger that refuses negative stock. Each batch has its own movement history, and important changes are written to an audit log. The inventory list supports search, filters (status, category, supplier, expiring within N days), sorting and pagination. |
| **Expiry engine** | Statuses are calculated centrally in Python and SQL, in the business's timezone: depleted → no date → requires confirmation → expired → expiring today → expiring soon → fresh. Reminder intervals are configurable (default 7/3/1/0 days), with an optional one-time "expired but still in stock" alert. |
| **Email notifications** | Real SMTP delivery: Mailpit locally, any SMTP server in production. Emails use HTML + text templates. Idempotency comes from a unique key per batch, recipient, interval and expiry date. Statuses are pending, sent, failed and cancelled. Retries use bounded exponential backoff. Failed notifications can be retried manually, and a sent one can be resent deliberately. Reminders are cancelled automatically when a batch is depleted or its date changes. |
| **Dashboard** | Summary cards, an upcoming-expiry list with filters (today / 3 days / 7 days / expired / missing date / needs verification), a status chart, and recent batches, invoices, stock changes and notifications. All numbers come from the database. Empty workspaces get an onboarding screen. |

---

## Architecture

```mermaid
flowchart LR
    subgraph Browser
        UI[React + Vite SPA]
    end
    subgraph Docker Compose
        NGINX[nginx<br/>static SPA + /api proxy]
        API[FastAPI backend<br/>REST API]
        SCHED[APScheduler jobs<br/>expiry check · email dispatch · recovery]
        WORK[Processing thread pool<br/>OCR → parse → validate]
        DB[(PostgreSQL)]
        FILES[(Private upload volume)]
        MAIL[Mailpit / SMTP]
        OLLAMA[Ollama<br/>optional local LLM]
    end
    UI -- HTTPS same origin --> NGINX --> API
    API --> DB
    API --> FILES
    API -- submit job --> WORK
    WORK --> FILES
    WORK -- OCR: Tesseract / PaddleOCR --> WORK
    WORK -- JSON-schema prompt --> OLLAMA
    WORK --> DB
    SCHED --> DB
    SCHED -- SMTP --> MAIL
```

### Flow from invoice upload to expiry notification

```mermaid
sequenceDiagram
    actor Staff
    participant API as FastAPI
    participant Worker as Processing worker
    participant DB as PostgreSQL
    participant Sched as Scheduler
    participant SMTP as Mailpit / SMTP

    Staff->>API: POST /api/invoices/upload (PDF/photo)
    API->>DB: invoice (status=uploaded), file stored privately
    API-->>Staff: 201 + invoice id (UI polls)
    API->>Worker: process_invoice(id)
    Worker->>DB: claim (uploaded → processing)
    Worker->>Worker: PDF text layer or OCR → rule parser (+ local LLM) → grounding → validation
    Worker->>DB: header, lines, warnings, raw text (status=awaiting_review)
    Staff->>API: edit / exclude lines, POST /confirm
    API->>DB: one batch per accepted line + "received" movements + audit (same transaction)
    Staff->>API: POST /api/scans/product (package photo) → review → confirm expiry
    API->>DB: batch.expiry_date_status = confirmed
    loop every EXPIRY_CHECK_INTERVAL_MINUTES
        Sched->>DB: confirmed, in-stock batches at a reminder interval
        Sched->>DB: INSERT notification ON CONFLICT DO NOTHING
        Sched->>SMTP: send pending emails (bounded retries)
        Sched->>DB: status sent / failed
    end
```

### Backend layout

```
backend/
  app/
    main.py                     FastAPI app, error handlers, scheduler start-up
    core/                       config, security (argon2, tokens), logging, rate limiting, errors
    api/deps.py                 auth context (tenant), pagination
    api/routes/                 auth, dashboard, invoices, inventory, scans, notifications, settings, system
    models/                     SQLAlchemy models (UUID PKs, FKs, check/unique constraints)
    schemas/                    Pydantic request/response + AI extraction schemas
    services/
      ocr_service.py            pluggable OCR engines + PDF text layer + image preparation
      invoice_extraction_service.py  rule parser, LLM extraction, grounding, validation
      product_label_service.py  packaging fields + optional LLM/VLM hints
      date_extraction_service.py     deterministic date parsing & label classification
      barcode_service.py        OpenCV EAN/UPC/QR + GTIN checksum
      inventory_service.py      products, batches, stock ledger
      expiry_status.py          central status rules (Python + SQL)
      notification_service.py   reminder scheduling, rendering, dispatch, retry
      email_service.py          SMTP transport
      processing_service.py     job runner + invoice/scan state machines + recovery
    ai/                         Ollama client, prompts, output validation & grounding
    workers/                    APScheduler jobs (`python -m app.workers` = standalone worker)
    templates/emails/           HTML + text email templates
  alembic/                      migrations
  tests/                        pytest suite + synthetic fixtures
frontend/                       React + TS + Vite + Tailwind (shadcn-style components)
samples/                        synthetic invoices and packaging photos for demos
```

---

## Technology stack

- **Frontend:** React 18, TypeScript, Vite 6, Tailwind CSS 3, shadcn-style components built on Radix UI, React Router 6, TanStack Query 5, React Hook Form + Zod, Recharts, lucide-react, sonner (toasts). Tests use Vitest + Testing Library.
- **Backend:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 16 (psycopg 3), APScheduler 3, argon2-cffi, Jinja2. Tests use pytest; ruff handles linting.
- **AI / documents:** Tesseract 4/5 (default OCR) or PaddleOCR (optional), pypdfium2 (PDF text layer & rendering), Pillow + OpenCV (image prep, barcodes), and Ollama with Qwen2.5 (optional LLM and optional vision model).
- **Infrastructure:** Docker Compose, nginx, Mailpit.

---

## Prerequisites

- Docker 24+ with Docker Compose v2.
- About 2 GB of disk for images. Add 1–5 GB if you use a local LLM.
- RAM: about 1 GB for the base stack. A local LLM needs an extra 1.5 GB (`qwen2.5:1.5b`) to 6 GB (`qwen2.5:7b`). No GPU is required.

For development without Docker: Python 3.12, Node 20.19+, PostgreSQL 16, and the Tesseract binary (`apt install tesseract-ocr`).

---

## Installation & start (Docker)

```bash
git clone <this repository> smartshelf-ai && cd smartshelf-ai
cp .env.example .env            # then edit POSTGRES_PASSWORD (and DATABASE_URL to match)
docker compose up -d --build
```

Compose waits for PostgreSQL to be healthy. The backend applies migrations (`alembic upgrade head`) before it starts, and the frontend waits for a healthy backend.

| URL | What |
|---|---|
| http://localhost:8080 | **SmartShelf AI web app**. Register a workspace on first visit. |
| http://localhost:8000/docs | **FastAPI interactive docs** (OpenAPI). Use *Authorize* with the `access_token` from `/api/auth/login`. |
| http://localhost:8000/redoc | ReDoc API reference |
| http://localhost:8025 | **Mailpit** inbox (all emails sent locally land here) |

Stop with `docker compose down`. Add `-v` to also delete the database, uploads and model volumes.

> **No demo data is seeded.** To try the pipeline, upload the synthetic files in [`samples/`](samples/): `invoice_dairy.pdf` (digital PDF), `invoice_bakery.png` (photo → OCR), `invoice_multipage.pdf` (2 pages, 30 lines), and packaging labels `label_milk.png` (with EAN-13 barcode), `label_ambiguous.png` (day/month ambiguity) and `label_bestbefore.png`. Regenerate them with `python tests/fixtures/generate_fixtures.py` inside `backend/`.

---

## Environment variables

All configuration comes from environment variables. See [`.env.example`](.env.example), which uses placeholders only. Never commit `.env`. Inside Docker Compose, `DATABASE_URL`, `SMTP_HOST`, `OLLAMA_BASE_URL` and `UPLOAD_DIR` are overridden to use the service names.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://…@localhost:5432/smartshelf` | SQLAlchemy URL |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | – | Used by the `db` container |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins (only needed when the SPA is served from another origin) |
| `FRONTEND_BASE_URL` | `http://localhost:5173` | Used for links in emails |
| `SESSION_TTL_HOURS` / `SESSION_COOKIE_SECURE` | `168` / `false` | Session lifetime; set the cookie to secure under HTTPS |
| `AUTH_RATE_LIMIT_ATTEMPTS` / `AUTH_RATE_LIMIT_WINDOW_SECONDS` | `10` / `300` | Login/registration throttling per IP + email |
| `UPLOAD_DIR`, `MAX_UPLOAD_MB`, `MAX_PDF_PAGES` | `./data/uploads`, `15`, `20` | Private storage and upload limits |
| `OCR_ENGINE` | `auto` | `auto` (PaddleOCR if installed, otherwise Tesseract), `tesseract` or `paddle` |
| `TESSERACT_LANG` / `TESSERACT_CMD` | `eng` / auto | Tesseract language pack and binary path |
| `LLM_ENABLED` | `true` | Set `false` to use rule-based extraction only |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama server |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | Text model for invoice extraction and label hints |
| `OLLAMA_VISION_MODEL` | *(empty)* | Optional vision-language model for packaging photos, e.g. `qwen2.5vl:7b` |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_ATTEMPTS` | `120` / `2` | Bounded LLM calls and retries |
| `RUN_SCHEDULER` | `true` | Run background jobs in this process |
| `EXPIRY_CHECK_INTERVAL_MINUTES` | `15` | How often reminders are scheduled |
| `NOTIFICATION_DISPATCH_INTERVAL_SECONDS` | `60` | How often pending/retrying emails are sent |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `SMTP_USE_SSL` | Mailpit | SMTP server. Empty `SMTP_HOST` means emails are recorded as *failed: SMTP not configured*. |
| `EMAIL_FROM`, `EMAIL_MAX_ATTEMPTS` | – / `5` | Sender address and bounded retry count |

Per-business settings live in the app's **Settings** page, not in env vars: business name, timezone, currency, numeric date order (DD/MM vs MM/DD), notification recipients, reminder intervals, expired-stock alerts and the earliest local sending hour.

---

## Database migrations

Migrations live in `backend/alembic/versions`. The Docker backend runs `alembic upgrade head` on every start. To run them manually:

```bash
docker compose exec backend alembic upgrade head        # inside Docker
cd backend && .venv/bin/alembic upgrade head            # local (uses DATABASE_URL)
.venv/bin/alembic revision --autogenerate -m "message"  # after changing models
.venv/bin/alembic check                                 # verify models and migrations agree
```

The test suite creates a fresh `smartshelf_test` database and migrates it from zero each run, so migrations are exercised against a clean database every time.

---

## Local LLM setup (optional)

SmartShelf works without any language model: invoices are then extracted by the deterministic parser, and dates are **always** parsed deterministically. A local LLM improves invoice extraction for unusual layouts and can suggest product names and brands.

Model weights are **never downloaded automatically**. Download them explicitly.

**Option A: Ollama in Docker Compose (`ai` profile):**

```bash
docker compose --profile ai up -d ollama
docker compose exec ollama ollama pull qwen2.5:1.5b      # ~1 GB
# to start Ollama with every `docker compose up`, set COMPOSE_PROFILES=ai in .env
# optional, needs ~6 GB RAM:  docker compose exec ollama ollama pull qwen2.5:7b
```

The backend already points to `http://ollama:11434` inside Compose. Set `OLLAMA_MODEL` in `.env` and run `docker compose up -d backend` to switch models.

**Option B: Ollama installed on the host** (https://ollama.com/download):

```bash
ollama pull qwen2.5:1.5b
# in .env: DOCKER_OLLAMA_BASE_URL=http://host.docker.internal:11434  (Docker backend)
#      or: OLLAMA_BASE_URL=http://localhost:11434                    (local backend)
```

Check **Settings → AI & services status** in the app, or `GET /api/system/ai-status`. Existing invoices can be re-read with **Read again** after installing a model.

| Model | Size | RAM | Licence | Notes |
|---|---|---|---|---|
| `qwen2.5:1.5b` (default) | ~1 GB | ~2 GB | Apache-2.0 | Runs on modest CPUs; good for clean invoices |
| `qwen2.5:7b` | ~4.7 GB | ~6–8 GB | Apache-2.0 | Noticeably better on messy layouts; slower on CPU |
| `qwen3:1.7b` / `qwen3:8b` | 1.4 / 5 GB | – | Apache-2.0 | Alternative; reasoning mode may slow responses |
| `qwen2.5vl:7b` (vision, optional) | ~6 GB | ~8 GB+ | Apache-2.0 | Set as `OLLAMA_VISION_MODEL` for photo understanding |

> **Licence caution:** `qwen2.5:3b` and `qwen2.5vl:3b` are released under the *Qwen Research License* (non-commercial), **not** Apache-2.0. Avoid them for commercial use. Always check the model card of the exact model you pull, since weights, code and dependencies can carry different licences.

**Safety design:** document text is passed to the model inside delimiters and treated as untrusted data. Output is constrained by a JSON schema, validated with Pydantic, and retried at most `LLM_MAX_ATTEMPTS` times, with the validation error given as feedback. It is then cross-checked against the OCR text, and invented values are discarded. Model output is never executed. Dates from a vision model are re-parsed deterministically and always marked uncertain.

---

## Local development without Docker

```bash
# services only
docker compose up -d db mailpit

# backend (Python 3.12 with ssl support)
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000        # reads ../.env

# frontend (separate terminal)
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies /api to :8000
```

Optional PaddleOCR: `pip install paddlepaddle==3.0.0 paddleocr==3.0.0`, then set `OCR_ENGINE=paddle`. For Docker, build with `INSTALL_PADDLEOCR=true docker compose build backend`. PaddleOCR downloads its own detection/recognition weights on first use.

---

## Using the app

1. **Register**: this creates your workspace. Your email is the first reminder recipient.
2. **Upload Invoice**: drop a PDF or photo. The review page polls while the invoice is processed, and you can leave and come back. Check the warnings, edit or exclude lines, then **Confirm invoice**.
3. Confirming adds every accepted line to inventory as one batch, under a catalogue product with the same name (created if needed). Invoices don't carry expiry dates, so these batches show *No expiry date* until you scan the package or edit the batch. Quantities count packs or pieces; the size of each pack (e.g. 500 ml, read from "Toned Milk 500ml") is shown as "20 pcs × 500 ml" and can be edited. A line missing its quantity isn't guessed; it keeps an **Add to inventory** button, where you can also split it into batches. Dates are always shown and entered as dd/mm/yyyy. **Delete** (on the invoice page or the list) removes an upload and its file. For a confirmed invoice it also takes whatever stock from it is still in inventory back out, as a recorded stock correction.
4. **Scan Product**: photograph the date area of a package, from a batch page (to fill in that batch's date) or standalone (to create a new batch). Pick the right reading for ambiguous dates, tick *I compared the date with the package*, then **Save and confirm date**. *Save without confirming* stores the date but never triggers reminders.
5. **Inventory**: record consumed, sold, discarded or returned stock and counted corrections. Every change appears in the batch's stock history with who did it and when. Expired batches stay visible until stock is adjusted.
6. **Notifications**: see sent, pending and failed reminders, retry failures, or run the check immediately.

---

## Testing email with Mailpit

1. Make sure a recipient is set in **Settings → Expiry reminders**, and the earliest send hour has passed in your business timezone.
2. Add a batch with a confirmed expiry date 0–7 days from today (or upload `samples/invoice_dairy.pdf` and add a line with such a date).
3. Wait for the scheduler (`EXPIRY_CHECK_INTERVAL_MINUTES`, which also runs about 10 s after start-up) or click **Notifications → Check for due reminders now**.
4. Open http://localhost:8025. You'll see e.g. *"SmartShelf AI — Toned Milk 500ml expires in 3 days"*.

To simulate a failure, stop Mailpit (`docker compose stop mailpit`). Notifications back off and retry, then become *failed*. Start Mailpit again and click **Retry**.

---

## Running the tests

```bash
# backend: needs the PostgreSQL service (docker compose up -d db) and Tesseract for OCR tests
cd backend
.venv/bin/pytest                       # 101 tests; creates/migrates smartshelf_test
.venv/bin/ruff check . && .venv/bin/ruff format --check .
# custom DB: TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/smartshelf_test .venv/bin/pytest

# frontend
cd frontend
npm test                               # Vitest workflow tests
npm run typecheck && npm run lint && npm run build
```

The LLM and SMTP transport are mocked in automated tests. No external service is contacted. OCR tests on image fixtures are skipped automatically if Tesseract is not installed. The backend test suite covers:

- Date parsing and ambiguity.
- Expiry statuses and reminder offsets.
- Invoice schema, parsing, LLM retry and grounding.
- Label parsing and barcode checksums.
- Authentication, CSRF and rate limiting.
- Business data isolation.
- Invoice upload → review → confirm → split into batches.
- Duplicate and invalid uploads, and the manual fallback for unreadable documents.
- Scans and expiry confirmation.
- Stock movements and the negative-stock guard.
- Notification deduplication, intervals, send hour, backoff, failures, retry and cancellation.

---

## API overview

Full, interactive documentation is at `/docs`. All list endpoints accept `page` and `page_size` (max 100). All IDs are checked against the caller's business, and other tenants' IDs return 404.

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |
| Dashboard | `GET /api/dashboard/summary`, `GET /api/dashboard/upcoming-expiry?window=upcoming\|today\|3d\|7d\|expired\|missing\|unconfirmed` |
| Invoices | `POST /api/invoices/upload`, `GET /api/invoices`, `GET /api/invoices/{id}`, `GET /api/invoices/{id}/file`, `PATCH /api/invoices/{id}`, `GET /api/invoices/{id}/items`, `POST /api/invoices/{id}/items`, `PATCH /api/invoices/{id}/items/{item_id}`, `POST /api/invoices/{id}/confirm`, `POST /api/invoices/{id}/reject`, `POST /api/invoices/{id}/reprocess`, `DELETE /api/invoices/{id}`, `POST /api/invoices/{id}/items/{item_id}/add-to-inventory` |
| Products | `POST /api/products`, `GET /api/products`, `GET /api/products/{id}`, `PATCH /api/products/{id}`, `GET /api/suppliers`, `GET /api/categories` |
| Inventory | `GET /api/inventory/batches`, `POST /api/inventory/batches`, `GET /api/inventory/batches/{id}`, `PATCH /api/inventory/batches/{id}`, `POST /api/inventory/batches/{id}/adjust-stock`, `GET /api/inventory/batches/{id}/movements`, `GET /api/inventory/movements` |
| Scans | `POST /api/scans/product`, `GET /api/scans`, `GET /api/scans/{id}`, `GET /api/scans/{id}/image`, `POST /api/scans/{id}/confirm`, `POST /api/scans/{id}/reprocess`, `DELETE /api/scans/{id}` |
| Notifications | `GET /api/notifications`, `POST /api/notifications/{id}/retry[?force=true]`, `POST /api/notifications/run-check` |
| Settings | `GET/PUT /api/settings/business`, `GET/PUT /api/settings/notifications` |
| System | `GET /api/health`, `GET /api/system/ai-status` |

Browser clients authenticate with the HttpOnly session cookie and must send `X-Requested-With` on state-changing requests (CSRF protection). Script clients can use `Authorization: Bearer <access_token>`.

---

## Replacing the OCR engine or LLM

- **OCR:** implement `OcrEngine.recognize_words()` in `app/services/ocr_service.py`, returning word boxes, and register it in `_build_engine()`. Rows, column gaps, the PDF text layer and image preparation are shared, so no other code changes. `OCR_ENGINE` selects the engine.
- **LLM:** `app/ai/llm_client.py` is the only module that talks to Ollama (`chat_json(model, system, user, schema, images)`). To use another self-hosted runtime (llama.cpp server, vLLM, LocalAI), implement the same method and return it from `get_llm_client()`. Prompts are in `app/ai/prompts/`, and the schemas they must satisfy are in `app/schemas/extraction.py`. Changing `OLLAMA_MODEL` is enough to try a different Ollama model.
- **Date logic** lives only in `app/services/date_extraction_service.py` and never depends on a model.

---

## Scaling beyond one instance

This MVP is designed for a single backend instance:

- Background jobs run in-process (APScheduler). Each job takes a PostgreSQL advisory lock, so accidentally running two instances does not double-send. Notification sending also uses `SELECT … FOR UPDATE SKIP LOCKED` and a unique idempotency key.
- Document processing uses an in-process thread pool. Records are claimed atomically, so a document is never processed twice. A sweeper re-queues uploads interrupted by a restart, up to `PROCESSING_MAX_ATTEMPTS`.
- Rate limiting is in memory.

For several API instances:

1. Set `RUN_SCHEDULER=false` on API instances and run one worker with `python -m app.workers` (same image).
2. Move document processing to a queue such as Redis + RQ, Celery or Dramatiq, so a dedicated OCR/LLM worker pool can scale independently.
3. Move rate limiting to Redis or the reverse proxy.
4. Put uploads on shared storage, such as an S3-compatible bucket like MinIO, instead of a local volume.

---

## Known limitations

- **OCR quality depends on the photo.** Tesseract struggles with curved, glossy or low-contrast packaging and dot-matrix printed dates. PaddleOCR is usually better on packaging but is heavier. Everything is reviewable, and unreadable uploads fall back to manual entry.
- **The rule-based invoice parser** handles common tabular layouts (description + qty [+ unit] + price + total, with optional codes, HSN and tax columns). Unusual layouts, such as multi-line descriptions or rotated tables, may need manual correction or the local LLM.
- **Small LLMs make mistakes.** Grounding removes invented values, but a model can still mis-assign a real number to the wrong column. The review step exists for this reason.
- Barcode lookup is local only: barcodes match products you have already saved. There is no external product database.
- Batch numbers, product names and brands from labels are always suggestions marked "please check".
- One business per user. There is no invite flow for extra staff yet, although the membership model with roles exists.
- No password reset or email verification flow yet.
- PDF preview relies on the browser's built-in PDF viewer, with an "Open PDF" fallback.
- HEIC photos (the iPhone default in some settings) are not accepted. Browsers usually convert camera captures to JPEG.
- Uploaded files are kept indefinitely. There is no retention policy yet.
- The first page load shows a 401 for `/api/auth/me` in the browser console when logged out. This is the expected session probe.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `POSTGRES_PASSWORD` error on `docker compose up` | Copy `.env.example` to `.env` and set it. |
| Backend restarts with "password authentication failed" | The DB volume was created with a different password. Change it back, or `docker compose down -v` (deletes data). |
| Invoice stuck on "Reading your invoice…" | Check `docker compose logs backend`. Stuck jobs are re-queued after `PROCESSING_STALE_MINUTES`. Use **Read again**. |
| "OCR is not available on the server" | Install Tesseract (`apt install tesseract-ocr`) or set `OCR_ENGINE`; the Docker image includes it. |
| "AI not used: … not reachable" warning | Ollama isn't running or the model isn't pulled. This is optional; see [Local LLM setup](#local-llm-setup-optional). |
| LLM extraction times out on CPU | Use a smaller model (`qwen2.5:1.5b`) or raise `LLM_TIMEOUT_SECONDS`. |
| No reminder emails | Check that a recipient is set, the date is **confirmed**, the batch is in stock, the date is within an interval, and the local time is past the send hour. Look at **Notifications** for failure reasons and check Mailpit at :8025. |
| Dates read with day and month swapped | Set **Settings → Numeric date order**. Ambiguous dates are flagged regardless. |
| `pip` SSL errors locally | Use a Python build with the `ssl` module (e.g. the distribution's `python3.12`). |
| Frontend dev server can't reach API | Start the backend on :8000, or set `VITE_API_PROXY_TARGET`. |

---

## Model and dependency licences

| Component | Licence |
|---|---|
| Tesseract OCR, pytesseract | Apache-2.0 |
| PaddleOCR, PaddlePaddle (optional) | Apache-2.0 |
| OpenCV (opencv-python-headless) | Apache-2.0 (wheels bundle third-party libs, e.g. FFmpeg under LGPL) |
| pypdfium2 / PDFium | Apache-2.0 or BSD-3-Clause / BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| NumPy | BSD-3-Clause |
| Ollama | MIT |
| Qwen2.5 1.5B / 7B Instruct, Qwen2.5-VL 7B, Qwen3 models | Apache-2.0 (check the model card) |
| Qwen2.5 3B, Qwen2.5-VL 3B | Qwen Research License, **non-commercial** |
| FastAPI, Pydantic, SQLAlchemy, Alembic, APScheduler, argon2-cffi | MIT |
| psycopg 3 | LGPL-3.0 (dynamically linked library use) |
| httpx, Jinja2, python-dateutil | BSD-3-Clause / BSD-3-Clause / Apache-2.0 + BSD-3-Clause |
| PostgreSQL | PostgreSQL License |
| Mailpit | MIT |
| nginx | BSD-2-Clause |
| React, React Router, TanStack Query, React Hook Form, Zod, Recharts, Radix UI, Tailwind CSS, Vite, sonner | MIT |
| lucide-react | ISC |

Licences listed for convenience. Verify them for your own distribution, especially for model weights.
