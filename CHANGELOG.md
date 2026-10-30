# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Delete packaging scans (`DELETE /api/scans/{id}`) from the scan list or review page. A batch the scan created loses its remaining stock (recorded correction); a date it confirmed on an existing batch is kept.
- Delete invoices (`DELETE /api/invoices/{id}`) from the invoice page or list. The file is removed, and a confirmed invoice's remaining stock is taken out of inventory with a recorded correction; stock already used is untouched.

### Changed
- Removed the "AI & services status" card from Settings; the business and reminder settings now use the full width. The `GET /api/system/ai-status` endpoint is unchanged for administrators.
- Removed the barcode tip from the Scan Product photo instructions.
- Quantities count packs or pieces, and an optional **size per pack** (value + g/kg/ml/ltr) is stored on batches and invoice lines (`pack_size`, `pack_unit`). It is read from invoice product names ("Milk 500ml") and label text ("Net Qty: 500 ml"), stays editable, and shows as "20 pcs × 500 ml". Loose goods ("5 kg") become packs of 1 kg. A migration adds the columns and backfills sizes from existing invoice lines.
- Mandatory fields are marked with a red `*` throughout the app and announced as "required" to screen readers.
- The invoice page says "Read automatically with AI" instead of naming the model.
- Missing units are no longer warned about on invoice lines; a data migration removes those notes from existing invoices.
- Confirming an invoice adds every accepted line to inventory in the same step (one batch per line in pcs, with the line's pack size, no expiry date). Lines missing a quantity stay for manual adding. `POST /api/invoices/{id}/confirm` accepts `add_to_inventory: false` for the old behaviour.
- Dates are shown and entered as dd/mm/yyyy regardless of browser locale; ambiguous date choices spell out the month.
- No "please check" warning when the local AI ran but the rule-based result was better; the warning now appears only when the AI could not be used.
- `.env.example` documents `COMPOSE_PROFILES=ai` to start Ollama with the stack.
- The confirm dialog says in advance which lines will be skipped because they have no quantity.

### Fixed
- Sign out left the signed-in user on screen and bounced back to a "Not authenticated" dashboard (the user query was removed from the cache before being marked signed out).
- AI-read line values were flagged "not found in the document" when invoice columns were separated only by spaces (`10  45.00  450.00` was read as one number).

## [1.0.0] - 2026-10-29

First complete release.

### Added
- Accounts with a business workspace per registration: Argon2 password hashing, revocable server-side sessions (HttpOnly cookie or Bearer token), CSRF header check, rate limiting, and backend-enforced tenant isolation.
- Invoice scanner for PDF, JPEG and PNG: PDF text layer or OCR, rule-based parsing with optional local LLM extraction (Ollama, schema-constrained, grounded against the document text), arithmetic and duplicate checks, a review screen with manual entry, and splitting one line into several batches.
- Packaging scanner with camera capture: OCR, EAN/UPC/QR barcode reading, deterministic labelled date extraction (manufacturing, packing, expiry/use-by, best-before) with ambiguity detection, and explicit user confirmation of expiry dates.
- Batch-level inventory with an audited stock ledger (consumed, sold, discarded, returned, received, counted correction), negative-stock protection, search, filters and sorting.
- Expiry engine with timezone-aware statuses and configurable reminder intervals (default 7/3/1/0 days), plus an optional expired-stock alert.
- SMTP email reminders with HTML templates, idempotent scheduling, bounded retries, manual retry and resend, and automatic cancellation.
- Dashboard with summary cards, upcoming-expiry filters, a status chart and recent activity; onboarding for empty workspaces.
- Docker Compose stack (PostgreSQL, Mailpit, backend, nginx frontend, optional Ollama), Alembic migrations, synthetic sample documents, and backend and frontend test suites.
