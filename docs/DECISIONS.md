# Design decisions

Short records of decisions that shape the codebase, with the reasoning behind them. Add a new entry rather than rewriting an old one when a decision changes.

---

## 1. Dates are parsed deterministically, never by a model

**Decision:** Expiry, manufacturing, packing and best-before dates come only from `date_extraction_service.py`. A language or vision model may transcribe date text, but the value is always re-parsed by rules and marked uncertain.

**Why:** A wrong expiry date is a food-safety problem. Rules are testable, explainable to staff ("read from 'EXP: 01/11/2026'") and never hallucinate. Models are good at finding text, not at being reliably right about 03/04 vs 04/03.

**Consequence:** New date formats or labels need code and tests, not prompt changes.

## 2. A human confirms every expiry date before it drives reminders

**Decision:** Batches store `expiry_date_status` (missing / unconfirmed / confirmed). Only confirmed dates are considered by the reminder scheduler. Scan results are confirmed only through an explicit checkbox.

**Why:** OCR misreads are common on packaging. An unconfirmed date is still shown (and flagged), but it never causes a misleading email.

## 3. The rule-based invoice parser always runs; the LLM is optional

**Decision:** Every invoice goes through the deterministic parser. When Ollama is available, the LLM result is grounded against the OCR text and compared with the rule result. The one that is more complete and more arithmetically consistent wins, and the header gaps are filled from the other.

**Why:** The app must work without a model, on modest hardware. Testing with a real 1.5B model showed it sometimes reads package sizes ("500 ml") as quantities. Those values are grounded in the text but wrong, so qty × price = total became part of the selection.

## 4. Grounding instead of trusting model output

**Decision:** LLM header values that don't appear in the document text are discarded; line items whose names aren't found are dropped; ungrounded numbers are flagged.

**Why:** The core requirement is "never invent data". Schema validation proves the shape, not the truth; grounding is a cheap, deterministic truth check.

## 5. Inventory is tracked per batch with an append-only ledger

**Decision:** Each delivery creates `InventoryBatch` rows with their own expiry date. Quantities change only through `InventoryMovement` rows written under a row lock, with a database check preventing negative stock.

**Why:** The same product arrives with different expiry dates; a single quantity per product cannot answer "which packets expire Friday?". The ledger answers "who changed this and when".

## 6. Server-side sessions instead of JWTs

**Decision:** Opaque random tokens stored as SHA-256 hashes, sent in an HttpOnly cookie (browser) or as a Bearer token (API clients).

**Why:** Logout must really log out (revocation), and the browser should never hold a readable token. The cookie also lets `<img>` tags load private scan images without exposing public URLs.

## 7. String enums with check constraints, not PostgreSQL enums

**Decision:** Status columns are `VARCHAR` with `CHECK (status IN (...))`, generated from Python `StrEnum`s.

**Why:** Adding a value to a native PG enum is awkward in migrations (it cannot run inside some transactions and cannot be easily removed). Check constraints are simple to alter.

## 8. In-process background work for the MVP

**Decision:** APScheduler and a small thread pool run inside the API process. Jobs take PostgreSQL advisory locks, records are claimed atomically, and a sweeper recovers interrupted work after restarts.

**Why:** It avoids running Redis or a separate broker for a single-cafe deployment. The locking makes accidental double instances safe, and `python -m app.workers` already allows a dedicated worker. The README describes the path to a real queue.

## 9. One reminder per interval, tightest interval only

**Decision:** When several intervals are due at once (e.g. a batch added 2 days before expiry with intervals 7/3/1/0), only the tightest due interval is sent. The idempotency key is (batch, recipient, kind, interval, expiry date).

**Why:** Three emails at once about the same batch is noise. Including the expiry date in the key means correcting a date starts a fresh reminder cycle, and the old pending reminders are cancelled.

## 10. Hand-written shadcn-style components

**Decision:** UI primitives in `frontend/src/components/ui/` are written by hand in the shadcn style (Radix + Tailwind + `cva`) instead of being generated with the shadcn CLI.

**Why:** It keeps the project buildable offline with a small, reviewable set of components that only cover what the app uses. New components should follow the same pattern.

## 11. Confirming an invoice receives the stock

**Decision:** `POST /confirm` turns each accepted line into one batch in the same transaction. The product is matched by exact name or created; fuzzy look-alikes are not merged. Batches have no expiry date. Lines without a quantity are left for manual adding. Quantities count packs (`pcs`); what one pack holds is a separate pack size (500 ml) read from the product name, so "20 packet", "20 bottle" and a missing unit all mean the same count.

**Why:** Staff found adding each line separately tedious. Confirming already means "this delivery arrived". Splitting a line by expiry and choosing a fuzzy catalogue match remain available, through the batch page, scanning, or the manual flow (`add_to_inventory: false`).
