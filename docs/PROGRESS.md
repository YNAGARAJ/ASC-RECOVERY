# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist — this file adds the texture that isn't in that
summary.

## Phase: 12 (first customer pilot). Code complete; DB-backed half unverified, same ceiling as every phase before Phase 10's CI run.

**This phase started with Phase 11's own gate still open.** Phase 11's
entry in `docs/PHASES.md` says explicitly: "do not check this phase off,
and do not start Phase 12, until every one of the 15 items in
`docs/compliance/README.md`'s tracker reads DONE with real evidence." That
has not happened — nothing in `docs/compliance/` is signed, purchased,
engaged, or tested. The user explicitly directed proceeding anyway ("yes,
proceed with Phase 12 we have audit to do") after being told this
directly. This is a deliberate sequencing override, not something a
resuming session should quietly "fix" by trying to backfill Phase 11 —
that phase's remaining items are external actions (BAAs, insurance, a
pentest engagement), not more engineering.

Everything in this phase that *can* be verified without a live Postgres
has been: pure domain logic, RBAC matrix, schema serialization, the
onboarding script's config validation, offline Alembic SQL generation.
Everything that needs a real database (four DB-backed test files) is
written, skips cleanly with an explicit message, and has never executed —
it will get its first real run the next time this branch's CI pipeline
goes green against a real Postgres, same as every DB-writing phase before
Phase 10.

## Done (files completed, one line each)

- `src/domain/outcomes.py` (new) — `Outcome` enum, `validate_outcome_recording`
  (rejects a second recording on the same finding, and any recording on a
  `CORRECT_NO_VARIANCE` finding), `calculate_confidence` (recovered-count /
  decided-count, `None` on cold start). Pure, no I/O.
- `tests/domain/test_outcomes.py` (new) — 8 tests, all passing.
- `alembic/versions/0005_finding_outcomes.py` (new) — four nullable
  columns on `findings`: `outcome`, `amount_recovered`,
  `outcome_recorded_by`, `outcome_recorded_at`. Same idempotency pattern as
  0002-0004 (offline branch + inspector-guarded online branch).
- `src/db/models.py` — `Finding` extended with the four columns above.
- `src/db/repository.py` — `record_finding_outcome`, `list_historical_outcomes`
  (payer + root_cause, joined via `contract_version -> contract`),
  `list_findings_past_deadline_without_outcome` (surfaces candidates for a
  human to confirm as expired; never writes anything itself).
- `src/security/rbac.py` — new `Action.RECORD_FINDING_OUTCOME`, granted to
  BILLER and ADMIN.
- `src/api/repository.py` — `FindingSummary`/`FindingDetail` extended with
  outcome fields; new `RecordOutcomeInput`; `Repository.record_finding_outcome`
  (Protocol + `PostgresRepository` impl); `confidence_score` computed in
  `get_finding_detail` only (not the list endpoint — see Decisions below);
  new `_lookup_payer_id` helper.
- `src/api/schemas.py` — `RecordOutcomeIn`; outcome fields on
  `FindingSummaryOut`; `confidence_score` on `FindingDetailOut`.
- `src/api/routes/findings.py` — `POST /findings/{id}/outcome` (409 on
  `OutcomeAlreadyRecordedError`, 422 on `NothingToAppealError`); CSV export
  gained `outcome`/`amount_recovered` columns.
- `tests/api/fakes.py`, `tests/api/conftest.py`, `tests/api/test_pagination.py`,
  `tests/api/test_csv_export.py`, `tests/api/test_authz_matrix.py` — updated
  for the new fields/route; `test_authz_matrix.py` now has a
  `test_record_outcome_matrix` case, 52 total cases in that file, all
  passing.
- `scripts/onboard_customer.py` (new) — creates a tenant, its first admin
  user, and optionally an initial contract + fee-schedule version. A
  script, not an endpoint (see Decisions). Connects with the app's own
  `DATABASE_URL`, calls straight into `db.repository`.
- `docs/RUNBOOK.md` — new "Onboarding a new customer" section documenting
  the script, its JSON config shape, and a worked example.
- `tests/db/test_finding_outcomes.py` (new) — 3 DB-backed tests for the
  three new repository functions, findings seeded directly via
  `save_findings` (not full 835 ingestion) for exact control over
  payer/root_cause/shortfall.
- `tests/api/test_endpoints_live_db.py` — new
  `test_record_outcome_cross_tenant_lookup_is_404_against_real_rls`.
- `tests/api/test_pilot_workflow_live_db.py` (new) — the full synthetic
  pilot: onboard -> load fee schedule -> ingest three synthetic 835 files
  sharing one payer (standing in for "a quarter" — no real pilot customer
  exists in this environment) -> findings report via the API -> record two
  outcomes -> confirm the third finding's `confidence_score` is `"0.5"` ->
  confirm a decided finding's own score excludes itself -> confirm
  recording twice is rejected with 409.
- `docs/PHASES.md` — Phase 12 entry added with full detail (deliberately
  left unchecked); current-phase header updated.

## In progress

Nothing mid-write. Every item in the approved Phase 12 plan is complete —
this checkpoint is being written at the end of the phase's local-gate
pass, not mid-implementation.

## Failing

Nothing failing locally. Full gate run this session, all green:
`ruff check .` clean, `mypy --strict .` clean (143 files), `pytest -q`
408 passed / 28 skipped (skips are every DB-backed test file across the
whole repo, not new failures), 100% branch coverage on
`domain/variance.py`, `python -m evals.run` GATE PASSED, `bandit -r .`
clean, `pip-audit` clean, `alembic upgrade head --sql` clean through 0005.
`gitleaks` was not run — not installed in this environment, same gap as
every prior phase; CI's `security` job is what actually covers it.

**The real unresolved item is verification, not failure**: the four
DB-backed test files this phase added/touched
(`tests/db/test_finding_outcomes.py`,
`tests/api/test_endpoints_live_db.py`'s new case,
`tests/api/test_pilot_workflow_live_db.py`) have never run against a real
Postgres. They skip cleanly with an explicit message rather than silently
passing — but "written and skips cleanly" is not the same as "verified,"
and a resuming session should not report this phase as fully done until
CI actually runs them green.

## Decisions worth knowing (not obvious from the code)

- **`confidence_score` lives only on `FindingDetailOut`, not
  `FindingSummaryOut`.** The approved plan didn't specify this split
  explicitly; it was decided during implementation to avoid an N+1 query
  cost across a paginated list of up to 100 findings. If a future phase
  wants confidence visible in the worklist table itself, that needs a
  batched query design, not just moving the existing per-finding lookup
  into the list path.
- **Onboarding a new tenant is a script (`scripts/onboard_customer.py`),
  deliberately not an API endpoint.** `security/rbac.py` is entirely
  tenant-scoped — there is no "platform superadmin" role that could gate a
  `POST /tenants` endpoint without breaking the no-cross-tenant-access
  boundary maintained since Phase 3. Introducing one is a real
  architectural decision for a later phase to make deliberately, not a
  side effect of this phase's onboarding need.
- **`list_findings_past_deadline_without_outcome` only surfaces
  candidates — it never writes `outcome="expired"` itself.** Every outcome
  recording, including expiry, is a human decision
  (`domain.outcomes.validate_outcome_recording` is only ever called from
  the human-facing recording path). There is currently no scheduled job or
  route that calls this query at all; it exists for a future "overdue
  findings" view/report to be built on top of, not wired to anything yet.
- **Findings in `tests/db/test_finding_outcomes.py` are built directly via
  `db.repository.save_findings`, not through the full 835 ingestion
  pipeline** (unlike `tests/api/test_endpoints_live_db.py`'s existing
  pattern). This was a deliberate choice for that file specifically, to
  get exact control over payer_id/root_cause/shortfall combinations needed
  to prove the filtering logic, without hand-building distinct synthetic
  835 files for every scenario. `test_pilot_workflow_live_db.py`, by
  contrast, does go through real ingestion (`ingest_file`) end to end,
  because demonstrating the actual pilot workflow — not just the
  repository functions — is that file's whole point.
- **The synthetic "quarter" in `test_pilot_workflow_live_db.py` is three
  files, not a real quarter's volume**, each built from a parameterized
  copy of `tests/domain/fixtures_x835.py`'s `claim_segments` shape (same
  charge/allowed/paid numbers, different claim/payer control numbers so
  they don't collide on remittance file_hash dedup or claim identity).
  This keeps all three findings identical in payer/root_cause/shortfall by
  construction, which is exactly what the confidence-score test needs and
  is stated as a demonstration, not a claim of real pilot volume.

## Traps for someone resuming cold

- **Don't check Phase 11 off as a side effect of Phase 12 progress.**
  They are independent; Phase 12 being code-complete says nothing about
  Phase 11's external checklist items being done. See Phase 11's own
  `docs/PHASES.md` entry and `docs/compliance/README.md`'s tracker.
- **`RootCause` is stored on `Finding` as `.name` (e.g.
  `"UNDETERMINED_VARIANCE"`), not `.value`** — reconstructing it from a DB
  row requires `RootCause[stored_string]` (bracket lookup), not
  `RootCause(stored_string)` (call syntax, which would raise). This
  predates this phase but is exactly what `record_finding_outcome`'s
  validation path in `api/repository.py` depends on getting right.
  `list_historical_outcomes` takes `root_cause: str` and matches it
  against this same `.name` string, not the enum's `.value`.
  `Decimal(1) / Decimal(2)` from `calculate_confidence` renders as `"0.5"`
  via `str(Decimal(...))`, but `Decimal(0) / Decimal(1)` renders as `"0"`,
  not `"0.0"` — `test_pilot_workflow_live_db.py`'s assertions depend on
  getting this exactly right; don't "simplify" those literals without
  checking Python's actual `Decimal` string formatting.
- **`db.repository.list_findings` orders by `FindingModel.created_at.desc()`**,
  and rows inserted within the same transaction can share an identical
  `created_at` (Postgres's `now()` is transaction-start time, not
  statement time) — `test_pilot_workflow_live_db.py` deliberately never
  depends on which of the three ingested findings comes back "first";
  it only partitions the returned list into "two I'll decide" and "one
  I won't," which holds regardless of tie-breaking order since all three
  are structurally identical by construction.
- Everything from the Phase 3-11 checkpoints still applies (CRLF warnings
  on `git add`, cross-platform lockfile drift if `make lock` runs on
  Windows/macOS, `.terraform/`/`sbom.json` gitignored on purpose,
  `scripts/hooks/block_phi.sh` blocks any email not ending in
  `@example.com`/`@test`/`@localhost` in new file content — this session
  hit that hook once with `@example.test` and had to switch to
  `@example.com`). See `docs/PHASES.md`'s Phase 10 entry for the full,
  longer list of earlier traps.

## Next 3 steps

1. **Push this branch and let CI run the four new/updated DB-backed test
   files against a real Postgres** — this is the only thing standing
   between "code complete" and actually checking Phase 12 off in
   `docs/PHASES.md`. Nothing about the pure/local half needs redoing.
2. **If CI surfaces a real bug** (same pattern as Phase 10's "CI debugging
   round" and Phase 5/6/7/8's first live runs): fix it, re-run the full
   local gate, push again. Do not assume the DB-backed tests are correct
   just because they're well-written and skip cleanly locally.
3. **Separately, and not blocking Phase 12's own code-complete status**:
   Phase 11's external checklist items (BAAs, insurance, pentest,
   workforce training, legal review) are still open and are what actually
   gates real PHI ever reaching this system — see that phase's own
   `docs/PHASES.md` entry and `docs/compliance/README.md` for the concrete
   next actions there.
