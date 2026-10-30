# Contributing to SmartShelf AI

Thanks for helping. This guide covers the workflow; [CLAUDE.md](CLAUDE.md) lists the codebase invariants every change must respect, and the [README](README.md) covers setup.

## Development setup

```bash
cp .env.example .env
docker compose up -d db mailpit

cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000

cd ../frontend
npm install && npm run dev        # http://localhost:5173
```

Tesseract must be installed locally (`apt install tesseract-ocr`) for the OCR tests; without it they are skipped. Ollama is optional.

## Making a change

1. Create a branch from `main`: `feature/<short-name>` or `fix/<short-name>`.
2. Keep changes focused. A schema change, its migration and the code that uses it belong in one change.
3. Add or update tests:
   - Backend logic: a unit test next to the related ones in `backend/tests/`.
   - New endpoints: an integration test through `TestClient`, **including a tenant-isolation check** (another business must get 404).
   - User-facing workflows: a Vitest test in `frontend/src/test/`.
4. Run the full check before opening a pull request:

   ```bash
   cd backend && .venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/alembic check
   cd ../frontend && npm test && npm run typecheck && npm run lint && npm run build
   ```

5. If you changed configuration, update `.env.example` and the README's environment table.
6. Add a line to [CHANGELOG.md](CHANGELOG.md) under *Unreleased*.

## Database migrations

- Change models in `backend/app/models/`, then run `alembic revision --autogenerate -m "describe change"`.
- Read the generated file. Autogenerate misses check-constraint edits and data migrations, so write those by hand.
- Migrations must apply on an empty database (the test suite does this on every run) and on existing data.
- Never edit a migration that has already been released; add a new one.

## Working on AI extraction

- Put sample documents in `backend/tests/fixtures/` (synthetic data only, never real supplier invoices or customer data) by extending `generate_fixtures.py`.
- Changes to prompts or model selection must keep the safeguards: schema-constrained output, Pydantic validation, bounded retries, and grounding against the OCR text.
- Never let a model supply date values. Improve `date_extraction_service.py` instead, with tests for every new format or label.
- Mock the LLM in automated tests (`FakeLLM` in `tests/test_invoice_extraction.py`). Tests must not require Ollama or network access.

## Commit messages

Use the imperative mood and explain why when it isn't obvious:

```
Prefer arithmetically consistent invoice lines over LLM output

Small models read package sizes ("500 ml") as quantities. The value is
grounded in the text but wrong, so result selection now weighs qty × price = total.
```

## Pull request checklist

- [ ] Tests added or updated, and the full check passes
- [ ] Migration included for schema changes, and `alembic check` is clean
- [ ] New queries filter by business; an isolation test covers new resources
- [ ] No secrets, real customer documents or personal data committed
- [ ] User-facing text is plain and actionable for non-technical cafe staff
- [ ] README / `.env.example` / CHANGELOG updated where relevant

## Reporting security issues

Do not open a public issue for vulnerabilities. See [SECURITY.md](SECURITY.md).
