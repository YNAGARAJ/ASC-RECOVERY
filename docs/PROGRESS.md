# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## IMPORTANT — two-stage work order for this repo, read before anything else

The user confirmed a **two-stage work order** (also saved as a project
memory, `project_roadmap_scope` — re-derive from here if it isn't in
context):

1. **Finish Wave 3 remediation first** — close `docs/audit/REGISTER.md`'s
   remaining MUST-FIX rows, in the register's own listed order.
2. **Then build the unbuilt product-completeness gaps** listed at the top
   of `docs/MASTER-BUILD-PROMPT-V2.md`'s "PART 3 — GAP REGISTER" —
   frontend, async jobs, lesser-of/stop-loss/prompt-pay-interest contract
   logic, SSO/SCIM, multi-org hierarchy, reprocessing, and more. Entirely
   unbuilt features, not bugs. Don't start this while register items
   remain open.

**Established pattern from F-17 and F-18, both this Wave**: not every
MUST-FIX row is a clean wiring fix. Some findings' "real" fix requires
inventing infrastructure/data storage that was never part of any of the
12 original phases (F-17's invoice-cost purchasing feed; F-18's
per-tenant SFTP/S3 config + an owned scheduler). **When a finding looks
like this, ask the user how to scope it before proceeding** — the two
resolutions so far: F-17 (asked, user chose the smaller documented-gap
path, left OPEN), F-18 (asked, user chose the smaller real-orchestration
path, closed FIXED). Both were genuine forks, not something to guess at.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 17 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 17/23** (F-01 through F-16,
plus F-18 — F-17 remains open, see the prior checkpoint and the
IMPORTANT section above). 6 HIGH findings remain open (F-17, F-19
through F-23).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-16**, plus **B-16** — see git log
  (`824b26a` through `3404f15`) and prior checkpoints for detail;
  unchanged this session.
- **F-18 (HIGH, this session)** — `SFTPPollSource`/`S3PollSource`
  (`ingestion/sources.py`) were fully built and tested since Phase 5 but
  constructed by nothing anywhere in the running system — the typical
  real-world 835 delivery channel for a payer (push to a mailbox, not a
  person uploading through the UI) didn't exist as a running path.
  **Asked the user first** (same as F-17): a *full* fix — owned
  scheduling, per-tenant SFTP/S3 config storage (the `Tenant` model has
  no field for it), real cloud credentials — would have been new
  infrastructure, matching `MASTER-BUILD-PROMPT-V2.md`'s own "Phase 7 —
  Async job infrastructure *(NEW — v1 had none)*" gap, not a wiring fix.
  **User chose the smaller, still-real option**: build the orchestration
  piece plus an invokable script, leave *scheduling itself* to whatever
  infrastructure a real deployment already has. Commit `febdf6e`
  (register updated to FIXED in `bf0f03f`):
  - `ingestion/poller.py` (new) — `poll_and_ingest`: pure orchestration,
    no I/O of its own. Calls `source.poll()` once, then a caller-supplied
    `ingest_one` callable per file. Same
    pure-orchestrator-over-an-injected-effect split
    `ingestion.plan`/`apply` already use, so the loop itself is provably
    correct without a live Postgres.
  - `scripts/ingestion/poll_remittances.py` (new) — the first real
    caller. Reads `TENANT_ID`/`SOURCE_KIND`/connection config from the
    environment, builds a real `SFTPPollSource`/`S3PollSource` against
    real paramiko/boto3-backed client adapters, closes `ingest_one` over
    a real `tenant_session` + `ingestion.pipeline.ingest_file` — the same
    function the HTTP upload path already calls. `seen` is deliberately
    NOT persisted across invocations (the `remittances` table stores a
    content hash, not the original filename — nothing to reload it
    from); `db.repository.record_remittance_if_new` is the real
    idempotency backstop, proven by this commit's own duplicate test.
  - `pyproject.toml` — `paramiko`/`boto3` added as a new `[poller]`
    optional-dependency group, not the base `dependencies` — the main
    API image never carries a hard dependency on either SDK. New
    `ignore_missing_imports` mypy override for both (installing real stub
    packages into the main dev environment for one optional script
    wasn't worth it).
  - `docs/RUNBOOK.md` — new "Scheduling remittance polling" section:
    concrete usage for both source kinds, explicit that scheduling itself
    is the deployer's own cron/CronJob/scheduled-task, not owned here.
  - New tests: `tests/ingestion/test_poller.py` (3 cases, pure — fake
    source + recording fake `ingest_one`, no DB).
    `tests/ingestion/test_poller_live_db.py` (2 cases, DB-backed, skips
    locally same as every other live-Postgres test) — one proving a real
    ingest against Postgres, one proving the "no persisted seen state"
    decision is genuinely safe (a second poll of the same object
    correctly comes back a duplicate, not a second claim set).
  - The two real client adapters (`_ParamikoSFTPClient`, `_Boto3S3Client`)
    are untested against a real SFTP server or AWS account — no live
    credentials exist in this build environment, same ceiling every
    other real-cloud-integration adapter here has had since Phase 9.

## In progress

Nothing mid-write. F-18 went through the full Wave 3 loop (state the
finding → ask before assuming scope → write/extend tests → minimal fix
→ show it passing locally → full local gate → mark FIXED in the
register with the commit SHA → commit) and is complete as a unit.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(166 files), `pytest -q` (478 passed, 34 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` (also
explicitly re-run against just the two new poller files) are all clean
as of commit `bf0f03f`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely. Still not blocking, still out of scope — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **`mypy --strict .` (the exact command this project's own gate uses)
  checks the *whole repo*, not just `src`+`tests`.** Discovered this
  session: `pyproject.toml`'s `[tool.mypy] files = ["src", "tests"]`
  looks like it should scope mypy, but a positional path argument on the
  command line (`.`) overrides that config entirely — `scripts/` (and
  `alembic/`, hence the pre-existing 0004 noise) gets checked too. This
  is why `scripts/ingestion/poll_remittances.py`'s lazy `paramiko`/`boto3`
  imports needed an explicit mypy override, not just an unused `files`
  entry. Worth remembering before assuming anything under `scripts/` is
  mypy-exempt.
- **`paramiko`/`boto3` went into a new optional-dependency group, not
  `dev` or the base `dependencies`.** Neither the main API image nor the
  general dev/test environment needs either SDK — only the one script
  that polls a real mailbox does. A third extras group (matching `dev`'s
  own existing split) keeps that boundary honest rather than bloating an
  environment that doesn't need it.
- **No per-tenant SFTP/S3 configuration storage was added.** The script
  reads connection details from environment variables per invocation
  (one script run = one tenant + one source), not a new `tenants` table
  column or a new config table. This was the explicit boundary the user
  approved — a real multi-tenant, self-service "configure your own SFTP
  drop" experience is exactly the kind of new-feature work deferred to
  stage 2, not built here.
- **Chose `ingest_file` (the low-level pipeline function) over
  `PostgresRepository.ingest_remittance` (the API-layer wrapper) as
  what the script's `ingest_one` closure calls.** Matches
  `scripts/onboard_customer.py`'s own established precedent of calling
  straight into `db.repository`/`ingestion.pipeline`, bypassing the API
  layer's `Repository` abstraction entirely for scripts — avoids needing
  to construct a `PacketDrafter`/tracer/instruments/notifier the script
  has no use for, just to satisfy `PostgresRepository.__init__`'s
  required params.

## Traps for someone resuming cold

- **Read the "IMPORTANT" section at the top of this file before doing
  anything else** — the two-stage work order, AND the now-twice-confirmed
  pattern that some register rows need a scoping conversation before
  starting, not just before finishing.
- **Everything F-01 through F-17's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, remembering `api.alerting.record_not_found(...)`
  on any new direct-id-lookup route, `required_figure_lines()` on any new
  scripted packet-draft fixture, `FakeRepository`'s audit-write gaps,
  OTel's global-provider write-once-per-process rule, and re-reading a
  finding's *full* row — not just its "Fix" column — once more right
  before marking it FIXED).
- **`docs/audit/REGISTER.md`'s BACKLOG table (MEDIUM/LOW items) still has
  no Status column** — B-16's "FIXED (sha)" marker from last session is
  appended inline into its description cell. Follow that same pattern if
  another BACKLOG item gets fixed incidentally.

## Next 3 steps

1. **F-19 next** per `REGISTER.md`'s own listed order — RLS
   tenant-isolation coverage is hand-maintained
   (`_TENANT_SCOPED_TABLES`, a hardcoded tuple in
   `alembic/versions/0001_initial_schema.py`) and proven for `claims`
   only; a future PHI-bearing table could ship with no RLS policy at all
   and nothing would fail. Register's suggested fix (a data-driven test
   parametrized from `Base.metadata` rather than the hand-maintained
   list) is genuinely fully buildable without real cloud infrastructure —
   this one looks like a clean stage-1 fit, unlike F-17/F-18's
   infrastructure wrinkle. Still worth a full read of the row and a
   reproduction before assuming, same discipline as always.
2. **F-20 (real cloud KMS adapters) and F-21 (a real, timed backup/
   restore drill) are the two most likely to need the same "ask before
   assuming" treatment F-17/F-18 got** — the register already says both
   are structurally blocked on real cloud infrastructure this
   environment has never had. Don't force either; surface the same kind
   of choice (small honest partial fix vs. full real thing) before
   starting.
3. **F-22/F-23 must come last**, not before F-01–F-21 are triaged (F-23
   literally depends on that). Once the MUST-FIX list is as closed as
   this environment allows, stage 2 begins — see the IMPORTANT section.
