# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## IMPORTANT — the plan changed this session, read this before anything else

The user confirmed a **two-stage work order** for this repo (also saved
as a project memory, `project_roadmap_scope`, so it should already be in
context — if it isn't, re-derive it from here):

1. **Finish Wave 3 remediation first** — close `docs/audit/REGISTER.md`'s
   remaining MUST-FIX rows, F-17 through F-23, in the register's own
   listed order. This checkpoint's "Next steps" section still tracks
   this.
2. **Then build the unbuilt product-completeness gaps** listed at the
   top of `docs/MASTER-BUILD-PROMPT-V2.md`'s "PART 3 — GAP REGISTER" —
   frontend, async job infrastructure, lesser-of/stop-loss/prompt-pay-
   interest contract logic, SSO/SCIM, multi-org/facility hierarchy,
   reprocessing without losing biller decisions, and more. These are
   entirely unbuilt features, not bugs. `MASTER-BUILD-PROMPT-V2.md` is a
   rebuild-from-scratch methodology document, not a literal task list
   against this repo — translate its phases into incremental work on the
   existing codebase rather than restarting from Phase 1.

**Do not start stage 2 until F-23 is closed** (or the user explicitly
redirects). See the memory file for the full reasoning and how the two
documents relate — the short version: `docs/audit/REGISTER.md`'s F-01
through F-23 are real bugs in the existing 12-phase build (what Wave 3
has been fixing); `MASTER-BUILD-PROMPT-V2.md`'s gap register cross-
references those same IDs (no separate fix needed there) plus a
completely separate list of large features nobody has built yet.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 16 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 16/23** (F-01 through F-16 —
see below). 7 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-15** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`, `5f8d462`, `cf6c3e4`, `f118ccf`,
  `eafb9ca`, `d299b4a`, `37748f7`, `1f17c10`, `f0b163e`, `b8da7c4`) and
  prior checkpoints for detail; unchanged this session.
- **F-16 (HIGH, this session)** — `BilateralConvention.TWO_LINE_SPLIT`
  was a storable enum value with no pricing implementation at all;
  `price_claim` only ever checked for `SINGLE_LINE_150_PCT`. A contract
  configured with `TWO_LINE_SPLIT` silently priced both bilateral lines
  at 100% (no reduction whatsoever) — a direct money error on every
  claim billed that way. Commit `43e241b` (register updated to FIXED in
  `aeae824`):
  - `domain/contract.py` — `TWO_LINE_SPLIT` now prices the higher-valued
    of the two modifier-50 lines (ties broken by original line order,
    same stable-sort convention step 6's MPPR ranking already uses) at
    100% of its base price, and the other at the remainder of
    `total_rate` above 100% (150% total → 100% + 50%) — what most payers
    actually mean by "bilateral, billed on two lines," not a second 150%
    payment on top of the first.
  - **Found and fixed a second, adjacent bug while writing this fix's
    own test**: bilateral lines were never excluded from MPPR ranking
    (step 6), so a two-line-split claim with no other procedures on it
    still triggered a *second*, spurious MPPR reduction on top of the
    bilateral one, purely from having 2 lines in the pool. This gap was
    latent in `SINGLE_LINE_150_PCT` too (its own existing tests only
    ever use a single line, so it never surfaced) — `TWO_LINE_SPLIT`
    can't be exercised with fewer than two lines, so implementing it
    immediately exposed the pre-existing interaction bug. Both bilateral
    conventions now exclude their lines from the MPPR pool, same as
    implant/assistant-surgeon/case-rate lines already do.
  - New tests (`tests/domain/test_contract.py`): `TWO_LINE_SPLIT` pays
    100%/remainder correctly, the split follows value not input order,
    bilateral lines are excluded from MPPR ranking.

Fully, genuinely verified locally — pure domain logic, no DB/infra
piece, same as F-13/F-14/F-15.

## In progress

Nothing mid-write. F-16 went through the full Wave 3 loop (state the
finding → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA →
commit) and is complete as a unit, same as every prior fix.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(162 files), `pytest -q` (472 passed, 32 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `aeae824`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely (confirmed via `git stash` nine sessions ago). Still not
blocking, still out of scope for this checkpoint — flagging again so it
doesn't get silently attributed to a future fix by mistake.

**Also pre-existing, not blocking:** `domain/contract.py`'s own coverage
sits at 95% (lines 142-144, 214->223, 241, 250, 254->260 uncovered) —
all in the assistant-surgeon/case-rate/`UNPRICED`-fallback branches,
none touched by this session's change. Only `domain/variance.py` has an
actual enforced 100%-coverage gate; this is just an observation, not a
failing check.

## Decisions worth knowing (not obvious from the code)

- **The 100%/50% split is by value, not by input line order.** Real
  bilateral pairs almost always share the same procedure code (and
  therefore the same base price), so this rarely matters in practice —
  but when it does (e.g. a data-entry quirk gives the two lines
  different charges), paying the *higher*-valued line at 100% is the
  conservative, payer-favorable-to-provider choice, and it reuses the
  exact stable-sort pattern MPPR ranking (step 6) already established in
  this same function, rather than inventing a second tie-breaking
  convention.
- **Fixed the MPPR-exclusion bug for `SINGLE_LINE_150_PCT` too, not just
  the new `TWO_LINE_SPLIT` convention**, even though F-16's own scope
  was narrowly "implement the missing branch." Deliberate: it's the same
  root cause (bilateral lines never excluded from the MPPR pool), the
  fix is one line applied uniformly, and leaving a known-identical bug
  unfixed immediately adjacent to the one being fixed would have been
  indefensible once noticed. Same judgment already applied to F-13's
  bonus docstring fix and F-08/F-09's shared fix site.
- **No new validation was added rejecting more than 2 modifier-50 lines
  under `TWO_LINE_SPLIT`, or exactly 1.** With 1 line, the code pays it
  at 100% (no reduction) — conservative and safe. With 3+, it pays the
  top-ranked at 100% and reduces the rest — degrades gracefully without
  crashing. Real-world data will essentially always have exactly 2;
  adding cardinality validation wasn't asked for and would be scope
  creep against an edge case this codebase's other rules (MPPR, case
  rate) don't validate either.

## Traps for someone resuming cold

- **Read the "IMPORTANT" section at the top of this file before doing
  anything else** — the work order for this repo changed this session
  (Wave 3 first, then unbuilt product gaps from `MASTER-BUILD-PROMPT-V2.md`),
  and that's easy to miss if skimming straight to "Next 3 steps" below.
- **Everything F-01 through F-15's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, remembering `api.alerting.record_not_found(...)`
  on any new direct-id-lookup route, `required_figure_lines()` on any
  new scripted packet-draft test fixture, `FakeRepository`'s audit-write
  gaps, OTel's global-provider write-once-per-process rule, and
  re-reading a finding's *full* row — not just its "Fix" column — once
  more right before marking it FIXED).
- **Any future change touching bilateral or MPPR pricing should check
  both conventions stay in sync.** `SINGLE_LINE_150_PCT` and
  `TWO_LINE_SPLIT` now share the same `bilateral_indices` computation and
  MPPR-exclusion step (`domain/contract.py`'s step 3) — a future edit to
  one convention's branch that doesn't consider the other risks
  reintroducing an asymmetry like the one just fixed.

## Next 3 steps

1. **F-17 next** per `REGISTER.md`'s own listed order — implant carve-out
   logic is fully correct in `domain/contract.py` but never fires on the
   real ingestion path; `invoice_cost` is always `None` there, so every
   implant line surfaces as `UNPRICED_CODE` with zero shortfall instead
   of a real recovery amount (typically the highest-dollar recovery
   category for an ASC). Needs threading the real invoice cost (and
   revenue code) from the parsed 835/upload metadata through to
   `ClaimLineInput.invoice_cost` — touches `ingestion/` (`domain/x835.py`
   parsing, `ingestion/plan.py`), not just `domain/contract.py` like the
   last several fixes. M effort per the register.
2. **After F-17**, continue F-18 through F-23 in `REGISTER.md`'s own
   listed order. Per the last checkpoint's assessment, several of these
   are the largest/hardest remaining: F-18 (SFTP/S3 poller wiring),
   F-19/F-20/F-21 (RLS coverage test, real cloud KMS adapters, a real
   restore drill — the register itself notes the latter two are
   genuinely blocked on real cloud infrastructure this environment
   doesn't have), F-22/F-23 (process items that must land last, not
   first, once F-01–F-22 are triaged).
3. **Once F-23 closes**, this checkpoint's "IMPORTANT" section's stage 2
   begins: sequencing the unbuilt product-completeness gaps from
   `MASTER-BUILD-PROMPT-V2.md`. Don't jump ahead to this while register
   items remain open — but it's worth reading `MASTER-BUILD-PROMPT-V2.md`
   in full once, ahead of time, to have a mental map ready (frontend,
   async jobs, lesser-of/stop-loss pricing, prompt-pay interest, SSO,
   multi-org hierarchy, reprocessing, and more — see the memory file or
   that document's own Part 3 for the complete list).
