# SmartShelf AI: Architecture

## Overview

SmartShelf AI tracks cafe inventory **per batch** and reminds staff before stock expires. Staff upload supplier invoices (PDF/photo) and photograph packaging labels; the system reads them with OCR, rule-based parsers and an optional **local** language model, a person confirms the result, and confirmed expiry dates drive email reminders.

Everything is self-hosted with Docker Compose. No paid AI APIs are used and no document leaves the server.

```
Browser (React SPA) ──/api──▶ nginx ──▶ FastAPI ──▶ PostgreSQL
                                          │  ├─▶ Tesseract OCR / pypdfium2 / OpenCV
                                          │  ├─▶ Ollama (Qwen 2.5 1.5B, optional)
                                          │  └─▶ SMTP (Mailpit in development)
                                          └─ APScheduler + worker threads (same process)
```

## Components

| Component | Responsibility |
|---|---|
| **Frontend** (React 18, TypeScript, Vite) | Dashboard, invoice upload and review, packaging scan and review, inventory, batch history, notifications, settings. TanStack Query for server state, React Hook Form + Zod for forms, Tailwind + Radix (shadcn-style) components, Recharts. |
| **API** (FastAPI, Pydantic 2) | ~48 REST endpoints. Thin routes: validate, load tenant-owned rows, call a service, commit. |
| **Services** (Python) | Business logic: inventory ledger, invoice extraction, label parsing, date extraction, expiry status, notifications, processing state machines, storage, audit. |
| **Database** (PostgreSQL 16, SQLAlchemy 2, Alembic) | Businesses, users, sessions, products, suppliers, invoices and lines, scans, batches, stock movements, notifications, audit events. Check constraints enforce valid states and non-negative stock. |
| **OCR** | pypdfium2 reads a PDF's text layer when present; otherwise pages and photos go through Pillow clean-up and **Tesseract** (PaddleOCR optional). OpenCV reads EAN/UPC/QR barcodes. |
| **Local LLM** (Ollama + Qwen 2.5 1.5B) | Optional. Structures invoice text into fields and line items; suggests product name, brand and pack size for labels. Never supplies dates. |
| **Scheduler** (APScheduler) | Expiry checks, reminder scheduling and sending with retries, recovery of interrupted processing. PostgreSQL advisory locks make duplicate instances safe. |
| **Email** (SMTP + Jinja2) | HTML reminder emails. Mailpit catches them in development. |

## Data flow

### 1. Invoice → inventory

1. **Upload**: file type checked by magic bytes, stored privately under a random name, SHA-256 used to detect duplicate uploads. Processing runs in a background thread; the UI polls.
2. **Text**: PDF text layer via pypdfium2, else OCR (Tesseract with rotation detection).
3. **Two readers**: a deterministic rule parser and, if available, Qwen via Ollama (JSON-schema-constrained output, Pydantic validation, bounded retries).
4. **Grounding**: every LLM value must appear in the document text; invented header values are discarded, ungrounded numbers flagged.
5. **Selection**: the result with better completeness and arithmetic consistency (qty × price = total) wins; header gaps are filled from the other. Small models sometimes read "500 ml" as a quantity; the arithmetic check catches that.
6. **Normalisation (rules only)**: dates via the date parser, pack size from the product name ("Milk 500ml" → 500 ml), units canonicalised.
7. **Review**: staff edit lines, exclude non-stock lines, confirm.
8. **Confirm**: in one transaction, each accepted line becomes an inventory batch (quantity in pcs, pack size from the line, no expiry date because invoices don't print one) plus a "received" stock movement and audit event.

### 2. Packaging label → expiry date

1. Photo upload or phone camera capture.
2. Tesseract OCR + OpenCV barcode reading.
3. **Date extraction**: labelled dates (MFG/PKD/EXP/USE BY/BEST BEFORE) are parsed deterministically; ambiguous day/month orders and unlabelled dates are flagged, never guessed.
4. Optional Qwen hints for product name/brand/pack size, marked as suggestions unless found in the text.
5. Review: staff pick the correct reading, tick "I compared the expiry date with the package", and save. The scan either creates a batch or confirms the date of an existing one.

### 3. Expiry → reminders

1. Status is computed in one place (Python and an equivalent SQL CASE): depleted, no date, requires confirmation, expired, expiring today, expiring soon, fresh, in the business's timezone.
2. Only **confirmed** dates schedule reminders (default 7/3/1/0 days before expiry; configurable).
3. Notifications are idempotent: unique on (batch, recipient, kind, offset, expiry date), inserted with `ON CONFLICT DO NOTHING`. Dispatch uses `SKIP LOCKED`, backoff retries and a send-hour window. Changing a date or depleting a batch cancels pending reminders.

## Technology stack

- **Frontend**: React 18, TypeScript 5, Vite 6, Tailwind CSS 3, Radix UI, TanStack Query 5, React Router 6, React Hook Form, Zod, Recharts, Vitest 3 + Testing Library.
- **Backend**: Python 3.12, FastAPI, Uvicorn, Pydantic 2, SQLAlchemy 2 (psycopg 3), Alembic, APScheduler, argon2-cffi, Jinja2, pytest, Ruff.
- **Document AI**: Tesseract (pytesseract), pypdfium2, OpenCV, Pillow, Ollama with `qwen2.5:1.5b` (optional vision model supported, off by default).
- **Infrastructure**: PostgreSQL 16, nginx, Mailpit, Docker Compose (Ollama under the `ai` profile).

## APIs and integrations

- **REST API** under `/api`, documented automatically at `/docs` (OpenAPI). Main groups: auth, settings, products, inventory batches and stock adjustments, invoices (upload, review, confirm, add-to-inventory, reprocess, delete), scans (upload, confirm, reprocess, delete), notifications (list, retry, run check), dashboard, system status.
- **Ollama** `/api/chat` with a JSON schema in `format`; timeouts and graceful fallback when the server or model is missing.
- **SMTP** with STARTTLS/SSL options; credentials only from environment variables.

## Security and data integrity

- **Auth**: Argon2id password hashes; opaque random session tokens stored as SHA-256 hashes; HttpOnly cookie (or Bearer); CSRF protection by requiring `X-Requested-With` on state-changing cookie requests; rate limits on login, registration and uploads.
- **Tenant isolation** in the backend: every query is scoped to the caller's business; foreign IDs return 404. Covered by an isolation test.
- **Stock ledger**: all quantity changes go through two service functions that lock the batch row, refuse negative stock and append a movement and an audit event in the same transaction. Deleting an invoice or scan zeroes its remaining stock with a recorded correction instead of deleting batches.
- **Untrusted documents**: document text is delimited in prompts, model output is validated and never executed, full document text is not logged.

## Key technical decisions

1. **Dates are deterministic.** Expiry dates come only from the rule-based date parser; a model may transcribe text but never supply a date value. A wrong expiry date is a food-safety issue.
2. **Human confirmation gates side effects.** Only confirmed dates send reminders; only confirmed invoices/scans create stock.
3. **Rules always run, the LLM is optional.** The app works on modest hardware without a model; when present, the LLM must beat the rules on completeness and arithmetic.
4. **Batch-level inventory with an append-only movement ledger**, so "which packets expire Friday?" and "who changed this?" are answerable.
5. **Quantities count packs; pack size is separate** (20 pcs × 500 ml).
6. **Server-side sessions** instead of JWTs, for real logout and no readable token in the browser.
7. **String enums + CHECK constraints** instead of PostgreSQL enums, for simpler migrations.
8. **In-process background work** (thread pool + APScheduler + advisory locks) instead of Redis/Celery, to keep a single-cafe deployment simple; `python -m app.workers` can run a dedicated worker.
9. **Day-first dates everywhere** (dd/mm/yyyy) independent of browser locale, with ambiguous readings spelled out ("4 May 2027").

## Quality

110 backend test cases (pytest against a real PostgreSQL test database, LLM and SMTP faked), 15 frontend workflow tests (Vitest), Ruff, TypeScript, ESLint and `alembic check` on every change, plus browser checks of the running Docker stack.
