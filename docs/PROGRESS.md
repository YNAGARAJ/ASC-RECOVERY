# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 15 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 15/23** (F-01 through F-15 —
see below). 8 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).
Nearly two-thirds of the MUST-FIX list is now closed.

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-13** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`, `5f8d462`, `cf6c3e4`, `f118ccf`,
  `eafb9ca`, `d299b4a`, `37748f7`, `1f17c10`, `f0b163e`) and prior
  checkpoints for detail; unchanged this session.
- **F-14 + F-15 (HIGH, this session)** — both in the same money/LLM-
  boundary family as F-13, both touching `packets/currency.py` and
  `packets/service.py`, fixed together like F-04/F-05 and F-08/F-09
  before them. Commit `b8da7c4` (register updated to FIXED in `d0d7273`):
  - **F-14** — the currency validator's regex required either a leading
    `$` or an exact two-decimal-digit suffix before treating a number as
    currency, so a hallucinated whole-dollar figure written as a bare
    integer ("the shortfall was 50") matched neither alternative and
    sailed through both the raw-draft and post-substitution gates
    completely undetected. `packets/currency.py` gained a third
    bare-integer regex alternative. Every real dollar value this system
    ever injects always has exactly 2 decimal places
    (`domain.money.Money`'s own invariant), so a *correct* substituted
    figure was always already caught by the existing 2-decimal
    alternative — the new one exists purely to catch what the LLM might
    invent on its own. Catching bare integers by default means a
    legitimate non-monetary number (the procedure code, in practice)
    needs an explicit opt-out: `extract_currency_figures`/
    `validate_currency` gained an `exclude` parameter, threaded from
    `packets/service.py` as `{data.procedure_code}`.
  - **F-15** — pure set-membership validation of the three dollar tokens
    after substitution can't distinguish "the model put the right figure
    in the right place" from "the model put a real, allowed figure in
    the wrong place" (e.g. `ACTUAL_ALLOWED_TOKEN` written where
    `EXPECTED_ALLOWED_TOKEN` belongs) — both substitute to values already
    in the allowed set, so the old check passed either way.
    `packets/prompt.py` gained `required_figure_lines()`: three exact
    `"Label: {{TOKEN}}"` strings (single source of truth for both the
    prompt instructions and the post-generation check, so the two can't
    drift apart) the model must reproduce verbatim.
    `packets/service.py`'s `generate_packet_draft` now rejects a draft
    missing or mislabeling any of the three lines, checked before
    substitution — a swapped token fails this exact-string check even
    though the swapped *value* would still pass plain set membership.
  - **Test fixture ripple**: both fixes changed what counts as a valid
    scripted draft, so every test fixture using the old free-form token
    placement needed updating —
    `tests/packets/test_service.py`'s `_GOOD_DRAFT` and
    `tests/api/test_endpoints_live_db.py`'s `_VALID_SCRIPTED_DRAFT` now
    use `required_figure_lines()`. `tests/packets/test_currency.py`'s
    procedure-code/year test was rewritten outright, not just patched —
    F-14 deliberately makes bare-integer detection the new default
    behavior, so the old test's assertion ("these bare digits are never
    flagged") was asserting the exact thing this fix intentionally
    changed, not a regression to keep guarding.
  - New tests: swapped-label rejection, bare-integer rejection, the
    procedure code specifically never flagged, the three required lines
    present verbatim in the built prompt, and the label-to-token pairing
    itself.

Fully, genuinely verified locally — no DB/infra piece this time, same as
F-13, though the DB-backed `_VALID_SCRIPTED_DRAFT` fixture still needed
updating to stay correct for whenever that file's tests eventually run
against a live Postgres.

## In progress

Nothing mid-write. F-14/F-15 went through the full Wave 3 loop (state the
findings → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA →
commit) and is complete as a unit, same as every prior fix.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(162 files), `pytest -q` (469 passed, 32 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `d0d7273`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely (confirmed via `git stash` eight sessions ago). Still not
blocking, still out of scope for this checkpoint — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **`exclude` takes exact strings, not a regex or a semantic category.**
  Considered making the bare-integer alternative "smarter" (e.g. only
  match 1-4 digit numbers, on the theory procedure codes are 5 digits)
  instead of adding an explicit exclude parameter. Rejected: digit-count
  heuristics are exactly the kind of fragile, drift-prone pattern-guessing
  this whole module already avoids for the $-prefixed/2-decimal cases: a
  digit-count rule would break the moment a 4-digit HCPCS-adjacent code
  or a genuinely 5-digit dollar figure showed up. An explicit exclude set
  of exact known-safe strings is simple, correct by construction, and
  the caller (`packets/service.py`) already knows exactly which one
  string it needs to pass.
- **`required_figure_lines()` lives in `packets/prompt.py`, not
  `packets/service.py` or `packets/currency.py`.** It's genuinely used by
  both `build_prompt` (to phrase the instruction) and
  `generate_packet_draft` (to validate the result) — putting it in
  `prompt.py` alongside the token constants it's built from means the
  label text and the token can never drift apart into two different
  modules independently defining "the same" string.
- **The three required lines use natural-language labels
  ("Expected allowed amount:", "Actual amount paid:", "Shortfall:"), not
  the raw field names.** These become part of what the LLM must
  literally reproduce, and by extension shape a real sentence in the
  final appeal letter a human (payer reviewer) will read — plain English
  labels read as a normal, professional letter; using internal field
  names verbatim would have looked mechanical in the delivered document
  for no benefit.
- **This is the third Wave 3 fix pairing two register rows in one commit**
  (after F-04/F-05, F-08/F-09) — same judgment each time: the register
  itself flags them as touching the same code, and splitting the tests/
  fixture-ripple work across two separate commits would have meant
  updating the same scripted-draft fixtures twice for no benefit.

## Traps for someone resuming cold

- **Everything F-01 through F-13's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, remembering `api.alerting.record_not_found(...)`
  on any new direct-id-lookup route, `FakeRepository`'s audit-write gaps,
  OTel's global-provider write-once-per-process rule, and re-reading a
  finding's *full* row — not just its "Fix" column — once more right
  before marking it FIXED).
- **Any future `ScriptedPacketDrafter` fixture used with
  `generate_packet_draft` must include all three `required_figure_lines()`
  verbatim, plus no bare-integer/raw-currency text anywhere else in the
  draft, or it will now be rejected.** This tripped three existing test
  fixtures this session (`tests/packets/test_service.py`,
  `tests/api/test_endpoints_live_db.py`) — worth checking
  `tests/api/test_pilot_workflow_live_db.py` too if it ever grows a
  scripted draft with real content (currently only uses
  `ScriptedPacketDrafter([])`, unaffected).
- **`extract_currency_figures`/`validate_currency` now require callers to
  think about `exclude`** whenever the text being scanned is known to
  legitimately contain a bare non-monetary number. Every current call
  site already handles this (`packets/service.py` passes the procedure
  code); a new call site that forgets this will get spurious rejections
  the moment its input mentions any bare digit sequence.

## Next 3 steps

1. **F-16 next** per `REGISTER.md`'s own listed order —
   `domain/contract.py`'s `BilateralConvention.TWO_LINE_SPLIT` is a
   storable enum value with no pricing implementation; a contract
   configured with it silently prices both lines at 100% (no bilateral
   reduction at all) — a direct money error, not just a missing feature.
   M effort per the register. This is pure domain logic (`domain/contract.py`,
   already has strong existing test coverage per the `variance.py`
   100%-coverage-adjacent gate) — should be fully offline-verifiable with
   no DB/infra piece, likely a clean, contained fix similar in shape to
   F-13/F-14/F-15.
2. **After F-16**, continue F-17 through F-23 in `REGISTER.md`'s own
   listed order, same loop each time (state finding → test → fix → gate →
   mark FIXED with SHA → commit) — and keep re-reading each finding's
   full row once more right before marking it FIXED.
3. **The MUST-FIX list is now closer to two-thirds closed (15/23), well
   past the halfway point flagged in the last several checkpoints.** The
   remaining 8 items (F-16 through F-23) include some of the largest
   individual pieces left — F-18 (SFTP/S3 poller), F-19/F-20/F-21 (RLS
   coverage test, real cloud KMS adapters, a real restore drill — the
   register itself notes these are genuinely blocked on real cloud
   infrastructure this environment doesn't have), and F-22/F-23 (process
   items that must come last, not first). Worth explicitly discussing
   with whoever's driving this next how far to push before pausing for
   the `docs/PHASES.md` update and Wave 0 baseline re-run raised in prior
   checkpoints — the "meaningfully further along" threshold from the
   original plan is arguably already met.
