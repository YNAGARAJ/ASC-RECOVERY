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

**This session added a real wrinkle to that plan, worth understanding
before continuing**: not every MUST-FIX row turns out to be a pure
"wiring" bug fitting cleanly into stage 1. F-17 (below) is a finding
whose real fix is closer to stage-2 new-feature work than a stage-1 bug
fix — the user was asked and chose to fix what's genuinely fixable now
and leave the rest explicitly open rather than force a dishonest "FIXED"
status or silently build new scope. **If a future finding looks similar
(the real fix requires inventing a data source/feature that was never
part of any of the 12 original phases), ask before proceeding the same
way** — don't assume every register row is a same-shaped wiring fix.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 16 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: still 16/23** (F-01 through
F-16 — unchanged this session; F-17 was worked but deliberately NOT
closed, see below). 7 HIGH findings remain open (F-17 through F-23,
F-17 partially addressed), plus the 59-item BACKLOG (now 58 open, one
closed this session — see below).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-16** — see git log (`824b26a` through
  `43e241b`) and prior checkpoints for detail; unchanged this session.
- **F-17 (HIGH, this session) — worked but deliberately left OPEN.**
  The finding bundles two separable problems, and only one is a real
  Wave 3 bug fix:
  - **Revenue code (fixed, tracked as B-16)** — `domain/x835.py`'s SVC
    segment parser hardcoded `revenue_code=None`, even though SVC04
    (the element right after the paid amount) genuinely carries it —
    the parser just skipped straight from `elements[3]` to `elements[5]`
    and never read `elements[4]`. `domain.contract._is_implant` matches
    an implant line by revenue code as well as procedure code, so this
    silently weakened implant *detection* on the real ingestion path.
    Now parsed correctly. Commit `3404f15` (register updated in
    `761eb65`, B-16 marked **FIXED** in the BACKLOG table — the first
    BACKLOG item closed this Wave).
  - **Invoice cost (genuinely not fixed, F-17 stays OPEN)** — an
    implant's invoice cost (what the ASC paid its supplier for the
    device) is not carried on an 835 remittance at all, and no phase of
    this build has ever built a purchasing-feed integration to source it
    from elsewhere. There is no table, no upload mechanism, no API
    surface for it anywhere in this codebase. **This is not a wiring
    bug** — there is nothing to wire. The audit's own full write-up
    (`docs/audit/04-domain-correctness.md`) rates this half at **L
    effort** (register's own summary line says M) and explicitly offers
    a fallback: "document that implant recovery is out of scope... until
    [a real source exists]."
  - **Asked the user how to proceed** (a genuine fork, not a judgment
    call I should have made unilaterally) rather than either quietly
    building a new upload-metadata intake mechanism (real but sizable
    new-feature work, arguably stage-2) or quietly marking F-17 FIXED
    when its core complaint — implant lines never price correctly —
    would still hold. **User chose**: fix the revenue-code half now,
    leave invoice_cost genuinely open, don't mark F-17 FIXED.
  - `ingestion/plan.py`'s comment explaining `invoice_cost=None` was
    substantially strengthened — explicit about there being no data
    source, pointing at the exact test proving the current documented
    behavior, framed as deferred-to-stage-2 rather than a vague "Phase 1
    behavior" note.
  - New tests: `tests/domain/test_x835.py` gets 2 (SVC04 parsed when
    present; parses as `None` not empty-string when absent, matching
    every other optional field's convention in this parser).
    `tests/ingestion/test_plan.py` gets 1 proving the documented split
    explicitly — an implant line is now correctly *detected* via revenue
    code on the real ingestion path, and still correctly surfaces as
    `UNPRICED_CODE` with `shortfall=0`, never a wrong figure.
    `tests/ingestion/fixtures.py`'s `make_contract_version` factory
    gained an `implant_carveout_rule` override parameter to make that
    last test possible without hand-building a full `ContractVersion`.

Fully, genuinely verified locally — pure domain/parsing logic, no
DB/infra piece, same as the last several fixes.

## In progress

Nothing mid-write. F-17 is in an unusual, deliberate state: worked
this session, register row updated to reflect exactly what's fixed and
what isn't, but the finding itself is not closed. This is not a
stopping-point-mid-fix situation — it's the intended final state until
a real invoice-cost source is built (stage 2, or a future explicit
decision to build the smaller "upload-time optional invoice-cost field"
version that was discussed and declined for now).

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(162 files), `pytest -q` (475 passed, 32 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `761eb65`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely. Still not blocking, still out of scope — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **F-17 was not marked FIXED, even partially, in the MUST-FIX Status
  column** — it got a long explanatory note instead, with an explicit
  "PARTIALLY ADDRESSED" marker distinct from every other row's plain
  "FIXED (sha)". This is deliberate: the register's Status column has
  been a clean, trustworthy signal all Wave — a future session (or the
  user) skimming it for what's left needs to see F-17 as still-open work,
  not accidentally skip it because it looks closed. B-16, the piece that
  genuinely IS done, got its own real FIXED marker in the BACKLOG table
  instead, where it belongs.
- **Did not build the smaller "optional invoice-cost field on the upload
  endpoint" version**, even though it was on the table as a real,
  bounded option (no new DB table, just an optional form field threaded
  through 5 layers). Declined by the user this session — worth
  remembering this was discussed and explicitly deferred, not
  overlooked, if it comes up again either as part of F-17 or as part of
  a future stage-2 phase.
- **Extended `tests/ingestion/fixtures.py`'s shared factory** (added
  `implant_carveout_rule` as an override param) rather than
  hand-constructing a `ContractVersion` inline in the one test that
  needed it. The factory's own docstring says ingestion tests don't
  normally need to re-exercise implant/MPPR/bilateral logic — noted
  directly in the updated docstring why this one override is the
  deliberate exception, so a future reader doesn't wonder why the
  "don't re-exercise this" factory grew a param for exactly that.

## Traps for someone resuming cold

- **Read the "IMPORTANT" section at the top of this file before doing
  anything else** — both the two-stage work order AND this session's
  specific lesson (not every register row is a clean wiring fix; ask
  before assuming).
- **Everything F-01 through F-16's checkpoints already flagged still
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
- **`docs/audit/REGISTER.md`'s BACKLOG table (MEDIUM/LOW items) has no
  Status column at all** — unlike the MUST-FIX table. B-16's "FIXED
  (sha)" marker was appended inline into its one description cell,
  matching the table's existing 4-column shape rather than restructuring
  it. If more BACKLOG items get fixed later, follow that same inline
  pattern for consistency rather than adding a column partway through.

## Next 3 steps

1. **F-18 next** per `REGISTER.md`'s own listed order — SFTP/S3 poll
   sources (`ingestion/sources.py`) are fully built and tested but
   constructed by nothing; no poller/scheduler/route ever pulls from
   either, so the typical real-world 835 delivery channel doesn't exist
   as a running path. Register already notes this is closed by "Phase
   9's existing 'sources behind a port' requirement plus the wiring
   check" in the `MASTER-BUILD-PROMPT-V2.md` cross-reference — worth
   checking whether a lightweight scheduled worker is genuinely buildable
   within this environment (no real SFTP/S3 credentials exist here
   either, similar ceiling to other infra-adjacent fixes) before
   assuming it's a clean stage-1 fit. Re-read the full row and reproduce
   fully before starting, same discipline as F-17.
2. **After F-18**, F-19/F-20/F-21 are the ones most likely to hit the
   same "not really fixable here" wall F-17 partly did — F-19 (RLS
   coverage test) is probably genuinely fixable (a data-driven test, no
   real infra needed beyond what already exists), but F-20 (real cloud
   KMS adapters) and F-21 (a real backup/restore drill, actually timed)
   are explicitly, structurally blocked on real cloud infrastructure this
   environment has never had — the register already says so plainly.
   Don't force those; confirm with the user how to handle them
   (document-and-defer, matching F-17's pattern, is the likely answer)
   rather than guessing.
3. **F-22/F-23 must come last**, not before F-01–F-21 are triaged (F-23
   literally depends on that). Once the MUST-FIX list is as closed as
   this environment allows, stage 2 begins — see the IMPORTANT section.
