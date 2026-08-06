# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 10 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 10/23** (F-01 through F-10 —
see below). 13 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-09** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`, `5f8d462`, `cf6c3e4`, `f118ccf`,
  `eafb9ca`) and prior checkpoints for detail; unchanged this session.
- **F-10 (HIGH, this session)** — exactly one `addFilter(PHIRedactionFilter())`
  call existed anywhere in `src/` (`api/errors.py`'s own logger), and no
  logging bootstrap existed at all. `api.request`'s logger
  (`api/request_context.py`) already had no filter; any future module's
  logger would silently start unredacted too by construction — Python
  only ever consults a `Logger`'s own filters for calls made directly on
  it, propagation to ancestor loggers walks their *handlers*, not their
  filters, so per-logger attachment can never be structural on its own.
  Commit `d299b4a` (register updated to FIXED in `62a68a6`):
  - `observability/logging_config.py` (new) — `configure_logging()`
    attaches `PHIRedactionFilter` to a handler on the ROOT logger
    instead of any one named logger. A root handler receives every
    propagated record from every logger in the process by construction
    — no per-module opt-in required, present or future. Also sets the
    root level to `INFO`, which fixes a related, previously-unnoticed
    side effect discovered while implementing this: `api.request`'s
    `"request started"` INFO log was being silently dropped by Python's
    default `WARNING` root level even before considering redaction —
    with no root logger configured at all, that line has likely never
    actually appeared anywhere in a real deployment.
  - `main.py` — calls `configure_logging()` first thing in
    `create_app_from_env()`, before anything else has a chance to log a
    line not yet covered by any filter.
  - `api/errors.py`'s own direct `addFilter` call was deliberately left
    in place, not removed — redundant once the root handler exists, kept
    anyway as a fail-safe that doesn't depend on `configure_logging`
    having run first (true for most of this test suite, which builds
    apps via `api.app.create_app` directly, bypassing `main.py`'s
    composition root entirely).
  - New `tests/observability/test_logging_config.py` (3 cases): the
    filter is actually attached to a root handler; a logger name used
    nowhere else in this codebase, given no filter of its own, still
    gets scrubbed (the actual F-10 proof); INFO records actually reach
    the handler instead of being silently dropped.

Third Wave 3 fix in a row (after F-06, F-08/F-09) with a fully, genuinely
verified local gate and zero unexecuted Terraform/DB piece — pure Python,
provably correct without a live Postgres.

## In progress

Nothing mid-write. F-10 went through the full Wave 3 loop (state the
finding → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA →
commit) and is complete as a unit, same as every prior fix.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(155 files), `pytest -q` (445 passed, 29 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `62a68a6`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely (confirmed via `git stash` four sessions ago). Still not
blocking, still out of scope for this checkpoint — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **Used plain imperative `logging` calls (`root.addHandler(...)`), not
  literally `logging.config.dictConfig`**, even though the register's
  fix text suggested `dictConfig` specifically. The actual requirement
  is "install structurally, on the root logger's handlers" — `dictConfig`
  is one way to express that, but a plain `logging.StreamHandler()` +
  `addFilter()` + `root.addHandler()` achieves the identical structural
  guarantee with less ceremony and, importantly, is much easier to make
  directly testable (inject a `stream=io.StringIO()` and inspect it)
  than unwinding a nested config dict in a test. Judgment call, not a
  deviation from the finding's actual intent.
- **No idempotency guard on `configure_logging()`** — calling it twice
  adds two independent root handlers rather than detecting and skipping
  a second call. Considered a guard (module-level flag, or scanning
  `root.handlers` for a marker attribute) and rejected it: this
  codebase's existing `setup_tracing`/`setup_metrics` already behave the
  same way on repeated calls (each builds a fresh, independent
  provider), so matching that precedent keeps the three startup-wiring
  functions consistent. It also sidesteps a real testability trap — an
  idempotent version would silently ignore a second call's `stream=`
  override, which would have made `tests/observability/test_logging_config.py`
  order-dependent on whichever test happened to call it first in the
  session. Real production impact is nil: `main.py`'s composition root
  only calls it once per process.
- **`api/errors.py`'s own `addFilter` call was kept, not removed**, even
  though it's now redundant given the root handler. This is a deliberate
  belt-and-suspenders choice for a CLAUDE.md rule-6 control ("assume
  every log line will be read by someone unauthorized") rather than
  cleanup-for-its-own-sake: it's also the only redaction guarantee that
  holds for any test or script that constructs an app via
  `api.app.create_app` directly (most of this test suite) without ever
  routing through `main.py`'s `configure_logging()` call. Removing it
  would have been a real, if narrow, regression for that path.

## Traps for someone resuming cold

- **Everything F-01 through F-09's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, OTel's global-provider write-once-per-process
  rule, and re-reading a finding's *full* row — not just its "Fix"
  column — once more right before marking it FIXED).
- **Most of this test suite never calls `main.py`'s `create_app_from_env()`**
  — it builds a `FastAPI` app via `api.app.create_app(repository=...,
  jwt_secret_key=...)` directly (see `tests/api/conftest.py`'s `app`
  fixture), which means `configure_logging()` never runs for the vast
  majority of API-layer tests. Any future test that wants to assert on
  *structural* root-level log redaction specifically needs to call
  `configure_logging()` itself first (see
  `tests/observability/test_logging_config.py` for the pattern) — it
  will not happen implicitly just because a `TestClient` request was
  made through a normal `app`/`client` fixture.

## Next 3 steps

1. **F-11 next** per `REGISTER.md`'s own listed order — 4 of 5 alert
   evaluators (`observability/alerts.py`: ingestion failure, auth
   anomaly, unusual PHI access, cross-tenant probe) have no runtime call
   site; only `eval_regression` runs, and only via offline `make eval`.
   Larger than F-08/F-09/F-10 (register rates it L effort) — needs a
   scheduled evaluator or a hook into the request/ingestion paths feeding
   real counts to each evaluator, dispatching to a notification port.
   Worth its own focused pass rather than a quick one alongside smaller
   fixes, similar to how F-04/F-05 got flagged as the largest remaining
   item back when this was a 23-item list with nothing closed yet.
2. **After F-11**, continue F-12 through F-23 in `REGISTER.md`'s own
   listed order, same loop each time (state finding → test → fix → gate →
   mark FIXED with SHA → commit) — and keep re-reading each finding's
   full row once more right before marking it FIXED (see F-07's
   near-miss, several checkpoints ago now).
3. **Once the MUST-FIX list is meaningfully further along**, update
   `docs/PHASES.md` to note Wave-3 remediation progress and re-run the
   Wave 0 baseline commands fresh across the accumulated batch, before
   telling the user this phase of remediation is done. 10/23 is real
   progress but this is judgment, not a hard threshold — revisit after
   F-11 (and whatever's quick to fold in around it) lands.
