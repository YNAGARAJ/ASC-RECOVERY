# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 13 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 13/23** (F-01 through F-13 —
see below). 10 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-12** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`, `5f8d462`, `cf6c3e4`, `f118ccf`,
  `eafb9ca`, `d299b4a`, `37748f7`, `1f17c10`) and prior checkpoints for
  detail; unchanged this session.
- **F-13 (HIGH, this session)** — `build_prompt` (`packets/prompt.py`)
  embedded the payer claim control number and date of service as literal
  text in the prompt sent to the LLM, and this module's own docstring
  called them "not PHI" — wrong. A claim/account number is a HIPAA Safe
  Harbor identifier (#16 account numbers / #18 any other unique
  identifying number); a date directly tied to an individual's care,
  including date of service, is explicitly identifier #3. With the real
  `AnthropicPacketDrafter` wired for a running deployment (`main.py`),
  this meant re-identifiable claim data would transmit to Anthropic with
  no BAA in place the moment a real API key was ever configured. Commit
  `f0b163e` (register updated to FIXED in `a9dbcf0`):
  - `packets/prompt.py` — both fields are now placeholder-only
    (`CLAIM_REFERENCE_TOKEN`/`DATE_OF_SERVICE_TOKEN`), the exact same
    mechanism patient name/member id and the dollar figures already used
    — the real value lives only in the `placeholders` map, substituted
    back into the draft after generation, never seen by the model.
    Corrected the module docstring's wrong PHI claim, and — noticed
    while fixing that sentence — its separately stale description of
    dollar amounts as "included as real values" (they never were; the
    code already placeholder-substitutes them too, the docstring just
    hadn't been updated to say so).
  - `main.py` — added a comment at the `AnthropicPacketDrafter`
    construction site reiterating that this data-minimization fix
    doesn't substitute for the actual BAA precondition. Deliberately not
    a code-level gate: BAA status is a compliance/process checklist item
    (`docs/compliance/README.md`), and the register's own closing note
    already frames that whole checklist as "a non-engineering gate this
    audit does not shorten by a single day" — nothing here tries to.
  - `tests/packets/test_prompt.py` — new test proving the claim
    reference and date of service never appear in the literal prompt
    text (mirroring the existing patient-name/member-id test), plus the
    existing placeholders-map test extended to cover both new tokens.

Fully, genuinely verified locally — no DB/infra piece this time, pure
prompt-construction logic with existing test infrastructure already in
place to extend.

## In progress

Nothing mid-write. F-13 went through the full Wave 3 loop (state the
finding → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA →
commit) and is complete as a unit, same as every prior fix.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(162 files), `pytest -q` (462 passed, 32 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `a9dbcf0`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely (confirmed via `git stash` seven sessions ago). Still not
blocking, still out of scope for this checkpoint — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **No new runtime validation was added checking that the model never
  writes the raw claim reference/date literally.** Currency figures get
  a real post-hoc validation pass (`packets.currency.validate_currency`)
  because the model could still hallucinate a plausible-looking dollar
  amount even without being told the real one. Claim reference numbers
  and dates of service aren't the kind of thing a model spontaneously
  invents matching a real value by coincidence, and — more importantly —
  after this fix the model literally never receives them at all, so
  there's no channel for it to leak them through even accidentally. The
  risk this fix closes is transmission to a third party, not
  hallucination; the existing currency-validation machinery solves a
  different problem and doesn't need to grow a parallel check for these
  two fields.
- **`main.py`'s BAA reminder is a comment, not a runtime check.** A
  tempting alternative was gating `AnthropicPacketDrafter` construction
  behind a new `BAA_CONFIRMED=true` environment variable or similar.
  Rejected: that would just move the same trust problem one level down
  (nothing stops someone from setting the flag without an actual signed
  BAA), and the register's own verdict section already explicitly places
  BAA tracking outside engineering's remit. A prominent comment at the
  exact construction site, pointing at the real compliance checklist, is
  the honest amount of code-level enforcement this actually calls for.
- **Fixed the docstring's dollar-amount claim too, not just the claim
  reference/DOS one F-13 named.** While correcting the "not PHI" sentence,
  noticed it separately claimed dollar amounts are "included as real
  values" in the prompt text — false; `build_prompt`'s own code already
  placeholder-substitutes them and always has. Small, low-risk,
  directly-adjacent accuracy fix bundled into the same commit rather than
  filed as a new finding for one stale sentence.

## Traps for someone resuming cold

- **Everything F-01 through F-12's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook's quirks, the Makefile's `domain/variance.py`-only coverage gate,
  remembering `dependencies=[Depends(enforce_rate_limit)]` on any new
  authenticated router, remembering `api.alerting.record_not_found(...)`
  on any new direct-id-lookup route, `FakeRepository`'s audit-write gaps
  for `decide_packet`/`record_finding_outcome`, OTel's global-provider
  write-once-per-process rule, and re-reading a finding's *full* row —
  not just its "Fix" column — once more right before marking it FIXED).
- **`packets/prompt.py`'s placeholder set grew from 5 tokens to 7.** Any
  future code that iterates `BuiltPrompt.placeholders` expecting a fixed
  set of keys (none currently does, but worth checking before assuming)
  should account for `CLAIM_REFERENCE_TOKEN`/`DATE_OF_SERVICE_TOKEN` now
  always being present alongside the original five.

## Next 3 steps

1. **F-14 and F-15 next, together** — the register doesn't explicitly
   say to pair them, but both are in the same money/LLM-boundary family
   as F-13 and touch the same two files (`packets/currency.py`,
   `packets/service.py`): F-14 is the currency validator missing bare
   integers with no `$`/decimal point (a hallucinated whole-dollar figure
   written that way bypasses both gates); F-15 is set-membership
   validation accepting a real figure placed against the wrong label (a
   token-swap hallucination passes). Both are M effort, both need real
   test-driven fixes to `packets/currency.py`'s regex/`packets/service.py`'s
   validation logic — worth reading both finding rows in full before
   starting, same "full row, not just the Fix column" discipline as
   always, since fixing one without considering the other risks a second
   pass through the same two files.
2. **After F-14/F-15**, continue F-16 through F-23 in `REGISTER.md`'s own
   listed order, same loop each time (state finding → test → fix → gate →
   mark FIXED with SHA → commit).
3. **The MUST-FIX list is now nearly 60% closed (13/23).** Prior
   checkpoints have twice flagged that crossing halfway is a reasonable
   point to consider updating `docs/PHASES.md` and re-running the Wave 0
   baseline commands fresh across the whole accumulated batch — still
   true, still not done, still worth raising with whoever's driving this
   next rather than doing unprompted mid-stream. Worth actually raising
   explicitly at the start of the next session if it hasn't come up by
   then.
