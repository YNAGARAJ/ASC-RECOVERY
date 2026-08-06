# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 6 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 6/23** (F-01 through F-06 — see
below). 17 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-05** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`) and the prior checkpoint for detail;
  unchanged this session.
- **F-06 (HIGH, this session)** — `enforce_rate_limit`
  (`api/rate_limit.py`) and `AccountLockoutTracker`
  (`security/rate_limit.py`) were both complete and unit-tested since
  Phase 4, wired to zero routes: every endpoint (including PHI-decrypting
  reads) was unthrottled, and F-04's new login route had no lockout to
  hit even after it existed. Commit `5f8d462` (register updated to FIXED
  in `357910e`):
  - `api/rate_limit.py` — `enforce_rate_limit` now reads its limiter off
    `request.app.state.rate_limiter` instead of a module-level singleton.
    A module-level limiter would have persisted across every
    `create_app()`/`TestClient` built during a test session, so an
    earlier test could silently exhaust a later, unrelated test's budget
    for the same `(tenant, user)` key purely from run order — moved to
    per-app state for the same reason `app.state.repository`/
    `jwt_secret_key` already are, not only to dodge that specific risk.
  - `api/app.py` — `create_app()` now builds a fresh `RateLimiter` and
    `AccountLockoutTracker` per call, both accepted as optional override
    params so a test can inject a tiny-capacity/tiny-threshold instance
    instead of sending dozens of real requests to exercise the
    429/lockout paths.
  - `api/routes/{audit,contracts,findings,packets,remittances}.py` —
    each router now carries `dependencies=[Depends(enforce_rate_limit)]`.
    `health.py` and `auth.py` (login) are deliberately excluded:
    `enforce_rate_limit` requires an already-authenticated
    `AuthContext`, which doesn't exist for either — health checks carry
    no bearer token by design, and login is the thing that *produces*
    an `AuthContext`, not something that can require one first.
  - `api/routes/auth.py` — `POST /auth/login` now checks
    `AccountLockoutTracker.is_locked_out(subject)` before any password
    verification (a cheap dict lookup, so a locked-out subject doesn't
    also pay for a scrypt hash on every retry), and calls
    `record_failure`/`record_success` after. A locked-out account gets
    the same generic 401 as every other failure mode — consistent with
    the route's existing never-disclose-which-check-failed design
    (see F-04/F-05's checkpoint entry).
  - New tests: `tests/api/test_rate_limit.py` (4 cases — 429 once
    capacity is exhausted, independent budgets per `tenant:user`, a
    shared budget across different routes on the same router, health
    endpoints unaffected by even a capacity-1 limiter), plus 5 new cases
    appended to `tests/api/test_login.py` (lockout after 5 failures — the
    tracker's own default `max_failures` — a locked-out account never
    reaches `issue_session` even with fully correct credentials, a
    success resets the failure count, lockout is scoped per-subject).

Same verification ceiling as every prior fix this Wave: no live Postgres/
Terraform/cloud account in this environment, so nothing DB- or infra-
backed here was exercised against the real thing. F-06 itself has no
DB-backed piece though — both `RateLimiter` and `AccountLockoutTracker`
are pure in-memory Python, so this fix is fully, genuinely verified
end-to-end (route wiring included), not just offline-checked like the
Terraform/migration-heavy fixes were.

## In progress

Nothing mid-write. F-06 went through the full Wave 3 loop (state the
finding → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA →
commit) and is complete as a unit, same as F-01 through F-05.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(150 files), `pytest -q` (433 passed, 29 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `357910e`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Confirmed via
`git stash` in the F-04/F-05 session that these predate Wave 3 entirely.
Still not blocking, still out of scope for this checkpoint — flagging again
so it doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **`enforce_rate_limit` was moved off a module-level singleton onto
  `app.state` as part of this fix**, not left as originally written. The
  register's fix text only said "attach `enforce_rate_limit` as a
  router-level dependency" — it didn't flag the module-level `_limiter`
  as a problem. It became one the moment the dependency was actually
  attached to routes: `tests/api/conftest.py`'s `app`/`client` fixtures
  are function-scoped (a fresh `FastAPI` app per test), but a
  module-level limiter is process-wide and would have been shared and
  slowly drained across every single test in the whole pytest session
  regardless of which app instance served the request — a real, if slow-
  building, source of order-dependent flakiness the register's fix text
  didn't anticipate because nothing had exercised the dependency end to
  end yet. Caught by reasoning through the test suite's fixture scoping
  before writing tests, not by a failure — worth knowing in case this
  pattern reappears somewhere else `Depends(...)` wraps mutable
  module-level state.
- **`enforce_rate_limit` is intentionally *not* applied to `POST
  /auth/login`.** It structurally can't be — it depends on
  `get_auth_context`, i.e. it requires the very token that login is the
  thing producing. Brute-force protection on login is `AccountLockoutTracker`
  alone (per-subject), not the general request limiter. A horizontal
  attack trying many *different* subjects from one source isn't covered
  by either mechanism — that would need IP-based limiting, which isn't
  what F-06's fix text asked for and isn't built here. Worth a future
  finding if it matters, not silently assumed covered.
- **A locked-out login attempt skips password verification entirely**
  (checked first, before the scrypt hash), trading a small amount of
  timing-side-channel purity (a locked-out response is now faster than a
  not-locked-out wrong-password response) for not spending a real scrypt
  computation on every retry against an account already known to be
  locked. This is a different, accepted trade-off from the unknown-
  subject-vs-wrong-password timing consistency F-04/F-05 specifically
  built in — that one was about not leaking *subject existence*; lockout
  state being somewhat observable via timing is a standard, accepted
  property of basically every real-world account-lockout system, not an
  oversight.
- **Both `RateLimiter` and `AccountLockoutTracker` remain in-memory,
  single-process**, exactly as the register's own fix text flagged
  ("need a shared store before >1 replica"). Both clouds' Terraform
  already defaults `desired_count`/`min_replicas` to 2 (backlog item
  B-46) — meaning this in-memory state silently stops being a real limit
  the moment a second replica exists, since each replica has its own
  independent budget/lockout state. Not solved here; solving it needs a
  Redis (or equivalent) adapter behind the same `RateLimiter`
  Protocol/`AccountLockoutTracker`-shaped port, which needs a real
  deployment target to build against — same "no live infra in this
  environment" ceiling as F-07/F-19/F-20/F-21.

## Traps for someone resuming cold

- **Everything F-01 through F-05's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook rejecting two specific words landing next to each other or a
  non-allowlisted synthetic email domain, and the Makefile's
  `domain/variance.py`-only coverage gate being much narrower than
  `pytest --cov=src/domain --cov-fail-under=100` would suggest).
- **If a future finding adds another authenticated router**, remember to
  add `dependencies=[Depends(enforce_rate_limit)]` to its `APIRouter(...)`
  construction explicitly — there's no central registry enforcing this,
  and nothing will fail loudly if a new router forgets it (the route will
  simply be unthrottled, silently, the same failure mode F-06 just fixed
  for the existing five). Worth a structural test (parametrized over
  `app.routes`, similar in spirit to `test_tenant_param_absence.py`)
  if a sixth authenticated router is ever added — not written this
  session since there's nothing yet to test it against beyond the
  five that already carry it by construction.

## Next 3 steps

1. **F-07 next** per `REGISTER.md`'s own listed order — AWS deployment
   target is unreachable end to end (no `aws_lb_listener` resource
   exists at all in `terraform/modules/aws/container_runtime.tf`), plus
   `make_engine` should defensively require `sslmode=require`. The
   Terraform half is genuinely infra-shaped (needs a real domain/ACM
   cert to fully verify) but the `sslmode` half of the fix is a small,
   fully offline-verifiable Python change — worth splitting those two
   halves explicitly when picking this one up, same way F-01's DB-backed
   and pure-logic halves were split.
2. **After F-07**, continue F-08 through F-23 in `REGISTER.md`'s own
   listed order, same loop each time (state finding → test → fix → gate →
   mark FIXED with SHA → commit). F-08/F-09 are noted in the register as
   sharing one fix site (`main.py`'s `PostgresRepository(...)`
   construction missing `instruments=`/`tracer=`) — worth doing together
   like F-04/F-05 were.
3. **Once the MUST-FIX list is meaningfully further along**, update
   `docs/PHASES.md` to note Wave-3 remediation progress and re-run the
   Wave 0 baseline commands fresh across the accumulated batch, before
   telling the user this phase of remediation is done. 6/23 still isn't
   "meaningfully further along" territory by itself; revisit after
   roughly F-12 or so lands.
