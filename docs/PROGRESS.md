# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 11 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 11/23** (F-01 through F-11 —
see below). 12 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).
Half of the MUST-FIX list is now closed.

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-10** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`, `5f8d462`, `cf6c3e4`, `f118ccf`,
  `eafb9ca`, `d299b4a`) and prior checkpoints for detail; unchanged this
  session.
- **F-11 (HIGH, this session)** — the largest single fix since F-04/F-05
  (register rated it L effort, the only other L-effort item besides that
  one). `observability/alerts.py`'s five evaluators were fully built and
  unit-tested since Phase 8; only `eval_regression` ever ran, offline via
  `make eval`. Ingestion failure, auth anomaly, unusual PHI access, and
  cross-tenant probe had zero runtime call sites — a real cross-tenant
  probe against this system would have fired nothing. Commit `37748f7`
  (register updated to FIXED in `87dd8b5`):
  - **Design choice**: hook-based wiring (evaluate + dispatch inline at
    the relevant call site), not a scheduled evaluator. No scheduler
    infrastructure exists anywhere in this codebase (no APScheduler,
    Celery, cron), and every other cross-cutting concern here (rate
    limiting via a FastAPI router dependency, audit logging via explicit
    repository calls) is already wired this same way. The register's fix
    text explicitly allowed either ("a scheduled evaluator (**or** hook
    into request/ingestion paths)").
  - `observability/notifications.py` (new) — `NotificationPort` Protocol
    + `LoggingNotificationPort`, the real-but-unpaging default until a
    real vendor (PagerDuty/Slack/email) exists, same deferral pattern as
    every other real-vendor integration in this codebase (real cloud KMS
    adapters, the real LLM provider, a real antivirus engine).
  - `observability/alert_state.py` (new) — `RollingWindowCounter`
    (generic, per-key hit count within a trailing time window — an
    ever-growing lifetime total would trip a threshold exactly once,
    forever, the moment it's ever crossed) and `IngestionOutcomeTracker`
    (a matched quarantined/total pair of these). Same in-memory/
    single-process scope note `security.rate_limit`'s existing trackers
    already carry.
  - `security/rate_limit.py` — `AccountLockoutTracker` gained
    `current_failure_count()`. Auth-anomaly reuses this tracker's own
    bookkeeping instead of adding a second, parallel counter for the
    same concept.
  - `api/routes/auth.py` — evaluates auth-anomaly after every failed
    login attempt, fed by the count above.
  - `api/alerting.py` (new) + `api/routes/{findings,packets}.py` — every
    direct-id-lookup route that can 404 (`get_finding`, `record_outcome`,
    `generate_packet`, `approve_packet`, `reject_packet`) now records the
    miss and evaluates cross-tenant-probe, keyed by actor, via a tracker
    on `app.state`.
  - `api/repository.py` — `PostgresRepository` gained an optional
    `notifier` param (same "additive, never required" contract
    `tracer`/`instruments` already have) plus two internal trackers.
    Evaluates unusual-PHI-access after every `write_phi_access_log` call
    (all 3 call sites: finding detail, packet generation, packet list)
    and ingestion-failure-rate after every non-duplicate ingestion.
  - `api/app.py` — `app.state.notifier` / `app.state.not_found_tracker`,
    same fresh-per-call / overridable-for-tests pattern `rate_limiter`
    and `lockout_tracker` already use.
  - `main.py` — one shared notifier instance passed to both the
    repository and the app, so DB-triggered alerts (PHI access,
    ingestion failure) and route-triggered alerts (auth anomaly,
    cross-tenant probe) dispatch through the same real adapter.
  - New tests: `tests/observability/test_alert_state.py` (8 cases),
    `tests/observability/test_notifications.py` (3 cases),
    `tests/api/test_alert_wiring.py` (5 cases — auth-anomaly and
    cross-tenant-probe, fully testable via `FakeRepository`/`TestClient`,
    no DB needed, since both live at the route layer).
    `tests/api/test_alerts_live_db.py` (2 cases, DB-backed, skips locally
    same as every other live-Postgres test in this repo) covers the two
    alerts that live inside `PostgresRepository`'s real DB writes
    (PHI-access-volume, ingestion-failure-rate) — these genuinely cannot
    be proven without a live Postgres, since `FakeRepository` doesn't
    perform the same DB writes that trigger them.

## In progress

Nothing mid-write. F-11 went through the full Wave 3 loop (state the
finding → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA →
commit) and is complete as a unit, same as every prior fix.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(162 files), `pytest -q` (461 passed, 31 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `87dd8b5`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely (confirmed via `git stash` five sessions ago). Still not
blocking, still out of scope for this checkpoint — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **Alert evaluation for `unusual_phi_access`/`ingestion_failure_rate`
  lives in `api/repository.py` (`PostgresRepository`), not inside
  `ingestion/pipeline.py`'s `ingest_file`**, even though `record_ingestion_outcome`
  (metrics) already lives in `ingest_file` right next to the span
  creation. Deliberate: adding `notifier`/tracker params to `ingest_file`
  would have touched its already-large signature and every one of its
  many existing call sites/tests across `tests/ingestion/`. Evaluating in
  `PostgresRepository.ingest_remittance` instead (which already wraps
  `ingest_file`'s return value) achieves the same real-DB-write-triggered
  timing with zero changes to `ingestion/pipeline.py` or its test suite.
  If a future session wants perfect symmetry with the metrics/tracing
  wiring, that's a deliberate refactor to weigh, not an oversight here.
- **`RollingWindowCounter` uses wall-clock-style time windows, not a
  fixed count of recent events** (e.g. "last 20 ingestions"). A count-based
  window was considered for `IngestionOutcomeTracker` specifically (ingestion
  frequency varies wildly per tenant, so a fixed *time* window is either
  mostly-empty for a low-volume tenant or huge for a bursty one) but
  rejected for consistency: reusing one primitive across all four alerts
  (PHI access, cross-tenant probe, ingestion failure) is simpler than
  building and testing two different windowing strategies for a fix
  already this large.
- **`current_failure_count()` was added to the existing
  `AccountLockoutTracker` rather than building a separate auth-anomaly
  counter.** The tracker already keeps a consecutive-failure count per
  account (`_LoginAttempts.failures`) to decide lockout; auth-anomaly's
  threshold (5, matching `AccountLockoutTracker`'s own default
  `max_failures`) fires at the same point lockout does. Two independent
  counters tracking the identical thing would have been pure duplication
  for no behavioral difference.
- **PHI-access-volume and ingestion-failure-rate could only get
  DB-backed test coverage, not local coverage** — `FakeRepository`
  doesn't perform the real `write_phi_access_log`/`ingest_remittance` DB
  writes these two alerts hook into, so there's no way to exercise them
  without a live Postgres. This is the same ceiling every DB-touching
  Wave 3 fix has had since F-01; the new tests in
  `tests/api/test_alerts_live_db.py` are written and skip cleanly, never
  executed in this environment.
- **Every alert threshold (5 failed logins, 50 PHI accesses in 5
  minutes, 10 not-found responses in 5 minutes, 10% quarantine rate) is
  whatever `observability/alerts.py`'s own evaluator functions already
  defaulted to since Phase 8** — none were tuned or changed as part of
  this fix. F-11 is about wiring existing, already-reviewed detection
  logic to real call sites, not about picking new thresholds; retuning
  any of them is a separate, deliberate decision for whoever operates
  this for real.

## Traps for someone resuming cold

- **Everything F-01 through F-10's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, OTel's global-provider write-once-per-process
  rule, most of the test suite never calling `main.py`'s
  `create_app_from_env()` so `configure_logging()` never runs for it, and
  re-reading a finding's *full* row — not just its "Fix" column — once
  more right before marking it FIXED).
- **If a future finding adds another direct-id-lookup route that can
  404**, remember to call `api.alerting.record_not_found(request, ctx.user_id)`
  before raising the 404 — there's no central registry enforcing this,
  same class of easy-to-forget wiring `enforce_rate_limit` already
  warned about for new routers.
- **`PostgresRepository`'s constructor now takes five optional
  keyword-only params beyond `drafter`/`encryptor`** (`tracer`,
  `instruments`, `notifier` — plus the two trackers, which are NOT
  constructor params, always built internally). Anyone constructing one
  by hand (scripts, a new test) should check whether they actually need
  real observability wired in, or whether the all-`None` defaults
  (silently no-op) are fine for their purpose — same judgment call
  `tracer`/`instruments` already required before this session.

## Next 3 steps

1. **F-12 next** per `REGISTER.md`'s own listed order — `record_finding_outcome`
   (`api/repository.py:773-795`, `db/repository.py:565-589`) writes
   `findings.outcome`/`amount_recovered` (a PHI-bearing table, including a
   dollar amount) with no `write_audit_log` call — a direct CLAUDE.md rule
   5 violation ("every write to a PHI-bearing table goes through the audit
   log, no exceptions"). Small, S effort per the register, fully
   offline-verifiable with a test asserting the audit row appears (via
   `FakeRepository` or a light DB-backed check) — a good, quick fix to
   follow this session's largest one.
2. **After F-12**, continue F-13 through F-23 in `REGISTER.md`'s own
   listed order, same loop each time (state finding → test → fix → gate →
   mark FIXED with SHA → commit) — and keep re-reading each finding's
   full row once more right before marking it FIXED.
3. **The MUST-FIX list just crossed the halfway mark (11/23).** Per the
   original plan ("once the MUST-FIX list is meaningfully further
   along"), this is a reasonable point to consider updating
   `docs/PHASES.md` to note Wave-3 remediation progress and re-running
   the Wave 0 baseline commands fresh across the whole accumulated batch
   — worth raising with whoever's driving this next, even if the actual
   re-run doesn't happen until a few more findings land.
