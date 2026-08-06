# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 12 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 12/23** (F-01 through F-12 —
see below). 11 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).
Just over half the MUST-FIX list is now closed.

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-11** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`, `5f8d462`, `cf6c3e4`, `f118ccf`,
  `eafb9ca`, `d299b4a`, `37748f7`) and prior checkpoints for detail;
  unchanged this session.
- **F-12 (HIGH, this session)** — `record_finding_outcome` writes
  `findings.outcome`/`amount_recovered` (a PHI-bearing table, including a
  dollar amount) with no `write_audit_log` call at all — a direct
  CLAUDE.md rule 5 violation ("every write to a PHI-bearing table goes
  through the audit log, no exceptions"). The analogous `decide_packet`,
  two functions away in the same file, already does this correctly.
  Commit `1f17c10` (register updated to FIXED in `789bb29`):
  - `api/repository.py` — added the missing `write_audit_log` call right
    after `db_repository.record_finding_outcome`'s DB write, both still
    inside the same `tenant_session` transaction (matching
    `decide_packet`'s exact pattern). `action="finding_outcome_recorded"`,
    `resource_type="finding"`, `phi_accessed=True` (matches the finding's
    own framing of this table as PHI-bearing — same treatment
    `packet_generated`'s audit entry already gets, since it's adjacent to
    the same patient/dollar context).
  - New DB-backed test in `tests/api/test_endpoints_live_db.py`: records
    an outcome through `POST /findings/{id}/outcome`, confirms exactly
    one `finding_outcome_recorded` audit entry with the right
    actor/resource_type/resource_id/`phi_accessed`. Skips locally, same
    as the rest of that file.
  - **No local (non-DB) test was possible for this one.**
    `FakeRepository.record_finding_outcome` doesn't model an audit write
    at all (neither does its `decide_packet`, which has the identical
    gap in the fake) — a pre-existing test-only simplification this fix
    doesn't touch. DB-backed coverage is the only way to prove this fix
    genuinely, same ceiling every DB-write-triggered Wave 3 fix has had.

Smallest fix since F-06/F-10 — S effort per the register, and it landed
that way: one added function call plus one new test, a deliberate change
of pace after F-11's much larger wiring project.

## In progress

Nothing mid-write. F-12 went through the full Wave 3 loop (state the
finding → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA →
commit) and is complete as a unit, same as every prior fix.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(162 files), `pytest -q` (461 passed, 32 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `789bb29`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely (confirmed via `git stash` six sessions ago). Still not
blocking, still out of scope for this checkpoint — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **`phi_accessed=True`, not `False`, on the new audit entry.** The
  `findings` table has no direct patient-identifying field (that's on
  `claims`), so this wasn't automatic — `decide_packet`'s own audit entry
  for approving/rejecting a packet uses `phi_accessed=False`. Went with
  `True` for two reasons: F-12's own finding text explicitly calls
  `findings` "a PHI-bearing table," and the closest real precedent
  (`packet_generated`'s audit entry, adjacent to the same patient/dollar
  context) already uses `True`. If a future reviewer disagrees, the
  discussion is here, not just left implicit in the diff.
- **No attempt was made to add audit-write coverage to `FakeRepository`
  for `record_finding_outcome` (or `decide_packet`, which has the
  identical gap).** Tempting for symmetry, but out of scope: F-12 is
  about the real DB-write path specifically, and touching
  `FakeRepository`'s behavior is a test-infrastructure change that
  affects every test using it, not a one-line fix. If a future finding
  specifically wants `FakeRepository` to model audit writes generally,
  that's its own deliberate piece of work, not a drive-by here.

## Traps for someone resuming cold

- **Everything F-01 through F-11's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, remembering `api.alerting.record_not_found(...)`
  on any new direct-id-lookup route, OTel's global-provider
  write-once-per-process rule, most of the test suite never calling
  `main.py`'s `create_app_from_env()`, and re-reading a finding's *full*
  row — not just its "Fix" column — once more right before marking it
  FIXED).
- **`FakeRepository` silently diverges from `PostgresRepository` on which
  writes get audited** — `write_audit_log` calls exist on the real
  adapter for `decide_packet`, `generate_packet`, and now
  `record_finding_outcome`, but the fake never models any of them. A
  future test that expects `repo.audit_entries` to grow after calling one
  of these through `FakeRepository` will be quietly wrong — this has
  been true since Phase 7 for `decide_packet`/`generate_packet` and is
  now also true for `record_finding_outcome`, not a new gap this session
  introduced, but worth knowing before assuming the fake is a complete
  mirror.

## Next 3 steps

1. **F-13 next** per `REGISTER.md`'s own listed order — `packets/prompt.py`
   embeds the payer claim control number and date of service as literal
   values in the LLM prompt, and its own docstring wrongly calls them
   "not PHI." Real re-identifiable claim data would transmit to a third
   party with no BAA in place if the real Anthropic drafter were ever
   enabled. Fix is placeholder-substituting those two fields the same way
   name/amounts already are, correcting the docstring, and confirming the
   real drafter still isn't enabled anywhere. M effort per the register —
   worth checking `packets/service.py`'s validation path carries through
   correctly for the two new placeholder tokens, same rigor as F-14/F-15
   (queued right after it) will need for the currency-validation gaps.
2. **After F-13**, continue F-14 through F-23 in `REGISTER.md`'s own
   listed order, same loop each time (state finding → test → fix → gate →
   mark FIXED with SHA → commit) — and keep re-reading each finding's
   full row once more right before marking it FIXED.
3. **The MUST-FIX list is now just over half closed (12/23).** The prior
   checkpoint already flagged this as a reasonable point to consider
   updating `docs/PHASES.md` and re-running the Wave 0 baseline commands
   fresh across the whole accumulated batch — still true, still not done
   yet, still worth raising with whoever's driving this next rather than
   doing unprompted mid-stream.
