# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 9 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 9/23** (F-01 through F-09 —
see below). 14 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-07** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`, `5f8d462`, `cf6c3e4`, `f118ccf`) and
  prior checkpoints for detail; unchanged this session.
- **F-08 + F-09 (HIGH, this session)** — Phase 8 built real OTel metrics
  and tracing, both fully unit-tested, but `main.py`'s
  `PostgresRepository(...)` construction never passed `instruments=` or
  `tracer=`, and `setup_tracing`'s returned `Tracer` was discarded
  outright. A real deploy would look instrumented while every ingestion
  metric and the one span in the codebase silently went to a no-op
  provider. Both findings share this one fix site (the register says so
  explicitly), fixed together like F-04/F-05. Commit `eafb9ca` (register
  updated to FIXED in `b32f30e`):
  - `observability/tracing.py` — `setup_tracing` gained
    `set_global: bool = False`. When `True`, registers the constructed
    provider as the process-wide `TracerProvider` via
    `opentelemetry.trace.set_tracer_provider`. Every span this codebase
    actually creates already gets its `Tracer` via explicit DI
    (`ingestion.pipeline.ingest_file`'s `tracer` parameter, never the
    ambient global), so nothing *functionally* depended on this — it's
    still real, standard OTel production wiring for anything future (or
    an auto-instrumentation library) that reaches for the global tracer
    instead of explicit DI. Defaults to `False` specifically so the
    function stays safely callable more than once per test session: the
    OTel API only honors the first-ever `set_tracer_provider` call in a
    process and logs a warning on every call after that.
  - `main.py` — captures `setup_tracing`'s return value (previously
    thrown away entirely), passes `set_global=True`, and threads both
    `tracer=` and `instruments=` into the `PostgresRepository(...)`
    construction that was missing them.
  - New tests: `tests/observability/test_tracing.py` proves
    `set_global=True` actually registers a real SDK `TracerProvider`
    process-wide (checks `isinstance(trace.get_tracer_provider(),
    TracerProvider)`, not identity against this call's own provider —
    robust regardless of test execution order, since the OTel API only
    lets the *first* registration in the process actually win).
    `tests/test_main.py` proves the env-wired app's repository carries
    non-None `_instruments`/`_tracer` — the literal regression this
    fixes. The deeper "a span survives the PHI-scrubbing exporter given
    real instrumentation" behavior already had DB-backed coverage
    (`tests/ingestion/test_pipeline_observability_live_db.py`,
    pre-existing, untouched by this change) — what was missing was proof
    that `main.py` actually *supplies* that instrumentation in the first
    place, which is what's new here. Deliberately did not add a new
    end-to-end DB-backed test threading `create_app_from_env()` through
    an HTTP upload down to a captured span — that would just re-prove
    what the pre-existing ingestion-level test and the new wiring-level
    test already separately cover, for no new signal.

This is the second Wave 3 fix (after F-06) with a fully, genuinely
verified local gate and no Terraform/DB-only piece left unexecuted —
`set_global`'s behavior and the repository-construction wiring are both
pure Python, provably correct without a live Postgres.

## In progress

Nothing mid-write. F-08/F-09 went through the full Wave 3 loop (state the
findings → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA →
commit) and is complete as a unit, same as every prior fix.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(153 files), `pytest -q` (442 passed, 29 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `b32f30e`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely (confirmed via `git stash` three sessions ago). Still not
blocking, still out of scope for this checkpoint — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **`set_global` defaults to `False`, not `True`.** The tempting
  alternative was making global registration unconditional inside
  `setup_tracing` — simpler call site, one less parameter. Rejected:
  `tests/observability/test_tracing.py` calls `setup_tracing(exporter)`
  three separate times across existing tests in one pytest session; an
  unconditional `set_tracer_provider` call would make the 2nd and 3rd
  calls each log an OTel warning ("Overriding of current TracerProvider
  is not allowed") on every future test run, forever, for no benefit —
  none of those tests need or check the global registry, they use the
  returned `Tracer` directly. Opt-in via a flag keeps the pure/testable
  default behavior unchanged and confines the global side effect to
  exactly the one real caller (`main.py`) that needs it — consistent
  with this codebase's general discipline of keeping side effects at the
  composition root.
- **No new DB-backed test was added for F-08/F-09**, unlike most prior
  Terraform/DB-touching Wave 3 fixes. Deliberate, not an oversight: the
  actual regression here (main.py discarding tracer/instruments) is pure
  Python composition-root wiring, fully provable without Postgres — the
  DB-touching part of "does a span survive real ingestion" was already
  covered by a pre-existing test
  (`tests/ingestion/test_pipeline_observability_live_db.py`) that this
  fix doesn't change or depend on. Adding a redundant end-to-end version
  would have tested the same two facts twice instead of adding new
  confidence.
- **`isinstance(trace.get_tracer_provider(), TracerProvider)` was chosen
  over an identity check** in the new tracing test specifically because
  the global OTel tracer-provider registry is process-wide, mutable,
  write-once state — asserting "this is *my* specific provider object"
  would make the test's correctness depend on whether it happens to run
  before any other test in the session that also registers one. The
  `isinstance` check is true under either ordering: once *any*
  `set_global=True` call succeeds anywhere in the process, the global
  provider is and remains SDK-backed for the rest of that process.

## Traps for someone resuming cold

- **Everything F-01 through F-07's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, and re-reading a finding's *full* row — not just
  its "Fix" column — once more right before marking it FIXED).
- **`opentelemetry.trace.set_tracer_provider` is a write-once global per
  process.** If a future session adds another `set_global=True` call
  site anywhere (a second composition root, a script, a notebook), only
  the first one to execute in that process actually takes effect; every
  later attempt is silently downgraded to a no-op with a logged warning.
  This is standard, documented OTel API behavior, not a bug in this
  codebase's wrapper — but it's easy to be surprised by if you don't
  already know the rule going in.

## Next 3 steps

1. **F-10 next** per `REGISTER.md`'s own listed order — PHI log scrubbing
   is per-logger opt-in (`security/redaction.py`'s `PHIRedactionFilter`
   is attached exactly once, in `api/errors.py`; any other logger,
   including `api.request` in `api/request_context.py`, doesn't have it
   and would leak PHI into its log lines unfiltered). The register's own
   suggested fix is a single `logging.dictConfig` at startup installing
   the filter on the root logger's handlers, structurally rather than
   per-call-site — a fully offline-verifiable Python change (log via a
   brand-new arbitrary logger name in a test, confirm the sink is clean).
2. **After F-10**, continue F-11 through F-23 in `REGISTER.md`'s own
   listed order, same loop each time (state finding → test → fix → gate →
   mark FIXED with SHA → commit) — and keep re-reading each finding's
   full row once more right before marking it FIXED (see F-07's near-miss
   two checkpoints ago).
3. **Once the MUST-FIX list is meaningfully further along**, update
   `docs/PHASES.md` to note Wave-3 remediation progress and re-run the
   Wave 0 baseline commands fresh across the accumulated batch, before
   telling the user this phase of remediation is done. 9/23 is closer but
   still probably not there yet by itself; revisit after roughly F-12 or
   so lands, per the original plan.
