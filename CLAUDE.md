# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository. The README covers setup and features; this file covers how to change the code safely.

## Project in one paragraph

SmartShelf AI is batch-level inventory and expiry tracking for cafes. FastAPI + SQLAlchemy 2 + PostgreSQL backend (`backend/`), React + TypeScript + Vite frontend (`frontend/`). Invoices and packaging photos go through OCR (Tesseract/PaddleOCR), a deterministic parser and an optional local LLM (Ollama). A human confirms the results, and APScheduler emails reminders for confirmed expiry dates. Everything is self-hosted; no paid AI APIs.

## Commands

```bash
docker compose up -d db mailpit                 # services needed for dev and tests

# backend (run from backend/)
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/pytest                                # needs db container; creates smartshelf_test
.venv/bin/pytest tests/test_date_extraction.py -q
.venv/bin/ruff check . && .venv/bin/ruff format .
.venv/bin/alembic revision --autogenerate -m "msg" && .venv/bin/alembic upgrade head
.venv/bin/alembic check                         # models and migrations must agree

# frontend (run from frontend/)
npm run dev                                     # :5173, proxies /api to :8000
npm test && npm run typecheck && npm run lint && npm run build

# full stack
docker compose up -d --build                    # app :8080, API docs :8000/docs, Mailpit :8025
```

Run backend tests, ruff, frontend tests, typecheck and lint before calling a change done.

## Environment gotchas

- Create the venv with `/usr/bin/python3.12`. `/usr/local/bin/python3.12` on the dev machine has no `ssl` module, so pip fails.
- The tests drop and recreate `smartshelf_test` on every run (`tests/conftest.py`). Never point `TEST_DATABASE_URL` at a real database.
- Use Vitest 3.x. Vitest 2 bundles Vite 5 and breaks `vite.config.ts` types with Vite 6.
- Do not add `build.rollupOptions.output.manualChunks` for React/Recharts. It caused a runtime "Cannot set properties of undefined (setting 'Children')" error in the production build only. Code-split with `lazy()` routes in `App.tsx` instead.
- `with_for_update()` on `InventoryBatch` must use `of=Model`. Its joined-eager relationships produce an outer join, and PostgreSQL rejects `FOR UPDATE` on that. `get_owned()` already does this.
- email-validator rejects reserved domains such as `.test`; use `example.com` in tests.
- Use `DateInput` (`components/ui/date-input.tsx`), not `<input type="date">`: the native input follows the OS locale (mm/dd on en-US). Display dates with `formatDate()` (dd/mm/yyyy), or `formatDateLong()` where day and month could be swapped.
- Pass `required` to `Field` for mandatory inputs (red `*`, "(required)" for screen readers). Tests should find such inputs with `getByLabelText(/^Name/)`.
- Quantities count packs/pieces (`unit` is `pcs` for new batches). What a pack holds is `pack_size` + `pack_unit` (g/kg/ml/ltr, both or neither), entered with `PackSizeInput`. `lib/units.ts` mirrors `backend/app/services/units.py` (`parse_pack_size()`, `pack_from_line()`); change both together.
- Ollama runs under the Compose `ai` profile. `COMPOSE_PROFILES=ai` in `.env` starts it with the stack; without it the backend falls back to rules and says the AI server is unreachable.

## Architecture map

| Concern | Where |
|---|---|
| Settings (all env-driven) | `backend/app/core/config.py` |
| Auth context and tenant scoping | `backend/app/api/deps.py` (`Auth`), `inventory_service.get_owned()` |
| Expiry status rules (Python and SQL) | `backend/app/services/expiry_status.py`. **Only place** statuses are defined. |
| Date parsing and classification | `backend/app/services/date_extraction_service.py` |
| Invoice parsing, LLM, grounding, validation | `backend/app/services/invoice_extraction_service.py`, `backend/app/ai/` |
| Packaging labels | `backend/app/services/product_label_service.py`, `barcode_service.py` |
| OCR engines | `backend/app/services/ocr_service.py` |
| Stock ledger | `backend/app/services/inventory_service.py` |
| Background processing state machines | `backend/app/services/processing_service.py` |
| Reminder scheduling and email | `backend/app/services/notification_service.py`, `email_service.py`, `templates/emails/` |
| Scheduler jobs | `backend/app/workers/expiry_monitor.py` |
| API client, types | `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` (mirror backend schemas by hand) |
| UI primitives | `frontend/src/components/ui/` (shadcn-style, hand-written) |

## Invariants: do not break these

1. **Never invent data.** Missing values stay `null`. LLM output must pass `ground_extraction()`; never bypass it.
2. **Dates are deterministic.** Expiry, manufacturing and best-before dates come only from `date_extraction_service`. A model may transcribe date text, but never supply a date value.
3. **Never assume the earliest or latest date is the expiry.** Unlabelled dates stay `unknown` and are offered to the user.
4. **Only confirmed dates drive reminders.** `expiry_date_status == "confirmed"` is required in `schedule_reminders()`. A date read from a scan is confirmed only when the user sets `confirm_expiry=true`. A date typed by a user is confirmed by default.
5. **Tenant isolation lives in the backend.** Every query filters by `ctx.business_id`. Load rows by ID through `get_owned()`, which returns 404 for other tenants (no information leak). Add an isolation test for any new resource.
6. **All stock changes go through `create_batch()` / `adjust_stock()`.** They lock the row, refuse negative stock, append an `InventoryMovement` and an audit event in the same transaction. Never write `current_quantity` directly.
7. **Invoice lines are not inventory batches.** Batches are created from lines only when the user confirms the invoice (`receive_confirmed_invoice()`, one batch per accepted line, no expiry date), or via the manual add-to-inventory or scan-confirm flows. They keep `invoice_id` / `invoice_item_id`. Deleting an invoice or a scan that created a batch (`delete_invoice()`, `delete_scan()`) zeroes the remaining stock through `adjust_stock()` corrections; batches are never deleted. Lines missing a quantity are never guessed; they stay `accepted`. Batches are counted in `pcs` and take the line's pack size (`extracted` keeps the original unit).
8. **Notifications are idempotent.** The unique key is (batch, recipient, kind, offset, expiry_date), inserted with `ON CONFLICT DO NOTHING`. Changing a batch's date or depleting it cancels its pending notifications.
9. **Keep extracted and confirmed values.** `InvoiceItem.extracted`, `ProductScan.extraction` and `ProductScan.confirmed` are audit records; do not overwrite them on edit.
10. **Document text is untrusted.** Keep it inside prompt delimiters, never execute model output, and never log full document text or secrets (use `core.logging.redact`).
11. **No fake success.** The UI shows success toasts only in `onSuccess` of a real mutation. Never replace backend calls with browser-only state.

## Conventions

- Backend layering: routes stay thin (validation, `get_owned`, commit). Business logic goes in `services/`. Raise `core.errors` exceptions (`NotFoundError`, `ConflictError`, `ValidationFailed`), not raw `HTTPException`, inside services.
- Enums are `StrEnum` in `models/enums.py`, enforced by `CheckConstraint(check_in(...))`, not native PG enums. Adding a value needs a migration that alters the check constraint.
- Every schema change needs an Alembic migration; `alembic check` must report no differences.
- Money and quantities are `Decimal` / `Numeric`; never use floats for stored values.
- Tests: integration tests hit the API via `TestClient`. Processing runs inline (`INLINE_PROCESSING`), and the LLM and SMTP sender are monkeypatched (`FakeSender`, `FakeLLM`). Image fixtures come from `tests/fixtures/generate_fixtures.py`; regenerate them rather than hand-editing.
- Frontend: TanStack Query for all server state (invalidate the affected query keys after mutations), React Hook Form + Zod or controlled state for forms, `Field` for accessible labels and errors, `ConfirmDialog` before destructive actions.
- Line length is 120 (ruff). Match the comment density of surrounding code: explain *why*, especially in business rules.
