# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint (about to commit "Phase 5:
ingestion pipeline"). Read `docs/PHASES.md` first for the phase checklist —
this file adds the texture that isn't in that summary.

## Phase: 5 code-complete, DB-writing half unverified. 3 is the same story.

Phases 0–2 and 4 are fully done: code written, gate criteria genuinely
checked, nothing hand-waved. **Phases 3 and 5 both have an honest gap**:
this machine has no Docker, no WSL, and no local Postgres — only `pip`
works (re-checked again this session; unchanged). Everything checkable
without a live database is green for both phases. The DB-writing parts are
written, tested where a test can exist without Postgres, and explicitly
**not** claimed as passed.

Current phase per `docs/PHASES.md`: **Phase 5 — Ingestion pipeline**,
code-complete, pure half verified, DB half unverified.

## Done (this session, Phase 5)

**Key architectural decision**: split ingestion into a pure planning layer
and a thin DB-apply layer, specifically so most of Phase 5's logic could
get *real, running* tests in this Postgres-less environment instead of
inheriting Phase 3's all-or-nothing skip status.

- `src/ingestion/reconcile.py` — pure `reconcile_bpr(transaction)`: BPR
  total paid vs sum(claim totals) + sum(PLB amounts), `Money("0.01")`
  tolerance. Fully tested (`tests/ingestion/test_reconcile.py`, 3 tests,
  all pass).
- `src/ingestion/virus_scan.py` — `VirusScanner` Protocol (same port shape
  as `security/kms.py`) + `EicarAwareScanner`, a dev adapter that flags
  only the industry-standard EICAR test string. Real AV engine integration
  deferred, same pattern as Phase 4 deferring real cloud KMS to Phase 9.
  Fully tested.
- `src/ingestion/sources.py` — `IngestionSource` Protocol + `UploadSource`
  (trivial, wraps one in-memory file) + `SFTPPollSource` / `S3PollSource`,
  both typed against minimal structural Protocols (`SFTPClient`,
  `S3Client`) rather than depending on `paramiko`/`boto3` — a real client
  satisfies them without this project taking on either dependency. Fully
  tested against fake in-memory clients, no real network needed.
- `src/ingestion/plan.py` — the core of this phase, **pure, no I/O**:
  `build_ingestion_plan(parse_result, *, contract_versions_by_payer,
  prior_findings_by_control_number) -> FileIngestionPlan`. Takes an
  already-parsed `domain.x835.ParseResult` plus pre-fetched contract/prior-
  finding data (both supplied by the caller — real DB fetches in
  `pipeline.py`, hand-built fixtures in tests), and decides: quarantine
  (zero usable claims parsed), per-claim pricing (`domain.contract.
  price_claim` + `domain.variance.evaluate_claim`, both Phase 1, untouched),
  and reversal netting. **Reversal netting deliberately does not add a new
  `RootCause` enum value to `domain/variance.py`** — a reversal (CLP02=22)
  produces one offsetting `Finding` per prior finding it reverses, same
  `root_cause` and `procedure_code`, `shortfall` negated, with the
  free-text `evidence` field explaining what it reverses. This keeps
  Phase 1's already-gated files untouched; reversal-netting is treated as
  an ingestion-time event, not a new classification. Fully tested: 6 tests
  covering quarantine (both "no transactions" and "transactions but zero
  claims"), partial-batch, reversal-netting-to-exactly-zero, determinism
  (same content -> equal plan, the pure proxy for "same file 3x -> identical
  totals"), and "no effective contract skips the claim, doesn't fail the
  batch."
- `src/ingestion/apply.py` — thin: `apply_ingestion_plan(session, tenant_id,
  plan, *, remittance_id, actor, contract_version_ids)`. Turns a
  `FileIngestionPlan` into DB writes via `db.repository` (below). Findings
  referencing a line index the claim doesn't actually have (e.g. a reversal
  reporting fewer lines than what it reverses) are dropped rather than
  raising — one bad correlation must not fail the batch. `contract_version_ids:
  Mapping[tuple[payer_id, effective_from], UUID]` exists because the pure
  domain `ContractVersion` dataclass deliberately carries no DB identity;
  `pipeline.py` builds this map from `repository.list_contract_versions`'s
  now-paired return value when it fetches contract data.
- `src/ingestion/pipeline.py` — `ingest_file(session, tenant_id, *, content,
  source, uploaded_by, scanner) -> IngestionOutcome | DuplicateOutcome`, the
  only place in `ingestion/` doing DB I/O orchestration: hash -> dedupe
  (`record_remittance_if_new` first, before any parsing work) -> virus scan
  -> UTF-8 decode -> `parse_835` (Phase 1) -> fetch contract versions and
  prior findings for referenced payers/control numbers -> `build_ingestion_plan`
  (pure) -> `apply_ingestion_plan`.
- `src/db/repository.py` additions (Phase 3 file, additive only — these
  functions genuinely didn't exist before, confirmed by grep before writing
  them): `create_service_line`, `create_adjustment`,
  `update_remittance_status`, `list_contract_versions` (now returns
  `list[tuple[UUID, ContractVersion]]`, paired with row id for the
  `contract_version_ids` map above), `list_findings_by_payer_claim_control_number`.
- `src/db/models.py` / `alembic/versions/0002_remittance_quarantine_reason.py`
  — added `remittances.quarantine_reason: Text NULLABLE` so "quarantined
  with a useful message" is actually queryable, not just implied by
  `status="quarantined"`. Verified via `alembic upgrade head --sql`
  (offline), same ceiling as migration 0001.
- `tests/ingestion/` — `fixtures.py` (contract-version builder + a
  BPR-total-parameterized 835 envelope, both built on top of
  `tests/domain/fixtures_x835.py`'s existing builders rather than
  duplicating them) and `conftest.py` (DB-backed test skip pattern, copied
  from `tests/db/conftest.py` since conftest discovery doesn't cross
  sibling directories, plus a `seed_tenant_with_contract` helper).
  `test_apply_idempotency.py`, `test_apply_quarantine.py`,
  `test_apply_audit_entry.py` exercise `pipeline.ingest_file` end to end
  against Postgres — written, skip cleanly without `TEST_DATABASE_URL`,
  **never executed**, same honest status as `tests/db/`.

RBAC note (deliberate, not a gap): `security.rbac.Action.UPLOAD_REMITTANCE`
already exists but enforcing it is endpoint-level — there's no API yet
(Phase 6). `pipeline.ingest_file()` does not check roles itself.

## Failing

Nothing. `pytest -q` → 234 passed, 14 skipped (11 from `tests/db/`, 3 new
from `tests/ingestion/`'s DB-backed tests — same honest skip, not broken).
`mypy --strict .` and `ruff check .` clean across 71 source files.
`python -m evals.run` → still 100%/100%/100%/100%, gate passed, unaffected
by this phase. `bandit -r . -x ./tests,./evals` clean (one `B101
assert_used` finding was raised and fixed during this session — see
Decisions below, not a leftover). Branch coverage on `domain/variance.py`
still 100% (Phase 5 didn't touch domain files).

## Decisions worth knowing (not obvious from the code)

- **Bandit caught a real bug during this session, not just a style
  complaint.** `apply.py`'s claim-persistence helper originally used
  `assert claim_plan.date_of_service is not None` to satisfy mypy after
  `plan.py` already filtered out claims with no derivable date. Bandit's
  `B101` flagged it: `assert` is stripped under `python -O`, so a
  production run with optimization enabled could have silently passed
  `None` into `claims.date_of_service`, a `NOT NULL` column. Replaced with
  an explicit `if ... is None: raise ValueError(...)`. If you see a bare
  `assert` guarding a DB write anywhere else in this codebase, it's
  probably the same class of bug — check whether `-O` would strip
  something load-bearing.
- **Reversal/takeback netting does not add a new `domain.variance.RootCause`
  value.** See `src/ingestion/plan.py`'s `_reverse_finding` — reuses the
  original finding's `root_cause`, negates the `shortfall`, and explains
  itself in `evidence`. Reopening `domain/variance.py` (Phase 1, already
  gated) for a one-phase-later feature felt like the wrong tradeoff; the
  free-text `evidence` field exists for exactly this. If a future phase
  needs to query "how many findings were reversals" cheaply without
  parsing `evidence` text, that's the point to revisit this, not before.
- **Payer identification falls back from `Entity.id_code` to `Entity.name`.**
  `src/ingestion/plan.py`'s `payer_key()`. Real 835s carry a payer id in
  N1*PR's fourth element (N104), but it's genuinely optional in the X12
  spec and every fixture in `tests/domain/fixtures_x835.py` omits it
  (`N1*PR*TEST PAYER` only — two elements). Discovered this by reading the
  parser's N1 handling directly before writing `_payer_key`, not by
  guessing. If real payer feeds are inconsistent about supplying N104,
  this fallback is required, not optional; don't remove it as
  "simplification."
- **`domain.contract.ContractVersion` carries no DB identity, on purpose**
  (Phase 1 stays pure). Ingestion needs the DB id anyway, to stamp
  `findings.contract_version_id` for traceability ("which contract were we
  paid against" matters for appeals). Solved with a `(payer_id,
  effective_from)` natural-key map built in `pipeline.py` from
  `repository.list_contract_versions`'s now-paired
  `list[tuple[UUID, ContractVersion]]` return value, threaded through
  `apply.py` as `contract_version_ids`. If a payer ever has two contract
  versions with the same `effective_from` (shouldn't happen, but not
  enforced by a DB constraint), this map silently keeps the last one seen
  — not currently guarded against.
- **Implant invoice cost is always `None` in ingestion-built
  `ClaimLineInput`s.** The 835 doesn't carry invoice cost — that comes from
  a separate purchasing/AP feed this phase doesn't have. Implant lines will
  price as `UNPRICED` until a future phase wires that feed in. This is
  existing Phase 1 behavior (`_is_implant` + no `invoice_cost` -> UNPRICED),
  not a new gap introduced here — just flagging it stays true through
  ingestion.
- **`tests/ingestion/conftest.py` duplicates `tests/db/conftest.py`'s
  skip-guard fixture rather than sharing it.** pytest conftest discovery
  only walks up the directory tree (a test file sees its own directory's
  conftest plus every ancestor's, never a sibling's), so `tests/ingestion/`
  can't use fixtures defined in `tests/db/conftest.py` without either a
  shared `tests/conftest.py` (didn't exist, chose not to introduce one
  mid-phase touching Phase 3's test setup) or duplication. Chose
  duplication, matching how Phase 3 itself was self-contained.

## Traps for someone resuming cold

- Everything from the Phase 3/4 checkpoint still applies: no
  project-local virtualenv (global Python 3.12 install), `git add -A` on
  Windows warns about LF→CRLF (harmless), `evals/golden/cases.py` is
  ruff-excluded and generated (never hand-edit).
- **`src/db/repository.list_contract_versions`'s return type changed** from
  `list[ContractVersion]` to `list[tuple[uuid.UUID, ContractVersion]]` this
  session — it had exactly zero callers before Phase 5 (grep-confirmed), so
  this wasn't a breaking change to anything real, but if a stale mental
  model of its old signature shows up anywhere, that's why.
- **`tests/ingestion/fixtures.py`'s `reconciling_835()` is not one of
  `tests/domain/fixtures_x835.py`'s builders** — it's a Phase 5 fixture
  that reuses those builders' pieces (`claim_segments`, `plb_segment`,
  `envelope_tail`, `seg`) but assembles its own envelope head, because none
  of the existing fixtures parameterize the BPR total (it's hardcoded
  `"500.00"` in `envelope_head()`), and BPR reconciliation testing needs to
  control that value directly.

## Next 3 steps

1. **If Postgres becomes available:** run `docs/DB_SETUP.md`'s steps, then
   `TEST_DATABASE_URL=... pytest tests/db/ tests/ingestion/ -v`. If all of
   Phase 3's four gate tests AND Phase 5's three (`test_apply_idempotency`,
   `test_apply_quarantine`, `test_apply_audit_entry`) pass, check off both
   phases in `docs/PHASES.md` — don't check off either for any lesser
   reason.
2. **Otherwise, start Phase 6 (API layer)** per
   `docs/MASTER-BUILD-PROMPT.md`: FastAPI endpoints for upload/list
   findings/finding detail/export/contract management/audit query, full
   authz test matrix (every role x every endpoint x own-tenant/other-tenant).
   This is where `security.rbac.Action.UPLOAD_REMITTANCE` finally gets
   wired in front of `ingestion.pipeline.ingest_file`. Enter plan mode
   first, same as every prior phase.
3. Either way, keep this checkpoint current — don't let a future session
   inherit a stale picture of what's verified vs. just written.
