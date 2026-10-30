# Security

## Reporting a vulnerability

Please report vulnerabilities privately to the project maintainers instead of opening a public issue. Include the affected version or commit, steps to reproduce, and the impact you observed. We aim to acknowledge reports within a few working days.

## Security model

SmartShelf AI is designed to be self-hosted by a single business or a small group of businesses. Its main protections:

| Area | Measure |
|---|---|
| Passwords | Argon2id hashing (`argon2-cffi`), automatic rehash on login when parameters change. Plaintext is never stored or logged. |
| Sessions | Random 256-bit tokens; only their SHA-256 hash is stored. Server-side revocation on logout, configurable expiry (`SESSION_TTL_HOURS`). |
| Browser auth | HttpOnly, `SameSite=Lax` session cookie; set `SESSION_COOKIE_SECURE=true` behind HTTPS. State-changing cookie requests must carry `X-Requested-With`, which blocks cross-site request forgery. |
| Tenant isolation | Every query is scoped to the caller's business, derived from the server-side session, never from client input. Other tenants' IDs return 404. Covered by automated tests. |
| Brute force | In-memory rate limits on login and registration (per IP + email) and on uploads (per user). Equal-time responses for unknown emails. |
| Uploads | Size and page limits; type detected from magic bytes; images verified with decompression-bomb protection; random server-side filenames outside any web root; files served only through authenticated, tenant-checked endpoints with `nosniff`. |
| AI input | Document text is untrusted: it is delimited in prompts, model output is schema-validated and grounded against the OCR text, and it is never executed as code, SQL or commands. Dates never come from a model. No data leaves the server. |
| Database | ORM-parameterised queries only; constraints enforce non-negative stock and valid states; multi-step changes run in transactions. |
| Errors and logs | Clients receive generic messages without stack traces. SMTP errors are summarised, not echoed, so credentials cannot leak. Full document text is not logged. |
| Secrets | All secrets come from environment variables. `.env` is git-ignored; `.env.example` holds placeholders only. |

## Hardening checklist for production

- [ ] Serve the app over HTTPS only and set `SESSION_COOKIE_SECURE=true`.
- [ ] Set a strong, unique `POSTGRES_PASSWORD` and do not publish port 5432 (remove the `ports` mapping for `db`).
- [ ] Do not publish Mailpit or Ollama ports. Point `SMTP_*` at your real mail provider and use TLS (`SMTP_USE_TLS` or `SMTP_USE_SSL`).
- [ ] Restrict `CORS_ORIGINS` to your real frontend origin.
- [ ] Back up the PostgreSQL volume and the uploads volume, and test restores.
- [ ] Keep base images and dependencies updated (`pip list --outdated`, `npm outdated`).
- [ ] If you run more than one backend instance, move rate limiting to a shared store or the reverse proxy (see the README).

## Known gaps

These are tracked as future work rather than solved today:

- No password-reset or email-verification flow.
- No account lockout beyond rate limiting, and no multi-factor authentication.
- Uploaded documents are kept indefinitely; there is no retention or deletion policy yet.
- Rate limiting is per process and resets on restart.
