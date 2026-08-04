# Build Phases

**Current phase: Phase 5 — Ingestion pipeline**

Phase 3's gate is still unverified pending a live Postgres — see below.
Phase 4 is fully verified and checked off. Phase 5's pure logic (planning,
reconciliation, sources, virus scan) is fully verified; its DB-writing half
is code-complete but unverified for the same reason as Phase 3 — see below.

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
- [ ] Phase 5 — Ingestion pipeline — **code complete, DB-writing half
      unverified for the same reason as Phase 3.** Split into a pure
      planning layer (`src/ingestion/reconcile.py`, `plan.py`, `sources.py`,
      `virus_scan.py`) and a thin DB-apply layer (`apply.py`,
      `pipeline.py`), specifically so most of this phase's logic could
      actually run in this Postgres-less environment instead of joining
      Phase 3 in permanent-skip limbo. Fully verified without a live DB:
      quarantine decisions (unparseable file, all-claims-malformed file),
      partial-batch handling (one bad claim doesn't fail the file), BPR
      reconciliation math, reversal/takeback netting (sums to exactly
      zero), source adapters (SFTP/S3 against fake clients implementing
      minimal Protocols), and the EICAR-based virus-scan path — all real,
      passing tests, not skips. **Not yet verified**: the three tests that
      exercise `ingestion.pipeline.ingest_file` end to end against
      Postgres (`tests/ingestion/test_apply_idempotency.py`,
      `test_apply_quarantine.py`, `test_apply_audit_entry.py`) — written,
      skip cleanly without `TEST_DATABASE_URL`, never executed. Added
      `alembic/versions/0002_remittance_quarantine_reason.py` (verified
      offline only, same ceiling as 0001) and five additive functions to
      `src/db/repository.py`. **Do not check this phase off until the
      three DB-backed tests above are run against a live Postgres**, same
      standard as Phase 3.
- [ ] Phase 6 — API layer
- [ ] Phase 7 — Recovery packet generation (the only place an LLM appears)
- [ ] Phase 8 — Observability and audit
- [ ] Phase 9 — Cloud-agnostic deployment
- [ ] Phase 10 — CI/CD and pre-production hardening
- [ ] Phase 11 — Real data readiness (the compliance gate — no code)
- [ ] Phase 12 — First customer pilot
