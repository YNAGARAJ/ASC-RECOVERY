# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## IMPORTANT — Wave 3 is now as closed as this environment allows; two-stage work order

The user confirmed a **two-stage work order** (also saved as a project
memory, `project_roadmap_scope` — re-derive from here if it isn't in
context):

1. **Finish Wave 3 remediation first** — close `docs/audit/REGISTER.md`'s
   remaining MUST-FIX rows, in the register's own listed order.
2. **Then build the unbuilt product-completeness gaps** listed at the top
   of `docs/MASTER-BUILD-PROMPT-V2.md`'s "PART 3 — GAP REGISTER" —
   frontend, async jobs, lesser-of/stop-loss/prompt-pay-interest contract
   logic, SSO/SCIM, multi-org hierarchy, reprocessing, and more. Entirely
   unbuilt features, not bugs.

**As of this checkpoint, all 23 MUST-FIX rows have a final disposition**
(20 FIXED, 3 honestly left OPEN with a documented reason — see below).
Stage 1 is done to the extent this environment allows. **Whether that
counts as "done enough" to start stage 2 has not been explicitly asked
yet — do that before writing any stage-2 code.** See "Next steps" below.

**Established pattern, confirmed across F-17, F-18, F-20, F-21, F-22 (all
from prior or this session)**: not every MUST-FIX row is a clean wiring
fix. Some findings' "real" fix requires infrastructure that was never
part of any of the 12 original phases, or that genuinely exists but this
particular session can't authenticate into. **When a finding looks like
this, ask the user how to scope it before proceeding** — don't guess.
Every one of F-17/F-18/F-20/F-21/F-22 got an explicit `AskUserQuestion`
before code was written or a row was marked either way.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. All 23 rows triaged: 20 FIXED, 3 OPEN with a documented reason.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks. Its BACKLOG section (65 rows, all
MEDIUM/LOW) is explicitly *not* part of Wave 3's scope — deferred by
design, not by omission.

**FIXED (20/23): F-01 through F-16, F-18, F-19, F-20, F-23.**

**OPEN, each with a specific, non-negotiable reason (3/23):**
- **F-17** — implant `invoice_cost` is always `None` on the real
  ingestion path because no purchasing-feed integration exists anywhere
  in this codebase. Not a wiring bug — there is nothing to wire. Implant
  lines correctly surface as `UNPRICED_CODE`/`shortfall=0` (never a
  *wrong* figure) in the meantime. Revenue-code half already fixed as
  B-16.
- **F-21** — `scripts/db/restore_drill.sh` (this session) automates the
  entire documented restore procedure, but the finding's actual
  requirement is "execute it for real, record the actual wall-clock
  number," and no AWS/Azure account exists in this environment to do
  that. User explicitly chose to leave this OPEN rather than mark it
  FIXED for writing the automation.
- **F-22** — every cross-seam integration test is gated behind
  `TEST_DATABASE_URL`. CI already has live Postgres and
  `docker-compose.yml`/`docs/DB_SETUP.md` already document a local
  option (both predate this session) — but **this session discovered a
  real PostgreSQL 18 server already running as a Windows service on
  this dev machine**, which would let the live-DB suite actually run
  here. User confirmed wanting to use it but didn't have the local
  `postgres` superuser password on hand and deferred setup to a future
  session — see "Next steps" for the exact handoff.

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-19** — see git log and prior
  checkpoints for detail; unchanged this session.
- **F-20 (HIGH, this session)** — `AwsKmsAdapter`
  (`src/security/kms_aws.py`) and `AzureKeyVaultAdapter`
  (`src/security/kms_azure.py`) implemented behind the existing
  `KeyManagementService` port (`security/kms.py`). Wired into
  `main.py`'s new `_build_kms()`, selected by a new `KMS_PROVIDER` env
  var (`"env"`/unset → today's `EnvKMS`, unchanged default;
  `"aws-kms"` → `AwsKmsAdapter`, requires `AWS_KMS_KEY_ID`;
  `"azure-keyvault"` → `AzureKeyVaultAdapter`, requires
  `AZURE_KEY_VAULT_KEY_ID`). Commit `02c6e38`, register updated in
  `5ba2b60`.
  - **AWS**: `key_id` is meant to be a KMS *alias*
    (`alias/asc-recovery-kek`), not a raw key id — aliases are what let
    AWS's own automatic annual rotation (`enable_key_rotation = true`,
    already in Terraform) take effect with zero redeploys.
  - **Azure**: fundamentally asymmetric with AWS here, documented at
    length in `kms_azure.py`'s docstring — a Key Vault key *version* is
    a distinct cryptographic object, not an alias. `current_kek_id()`
    is a *pinned* version id; picking up a rotation means redeploying
    with a new `AZURE_KEY_VAULT_KEY_ID` and running
    `EnvelopeEncryptor.rotate_kek()`. `wrap_key` rejects any `kek_id`
    other than the pinned current version; `unwrap_key` deliberately
    does not, since it must keep working for a DEK wrapped under an
    older version still enabled in the vault.
  - `AzureKeyVaultAdapter` takes an injectable `crypto_client_factory`
    so it's unit-testable without the real `azure-keyvault-keys` SDK
    installed; the wrap algorithm is the literal string
    `"RSA-OAEP-256"` rather than an imported enum, so the module has
    zero SDK imports at class-definition time.
  - `boto3`/`azure-identity`/`azure-keyvault-keys` added as a new
    `cloud-kms` optional-dependency group — not a base dependency;
    neither is installed in this dev environment, which is itself proof
    the lazy-import design works.
  - Tests: `tests/security/test_kms_aws.py` (5), `test_kms_azure.py`
    (5), `tests/test_main.py` (+5, `KMS_PROVIDER` wiring — both
    real-cloud branches only test the pre-SDK-import validation path,
    since neither SDK is installed here).
  - **Neither adapter has ever been exercised against a real AWS
    account or Azure Key Vault** — disclosed in both modules'
    docstrings, matching the F-18 poller adapters.
  - Documented in `docs/RUNBOOK.md`'s "Key rotation" section and
    `docs/SECURITY.md`'s control matrix + "Not yet built" list.
- **F-21 (HIGH, this session) — partially addressed, register entry
  stays OPEN**, per the user's explicit choice — see above.
  `scripts/db/restore_drill.sh` automates every step of
  `docs/RUNBOOK.md`'s restore procedure for either cloud (restore →
  wait → verify `alembic_version`/row counts/RLS → print elapsed time →
  tear down via a `trap`, `SKIP_TEARDOWN=1` escape hatch). Commit
  `7397b3d`. Shell-syntax-checked (`bash -n`) only — never actually run.
- **F-22 (HIGH, this session) — triaged, register entry stays OPEN**,
  per the user's explicit deferral — see above and "Next steps."
  Commit `0205871`.
- **F-23 (HIGH, this session)** — the `adversarial-reviewer` subagent
  was re-run against HEAD now that F-01 through F-22 all have a final
  disposition (F-23's own stated dependency). Result: **zero
  HIGH/CRITICAL findings.** Money math, date-of-service pricing, tenant
  isolation (RLS forced, `asc_app` has no bypass), audit-log coverage,
  PHI redaction, authorization, and this session's new KMS adapters all
  held up under independent review. 6 LOW items it noticed in passing
  were logged as `B-60` through `B-65` in the register's BACKLOG
  section (restore_drill.sh's connection string visible via `ps` on a
  shared host; poller filename not redaction-covered; a span attribute
  with the same gap; AWS KMS manual-rekey edge case; `decide_packet`
  understates `phi_accessed`; the first-admin bootstrap account has no
  path to actually get a password/MFA enrolled) — none of them HIGH or
  CRITICAL, none gate this finding. Commit `d154dcb`.

## In progress

Nothing mid-write. F-20 through F-23 all went through the full Wave 3
loop and are complete as units, each in the state the user asked for.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(171 files), `pytest -q` (495 passed, 35 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate, `python -m
evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar accuracy
on 504 golden cases), and `bandit -r . -x ./tests,./evals` were all clean
as of commit `5ba2b60` (last full gate run this session — F-22/F-23
triage after that point was register-only, no source changes, so the
gate result still holds at `d154dcb`).

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely. Still not blocking, still out of scope.

## Decisions worth knowing (not obvious from the code)

- **F-20 and F-21 both got a two-question `AskUserQuestion` before any
  code was written.** Both times the user picked "build what's
  buildable, be honest about what isn't, opt-in only" over the
  alternatives (skip entirely, or force a fake success): F-20 →
  **"Build both adapters, opt-in only via env var"**; F-21 → **"Build
  the restore-drill script, leave finding open."**
- **F-22's discovery mid-session changed the plan.** The original
  intent going in was to treat F-22 the same as F-17/F-21
  (infrastructure gap, document and move on) — both halves of its
  suggested fix already existed before this session (CI has live
  Postgres; `docker-compose.yml` documents a local option). Finding a
  real, already-running local PostgreSQL 18 service on this specific
  machine was a surprise discovery, not something anticipated by the
  register. Asked the user whether to use it (yes, with the existing
  instance rather than a separate cluster) and then for the `postgres`
  superuser password (deferred — user will run the setup SQL
  themselves later). **This is not the same kind of "infrastructure
  doesn't exist" gap as F-17/F-21/F-20** — the infrastructure exists
  and is a credential-away from being used; don't conflate the two when
  deciding how to talk about F-22 vs. the others.
- **`KMS_PROVIDER` defaults preserve F-04-through-F-19's status quo
  exactly** — `_build_kms()`'s `"env"` branch is byte-for-byte the same
  `EnvKMS(secrets)` construction `main.py` did before this session.
- **`AzureKeyVaultAdapter`'s first draft was not unit-testable** —
  lazy SDK imports *inside* `wrap_key`/`unwrap_key` meant even a
  fake-client test would hit a real import line. Redesigned with an
  injectable `crypto_client_factory` and a literal wrap-algorithm
  string instead of an enum import.
- **`restore_drill.sh` restores to "use latest restorable time," not a
  named snapshot** — matches what an actual disaster-recovery restore
  would use.
- **The adversarial re-review (F-23) was scoped to focus on
  recently-touched code first** (this session's KMS adapters,
  `restore_drill.sh`) precisely because it's the code least likely to
  have already had a fresh pair of eyes on it — then broadened to a
  full independent pass over `src/`. Worth repeating that ordering if
  F-23-style re-reviews happen again later.

## Traps for someone resuming cold

- **Read the "IMPORTANT" section at the top of this file before doing
  anything else.** Wave 3's MUST-FIX list is now fully triaged (20
  FIXED, 3 OPEN-with-reason) — this is a real milestone, but "triaged"
  is not the same as "the user has agreed stage 2 can start." That
  check-in has not happened yet.
- **F-22's local-Postgres handoff, ready to use immediately:** run this
  as the `postgres` superuser (matches `docker-compose.yml`'s existing
  dev credentials exactly, so nothing else in the repo needs to
  change):
  ```sql
  CREATE ROLE asc_owner LOGIN PASSWORD 'asc_owner_dev_password' CREATEDB;
  CREATE DATABASE asc_recovery OWNER asc_owner;
  ```
  then from the repo root:
  ```
  psql -U postgres -d asc_recovery -f scripts/db/init_roles.sql
  ```
  Once that's done: `export DATABASE_URL=...` (see `docs/DB_SETUP.md`
  for the exact connection string), `alembic upgrade head`, then
  `export TEST_DATABASE_URL=...` and run the full suite including
  every `*_live_db.py`/`tests/db/*` test — this would be the **first
  time ever** in this project's history that the live-DB suite runs for
  real outside CI. Worth doing before anything else if this session
  picks back up with the password available; it also lets F-19's RLS
  coverage test and the audit-log append-only test get verified against
  a real database, not just CI.
- **Everything F-01 through F-19's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, the `${VAR:?message}`
  bash-apostrophe gotcha, the PHI-content guardrail hook's quirks, the
  Makefile's `domain/variance.py`-only coverage gate, `mypy --strict .`
  actually sweeping the whole repo because of the `.` CLI argument,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, remembering `api.alerting.record_not_found(...)`
  on any new direct-id-lookup route, `required_figure_lines()` on any
  new scripted packet-draft fixture, `FakeRepository`'s audit-write
  gaps, OTel's global-provider write-once-per-process rule, and
  re-reading a finding's *full* row — not just its "Fix" column — once
  more right before marking it FIXED).
- **`pyproject.toml` now has two extras that both pull in `boto3`**
  (`[poller]` from F-18, `[cloud-kms]` from F-20) — deliberately not
  factored into a shared group. `pip install -e ".[poller,cloud-kms]"`
  if a real environment ever needs both.
- **B-60 through B-65 are new backlog rows from F-23's re-review** —
  same status as every other B-row (documented, deferred, not Wave 3
  scope), not something to reflexively start fixing.

## Next steps

1. **Explicitly check in with the user**: Wave 3's MUST-FIX list is now
   fully triaged — 20/23 FIXED, F-17/F-21/F-22 each OPEN with a specific
   documented reason (two are genuine infrastructure gaps this
   environment cannot close; F-22 is one credential away from being
   closeable and the path is written above). Ask whether this counts as
   "close enough" to start stage 2 (`docs/MASTER-BUILD-PROMPT-V2.md`'s
   unbuilt product-completeness gaps), or whether to first pursue the
   F-22 local-Postgres path to get a fourth row closed.
2. **If the user provides the local Postgres password**, follow the
   handoff above, run the full live-DB suite for real, and use the
   result to actually mark F-22 FIXED (or, if something fails, that's a
   new, real finding — not a hypothetical one anymore).
3. **If/when stage 2 begins**, start from
   `docs/MASTER-BUILD-PROMPT-V2.md`'s "PART 3 — GAP REGISTER" — these
   are unbuilt features (frontend, async jobs, lesser-of/stop-loss/
   prompt-pay-interest contract logic, SSO/SCIM, multi-org hierarchy,
   reprocessing), not bugs, so the working pattern shifts from
   "find-and-fix" to "design-and-build." Expect that to mean more
   upfront design conversation per item, not less.
