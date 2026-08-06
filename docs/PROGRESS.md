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

**Established pattern from F-17 and F-18 (both in a prior session)**:
not every MUST-FIX row is a clean wiring fix. Some findings' "real" fix
requires inventing infrastructure/data storage that was never part of
any of the 12 original phases. **When a finding looks like this, ask the
user how to scope it before proceeding** — don't guess. F-19 (this
session) turned out NOT to be one of these — it was a clean, fully
in-scope, fully offline-plus-DB-testable fix, confirming the "ask when
uncertain" rule doesn't mean "ask about everything."

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 18 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 18/23** (F-01 through F-16,
F-18, F-19 — F-17 remains deliberately open, see the prior checkpoint).
5 HIGH findings remain open (F-17, F-20 through F-23).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-18**, plus **B-16** — see git log
  (`824b26a` through `febdf6e`) and prior checkpoints for detail;
  unchanged this session.
- **F-19 (HIGH, this session)** — `_TENANT_SCOPED_TABLES`
  (`alembic/versions/0001_initial_schema.py`) is a hardcoded tuple that
  doesn't track `db/models.py`, and it already drifted once for real:
  `recovery_packets` needed its own migration (0004) to get RLS at all,
  since 0001 predates that table's existence and nothing ever re-checked
  the list afterward. The existing RLS cross-tenant proof
  (`tests/db/test_rls_tenant_isolation.py`) is well-designed but only
  ever exercises `claims` — a future PHI-bearing table could ship with a
  `tenant_id` column and no RLS policy at all, and nothing would fail.
  Commit `52ba255` (register updated to FIXED in `02c3683`):
  - New `tests/db/test_rls_coverage.py` — walks `Base.metadata` directly
    for every table carrying a `tenant_id` column, then queries
    `pg_class`/`pg_policies` to confirm each one has RLS enabled, forced,
    and an actual policy. A future `tenant_id`-bearing table with no RLS
    migration now fails this test instead of silently shipping
    unprotected. `users` is the one deliberate exception (already
    explained in `db.models.User`'s own docstring: resolving a bearer
    token's subject to a `tenant_id` is how a tenant-scoped session gets
    bootstrapped in the first place, so this lookup can't itself require
    `app.tenant_id` to already be set) — kept in a small, explicit
    exclusion set that its own separate test guards against silent
    future growth.
  - Complementary to, not a replacement for,
    `test_rls_tenant_isolation.py`: that file proves RLS actually
    *blocks* a cross-tenant read where present; this one proves
    *coverage* — that every table which should have RLS does.
  - 3 new tests: 2 fully offline (the tenant-scoped table list isn't
    accidentally empty; the exclusion list is exactly `{"users"}`), 1
    DB-backed (the actual `pg_class`/`pg_policies` check) that skips
    locally same as every other live-Postgres test in this repo.
  - This was the fastest MUST-FIX fix in several sessions — no scoping
    conversation needed, no infrastructure gap, pure test-writing against
    already-correct production code (every existing table's RLS was
    already right; only the *proof* of coverage was missing).

## In progress

Nothing mid-write. F-19 went through the full Wave 3 loop (state the
finding → write the test → show it passing locally where possible → full
local gate → mark FIXED in the register with the commit SHA → commit)
and is complete as a unit.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(167 files), `pytest -q` (480 passed, 35 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `02c3683`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely. Still not blocking, still out of scope — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **The exclusion list (`_DELIBERATELY_UNGATED_TABLES`) is enforced by
  its own dedicated test**, not just a comment. `test_deliberately_ungated_tables_list_is_exactly_the_justified_one`
  fails loudly if the set ever grows without someone deliberately editing
  that assertion too — the whole point of F-19 is "don't let a gap grow
  silently," and an unenforced exclusion list would just relocate the
  exact same silent-drift risk one file over.
- **Chose `pg_class`/`pg_policies` system-catalog queries over
  `information_schema`** — Postgres's RLS-specific flags
  (`relrowsecurity`, `relforcerowsecurity`) and `pg_policies` are the
  authoritative, Postgres-native source for this; `information_schema`
  doesn't expose RLS status at all (it's not part of the SQL standard).
  No real alternative existed here, just noting it wasn't a close call.
- **Did not touch `alembic/versions/0001_initial_schema.py`'s
  `_TENANT_SCOPED_TABLES` itself.** The finding's fix text asks
  specifically for "a data-driven test," not for migrations to somehow
  dynamically derive their own DDL from current ORM state (which would
  be a strange, fragile thing for a migration to do anyway — migration
  DDL is supposed to be a frozen historical record). The hardcoded list
  stays exactly as-is; the new test is what keeps it honest going
  forward.

## Traps for someone resuming cold

- **Read the "IMPORTANT" section at the top of this file before doing
  anything else** — the two-stage work order, and the reminder that "ask
  before assuming new infrastructure is in scope" doesn't mean every
  finding needs a scoping conversation. F-19 didn't.
- **Everything F-01 through F-18's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  `mypy --strict .` actually sweeping the whole repo (not just
  `src`+`tests`) because of the `.` CLI argument, remembering
  `dependencies=[Depends(enforce_rate_limit)]` on any new authenticated
  router, remembering `api.alerting.record_not_found(...)` on any new
  direct-id-lookup route, `required_figure_lines()` on any new scripted
  packet-draft fixture, `FakeRepository`'s audit-write gaps, OTel's
  global-provider write-once-per-process rule, and re-reading a finding's
  *full* row — not just its "Fix" column — once more right before
  marking it FIXED).
- **If a brand-new tenant-scoped table is ever added**, its own migration
  must grant RLS (matching 0004's `_grant_and_secure_recovery_packets()`
  pattern) *and* nothing further needs updating in
  `tests/db/test_rls_coverage.py` itself — the new test picks it up
  automatically via `Base.metadata`. That's the whole point; don't
  reflexively go add it to a list somewhere.

## Next 3 steps

1. **F-20 (real cloud KMS adapters) and F-21 (a real, timed backup/
   restore drill) are next per `REGISTER.md`'s order, and both are
   explicitly, structurally blocked on real cloud infrastructure this
   environment has never had** — the register says so plainly for both.
   Follow the F-17/F-18 pattern: read the full row, work out what (if
   anything) is genuinely fixable without real infrastructure, and ask
   the user how to scope it before writing code. Don't force either.
2. **F-22/F-23 must come last**, not before F-01–F-21 are triaged (F-23
   literally depends on that).
3. **Once the MUST-FIX list is as closed as this environment allows**,
   stage 2 begins — see the IMPORTANT section. Worth explicitly checking
   in with the user at that point about how close is "close enough" given
   F-20/F-21's infrastructure ceiling, rather than assuming.
