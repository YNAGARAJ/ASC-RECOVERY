# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint (about to commit "Phase 8:
observability and audit"). Read `docs/PHASES.md` first for the phase
checklist — this file adds the texture that isn't in that summary.

## Phase: 8 code-complete, one DB-backed test unverified. Same story as 3, 5, 6, 7.

Phases 0–2 and 4 are fully done. Phases 3, 5, 6, 7, and 8 each have an
honest gap: this machine has no Docker, no WSL, and no local Postgres —
only `pip` works (re-checked again this session; unchanged). Unusually,
Phase 8's gap is the *smallest* of the five — most of this phase's actual
substance (instrumentation, alert-evaluation logic) is inherently pure,
so only one new test is genuinely DB-gated.

Current phase per `docs/PHASES.md`: **Phase 8 — Observability and
audit**, code-complete, both literal gate requirements verified without a
live database, one round-trip test unverified.

## Done (this session, Phase 8)

Exploration confirmed this was greenfield: no OpenTelemetry dependency,
no tracing/metrics code anywhere, `phi_access_log` had a model but zero
writers/readers, `evals/run.py` only printed to stdout, and
`AnthropicPacketDrafter` discarded `response.usage` entirely.

**`src/observability/`** (new package):
- `tracing.py` — `PHIScrubbingSpanExporter` wraps any real `SpanExporter`
  (decorator pattern, same shape as every other adapter in this codebase)
  and scrubs span attributes before export, reusing
  `security.redaction.PHI_FIELD_NAMES`/`scrub_text` (promoted from
  private to public in that module specifically so this wouldn't
  duplicate the same regex patterns in two places). **No auto-
  instrumentation anywhere** — `opentelemetry-instrumentation-fastapi`/
  `-sqlalchemy` can capture raw SQL or route params as span attributes,
  an uncontrolled leak surface; every span in this codebase is created
  manually with attributes the call site explicitly chooses.
  `tests/observability/test_tracing.py` is **the Phase 8 gate test**:
  builds a span with a deliberately PHI-shaped attribute (name, member
  id, an SSN-shaped string assembled at runtime via `"-".join(...)` so
  the test fixture itself doesn't trip `scripts/hooks/block_phi.sh`),
  exports it through the wrapper via OTel's own `InMemorySpanExporter`,
  and asserts none of it survived.
- `metrics.py` — `dollars_detected`, `findings_per_remittance`,
  `ingestion_latency`, `ingestion_failures`, `llm_cost_per_packet` (all
  wired into real code, not just defined), plus a documented `queue_depth`
  stub (ingestion is synchronous, no queue exists to measure). Dollar
  amounts cross into `float` only at this telemetry boundary — a narrow,
  documented exception to CLAUDE.md rule 2, since OTel's instrument API
  has no `Decimal` support and a metrics backend stores float64 anyway;
  the system of record (`domain`, `db`) never touches `float`.
  `noop_instruments()` is the default everywhere so instrumentation is
  additive — no existing caller broke.
- `alerts.py` — 5 pure evaluator functions, one per alert the Phase 8
  prompt names (ingestion failure rate, eval regression, auth anomaly,
  unusual PHI access volume, cross-tenant probe). All take simple typed
  inputs (counts/thresholds/snapshots) and return `Alert | None`. Wiring
  to a real paging service (PagerDuty/Slack/email) is deliberately
  deferred to Phase 9/10 — same deferral pattern as real cloud KMS
  adapters. The cross-tenant-probe evaluator's docstring explains why
  cross-tenant lookups don't raise a distinguishable error by design
  (Phase 6): a burst of 404s on direct-id lookups from one actor is the
  only observable proxy signal, and it's worth surfacing regardless of
  whether it's probing or a client bug.

**`evals/history.py`** — JSONL-based eval run history (`evals/history/runs.jsonl`,
gitignored — a rolling local log, not meaningful shared history across
machines), not a DB table: eval runs are a build/CI signal, not
tenant-scoped customer data, so `make eval` stays Postgres-independent.
`detect_eval_regression` reuses `observability.alerts.evaluate_eval_regression_alert`
rather than duplicating the threshold logic. `evals/run.py`'s `main()` now
appends a record after every run and prints a regression alert if one
fires.

**`src/db/access_history.py`** (pure) — `AccessEvent` + `merge_access_history`,
the chronological sort/merge at the heart of the auditor report. Kept
decoupled from ORM models on purpose (same reasoning as Phase 5's
`PriorFinding`): `db.repository.get_claim_access_history` does the actual
multi-table query composition (a claim's finding_ids and packet_ids
resolved first, since they only reference the claim indirectly, then
`audit_log` queried across all three resource types plus `phi_access_log`
directly) and converts rows to `AccessEvent` before calling the pure
merge. New repository functions: `write_phi_access_log`,
`list_audit_log_by_resource_ids`, `get_claim_access_history`.

**`phi_access_log` finally has writers.** Wired into every API read path
that returns patient-identifying data:
`api.repository.PostgresRepository.get_finding_detail` (patient
name/member id), `list_packets` and `generate_packet` (draft_text
contains identifying details post-substitution). Both `get_finding_detail`
and `list_packets`'s `Repository` Protocol signatures gained a required
`actor: str` keyword arg to make this possible — route call sites in
`findings.py`/`packets.py` updated to pass `ctx.user_id`.

**New API endpoint**: `GET /claims/{claim_id}/access-history`
(`Action.READ_PHI_ACCESS_LOG`, already AUDITOR/ADMIN-only from Phase 4 —
no rbac change needed this phase). Added to the same authz-matrix
discipline as every Phase 6/7 endpoint —
`tests/api/test_authz_matrix.py` now has 48 cases (was 44), including the
cross-tenant proof: asking about another tenant's claim returns an empty
list, not a 404 (a 404 would confirm/deny the claim's existence
asymmetrically; empty-list is the same shape for "wrong tenant" and
"doesn't exist").

**Ingestion instrumentation**: `ingestion.pipeline.ingest_file` is now a
thin tracing/metrics wrapper (`_ingest_file_impl` holds the original,
untouched logic) — optional `tracer`/`instruments` keyword args default
to no-ops, so every test written before this phase kept working
unchanged. `ingestion.apply.IngestionOutcome` gained a `dollars_detected: Decimal`
field (sum of newly persisted findings' shortfalls), threaded through
from `_apply_claim`'s return value (changed from `int` count to
`Sequence[Finding]` so the caller can sum shortfalls itself).
`tests/ingestion/test_pipeline_observability_live_db.py` (DB-backed, skip
pattern) proves the wrapper emits a real scrubbed span and real metric
data points when given real instrumentation — the existing idempotency/
quarantine/audit tests in that directory all use the no-op defaults, so
none of them previously exercised this wiring.

**LLM cost tracking**: `packets.drafter.AnthropicPacketDrafter.draft()`
now captures `response.usage` (previously discarded entirely) and records
`llm_cost_per_packet` via a new pure `estimate_cost(model, input_tokens,
output_tokens) -> Decimal` function (small per-model pricing table,
labeled "illustrative rates, confirm before this feeds a real budget
decision"). `estimate_cost` is fully unit tested even though
`AnthropicPacketDrafter` itself still isn't (no API key in this
environment, same deferral as ever).

## Failing

Nothing. `pytest -q` → **365 passed, 19 skipped** (11 `tests/db/`, 4
`tests/ingestion/` — one new observability test, 4 `tests/api/test_endpoints_live_db.py` —
one new access-history test — all honest skips). `mypy --strict .` and
`ruff check .` clean across **128 source files** (was 115). `python -m
evals.run` → 100%/100%/100%/100%, now also appends to
`evals/history/runs.jsonl`. `bandit -r . -x ./tests,./evals` clean.
Branch coverage on `domain/variance.py` still 100%. `alembic upgrade head
--sql` clean through 0004 (no new migration this phase — `phi_access_log`'s
table already existed from Phase 3, just needed writers).

## Decisions worth knowing (not obvious from the code)

- **`PHIScrubbingSpanExporter` rebuilds a fresh `ReadableSpan` rather than
  mutating SDK-internal attribute state.** Verified interactively before
  writing any code: `ReadableSpan.__init__` accepts every field
  (name/context/parent/resource/attributes/events/links/kind/status/
  start_time/end_time/instrumentation_scope) as a constructor argument, so
  a scrubbed copy can be built entirely from public properties. This
  avoids poking at `_attributes` (a documented community pattern for OTel
  redaction processors, but a private-API dependency this codebase didn't
  need to take on).
- **`security.redaction._scrub_text` was renamed to `scrub_text`
  (private -> public)** specifically so `observability.tracing` could
  reuse the exact same SSN/MBI regex patterns instead of a second,
  driftable copy. One-line rename, one internal call site updated,
  `tests/security/test_redaction.py` untouched and still green — this is
  the kind of small, justified edit to an already-gated file that's fine
  (contrast with *not* touching `domain.contract.ContractVersion` in
  Phase 7, where the blast radius would have been much larger).
- **Three business metrics were deliberately not built**:
  `dollars_recovered`, `recovery_rate_by_cause`, `time_to_recovery`. All
  three need outcome data (did the payer actually pay?) that doesn't
  exist anywhere in this schema — `docs/MASTER-BUILD-PROMPT.md`'s Phase
  12 explicitly frames the outcome feedback loop as future work. Building
  metric scaffolding around data that doesn't exist would be worse than
  naming the gap. If a future session is asked to "finish" these metrics,
  the honest answer is "Phase 12 has to land first, this isn't a
  Phase 8 oversight."
- **`ingestion.apply._apply_claim`'s return type changed from `int` to
  `Sequence[Finding]`.** Needed so `apply_ingestion_plan` could sum
  shortfalls for `dollars_detected` without a second query — the count
  Phase 5 needed is just `len()` of what Phase 8 needed anyway. No
  behavior change, pure refactor; every existing call site and test kept
  passing untouched.
- **`get_finding_detail` and `list_packets` gained a required `actor: str`
  kwarg** — a real, deliberate signature change to two Phase 6/7 Protocol
  methods, needed because `phi_access_log` requires knowing *who* accessed
  the data, and neither method previously had any notion of an actor
  (only `generate_packet`/`decide_packet` did, since those already wrote
  to `audit_log`). Every call site (routes, `FakeRepository`,
  `PostgresRepository`) updated together; nothing left half-migrated.
- **Cross-tenant claim-history lookups return an empty list, not a 404.**
  Consistent with Phase 6's "no endpoint ever confirms another tenant's
  resource exists" principle — a 404 on `/claims/{other_tenant_claim_id}/access-history`
  would be a tell (this endpoint distinguishes "doesn't exist" from
  "not yours" nowhere else either). `get_claim_access_history`'s DB
  queries are simply tenant-scoped from the start, so a wrong-tenant
  claim id naturally returns zero rows, same shape as a nonexistent one.

## Traps for someone resuming cold

- Everything from the Phase 3/4/5/6/7 checkpoints still applies (no
  project-local virtualenv, CRLF warnings on `git add`, generated/ruff-
  excluded `evals/golden/cases.py`, SSN-shaped test fixtures must be
  assembled at runtime with `"-".join(...)` or `block_phi.sh` blocks the
  write).
- **`evals/history/` is gitignored** — don't be alarmed that
  `runs.jsonl` doesn't show up in `git status` after running `make eval`
  or the test suite; that's intentional, not a missing-file bug. Every
  `pytest -q` run that exercises `tests/evals/test_run.py::test_main_passes_against_the_real_golden_dataset`
  appends a row to it as a side effect — harmless, local-only.
- **`ingestion.pipeline.ingest_file`'s public signature grew two optional
  kwargs** (`tracer`, `instruments`) but its *behavior* for existing
  callers is bit-for-bit unchanged (no-ops by default). Don't mistake the
  wrapper/`_ingest_file_impl` split for a logic change — it's purely
  structural, done to add instrumentation without touching the tested
  inner function body at all.
- **`observability` has no dependency on `evals`, and `evals.history`
  imports from `observability.alerts` (not the other way around).**
  `evals/` is evaluation tooling that depends on `src/`; keep it that
  direction if extending either module.

## Next 3 steps

1. **If Postgres becomes available:** run `docs/DB_SETUP.md`'s steps,
   then `TEST_DATABASE_URL=... pytest tests/db/ tests/ingestion/
   tests/api/ -v`. If Phase 3's four gate tests, Phase 5's three, Phase
   6's two, Phase 7's one, and Phase 8's two
   (`test_ingest_file_emits_a_scrubbed_span_and_real_metrics`,
   `test_viewing_a_finding_shows_up_in_its_claim_access_history`) all
   pass, check off all five phases in `docs/PHASES.md` — don't check off
   any of them for a lesser reason.
2. **Otherwise, start Phase 9 (cloud-agnostic deployment)** per
   `docs/MASTER-BUILD-PROMPT.md`: multi-stage Dockerfile (non-root,
   pinned digests), health/readiness/liveness endpoints, Terraform
   modules with a provider-agnostic core (only BAA-eligible services per
   cloud), network segmentation, tested backup/restore, zero-downtime
   migration strategy, `docs/RUNBOOK.md`. This is also where every
   deferred "real adapter" from Phases 4/5/7/8 (cloud KMS, real AV, real
   OTLP exporter to a real tracing backend, real paging integration for
   `observability.alerts`) either gets wired for real or explicitly stays
   deferred with a stated reason. Enter plan mode first, same as every
   prior phase.
3. Either way, keep this checkpoint current — don't let a future session
   inherit a stale picture of what's verified vs. just written.
