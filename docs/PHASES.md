# Build Phases

**Current phase: Phase 5 — Ingestion pipeline**

Phase 3's gate is still unverified pending a live Postgres — see below.
Phase 4 is fully verified and checked off.

One phase per session. `/clear` between phases. Never advance past a failing
gate. See `docs/MASTER-BUILD-PROMPT.md` for full phase prompts and gates.

- [x] Phase 0 — Scaffold, constitution, guardrails
- [x] Phase 1 — Domain core (pure, no I/O)
- [x] Phase 2 — Eval harness and golden dataset
- [ ] Phase 3 — Persistence, tenancy, and effective-dated contracts —
      **code complete, hard gate not yet run.** The build environment this
      was written in has no Docker/WSL/Postgres, only `pip`. Everything
      checkable without a live database is green: `mypy --strict .`,
      `ruff check .`, `alembic upgrade head --sql` (offline DDL
      generation), and the full existing suite (`tests/db/`'s 11 tests
      skip with an explicit message rather than silently passing). The RLS
      tenant-isolation test, the idempotent-remittance test, the
      effective-dated-pricing round-trip, and the audit-log append-only
      test are all written but have never executed against a real
      Postgres. **Do not check this phase off until someone runs them** —
      see `docs/DB_SETUP.md` for exact steps (`docker compose up -d`,
      `alembic upgrade head`, then
      `TEST_DATABASE_URL=... pytest tests/db/ -v`).
- [x] Phase 4 — Security and PHI controls — fully verified. Envelope
      encryption (AES-256-GCM, KEK rotation without re-encrypting data),
      TOTP MFA, session issuance with a proven MFA-cannot-be-bypassed
      guarantee, deny-by-default RBAC (full role x action matrix tested),
      rate limiting + account lockout, and PHI log redaction all have
      passing tests with no external services required. `mypy --strict .`,
      `ruff check .`, full suite (219 passed, 11 skipped — the Phase 3
      `tests/db/` skips), `bandit`, and `pip-audit` all run clean — see
      `docs/SECURITY.md` for the full control -> HIPAA citation mapping
      and the two explicitly out-of-scope items (real cloud KMS adapters,
      deferred to Phase 9; `gitleaks` full-history scan, unavailable in
      this environment).
- [ ] Phase 5 — Ingestion pipeline
- [ ] Phase 6 — API layer
- [ ] Phase 7 — Recovery packet generation (the only place an LLM appears)
- [ ] Phase 8 — Observability and audit
- [ ] Phase 9 — Cloud-agnostic deployment
- [ ] Phase 10 — CI/CD and pre-production hardening
- [ ] Phase 11 — Real data readiness (the compliance gate — no code)
- [ ] Phase 12 — First customer pilot
