# Build Phases

**Current phase: Phase 7 — Recovery packet generation**

Phase 3's gate is still unverified pending a live Postgres — see below.
Phase 4 is fully verified and checked off. Phases 5, 6, and 7 each split
their work into a pure half (fully verified here) and a DB-writing half
(code-complete, unverified for the same reason as Phase 3) — see below.

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
- [ ] Phase 6 — API layer — **code complete, DB-writing half unverified
      for the same reason as Phases 3 and 5.** FastAPI service exposing
      upload remittance, list/detail findings, export worklist CSV,
      contract management, and audit log query. Same pure/DB split as
      Phase 5: route handlers depend on an `api.repository.Repository`
      port (`src/api/repository.py`), never on SQLAlchemy directly, with
      two adapters — `PostgresRepository` (real) and `FakeRepository`
      (test-only, in-memory, tenant-partitioned, `tests/api/fakes.py`).
      This is what let the **full authorization matrix** (every one of 4
      roles x 8 endpoints x own-tenant/other-tenant) run as real, passing
      tests without Postgres — not a subset, not skipped
      (`tests/api/test_authz_matrix.py`, 32 cases, all passing). Also
      fully verified without a live DB: no route anywhere accepts a
      client-supplied tenant identifier — proven both by inspecting every
      route's actual parameters and by inspecting the generated OpenAPI
      schema (`test_tenant_param_absence.py`) — which is what makes
      "no endpoint returns another tenant's data under any parameter
      manipulation" true by construction, not by defensive checking.
      OpenAPI spec generates and validates via `openapi-spec-validator`
      (`test_openapi.py`). Structured errors never echo PHI, proven by
      forcing an unhandled exception containing PHI-shaped text and
      asserting none of it reaches the response
      (`test_error_redaction.py`). Pagination and CSV export also covered
      (`test_pagination.py`, `test_csv_export.py`). **Not yet verified**:
      two representative endpoints (findings list, finding detail) run
      against real `PostgresRepository` + real RLS in
      `test_endpoints_live_db.py` — written, skips cleanly without
      `TEST_DATABASE_URL`, never executed; this is what would confirm
      `FakeRepository`'s tenant-isolation guarantee matches reality.
      Added a `users` table (ungated like `tenants`, for resolving a
      bearer token's subject to a tenant_id — ADR-style rationale in
      `src/db/models.py`'s `User` docstring) and an `audit_log.request_id`
      column via `alembic/versions/0003_users_and_audit_request_id.py`
      (offline-verified only, same ceiling as 0001/0002). Added
      `Action.READ_CONTRACT` to `security/rbac.py` (additive; Phase 4's
      full role x action matrix test updated and still green — now 47
      cases). No login/credential/OIDC endpoint was built — Phase 4 never
      built real credential verification to wire one to; tests mint
      tokens directly via the already-gated `issue_session()`. No
      user-management HTTP endpoint either — out of this phase's explicit
      scope. **Do not check this phase off until the live-DB tests above
      are run against a real Postgres**, same standard as Phases 3 and 5.
- [ ] Phase 7 — Recovery packet generation (the only place an LLM appears)
      — **code complete, DB-writing half unverified for the same reason as
      Phases 3, 5, and 6.** New `src/packets/` package (pure): `currency.py`
      (extracts every currency-shaped figure from LLM output, parses as
      `Decimal`, rejects if any value isn't in the finding record's known
      amounts), `prompt.py` (patient name/member id and every dollar
      figure are placeholder tokens in the text sent to the LLM — never
      the real values — substituted back in only after generation),
      `drafter.py` (`PacketDrafter` port: `ScriptedPacketDrafter` for
      every test, `AnthropicPacketDrafter` real adapter untested by
      design, same deferral as real cloud KMS), `templates.py`
      (per-payer letter boilerplate), `worklist.py` (deadline-proximity-
      then-dollar-value ranking), `service.py` (orchestrates draft ->
      reject-if-raw-figure -> substitute -> validate -> retry, up to a
      small cap; never returns an unvalidated draft). New
      `src/domain/deadlines.py` (pure `date` arithmetic, no timezone
      concept to get wrong by construction; leap-year-correct because
      Python's `date` already is). All of the above fully verified
      without a live DB — this **is** the phase's actual gate:
      `tests/packets/test_currency.py` proves a deliberately corrupted
      draft is rejected, `tests/packets/test_prompt.py` proves patient
      identifiers never reach the captured LLM prompt text (across
      distinctive names chosen so a false negative can't happen by
      luck), `tests/domain/test_deadlines.py` proves the deadline math
      across a leap-day-spanning window and a year rollover. New
      `Action.DRAFT_RECOVERY_PACKET` in `security/rbac.py` (additive;
      Phase 4's matrix test updated, now 51 cases). New `recovery_packets`
      table (tenant-scoped, RLS) plus `contracts.timely_filing_days` /
      `contracts.packet_template` via
      `alembic/versions/0004_recovery_packets_and_timely_filing.py`
      (offline-verified only) — deliberately **not** on
      `domain.contract.ContractVersion`, to avoid rippling through every
      test file that constructs one. Three new API routes
      (`POST/GET /findings/{id}/packets`, `POST /packets/{id}/approve`,
      `POST /packets/{id}/reject` — the human-approval step, never
      automatic) added to the same authz matrix discipline as Phase 6
      (`tests/api/test_authz_matrix.py`, now 44 cases). **Not yet
      verified**: one round-trip test (generate a packet against a real
      ingested finding, approve it, confirm both audit entries) in
      `tests/api/test_endpoints_live_db.py` — written, skips cleanly
      without `TEST_DATABASE_URL`, never executed. **Do not check this
      phase off until that test is run against a live Postgres**, same
      standard as Phases 3, 5, and 6.
- [ ] Phase 8 — Observability and audit
- [ ] Phase 9 — Cloud-agnostic deployment
- [ ] Phase 10 — CI/CD and pre-production hardening
- [ ] Phase 11 — Real data readiness (the compliance gate — no code)
- [ ] Phase 12 — First customer pilot
