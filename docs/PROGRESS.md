# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 7 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 7/23** (F-01 through F-07 — see
below). 16 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each carrying its own one-line defer-justification in the
register — not being worked yet, and that's fine, they're not blocking).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-06** — see git log (`824b26a`,
  `3d18d03`, `f51fb31`, `8d17361`, `5f8d462`) and the prior checkpoint for
  detail; unchanged this session.
- **F-07 (HIGH, this session)** — the AWS deployment target was
  unreachable end to end: no `aws_lb_listener` resource existed at all
  (not just missing TLS), no HSTS, and `make_engine` trusted whatever
  `sslmode` (or lack of one) a `DATABASE_URL` happened to carry. Two
  commits, register updated to FIXED in `e5df688`:
  - **`cf6c3e4`** — `terraform/environments/aws/main.tf` gained the two
    listener resources the module's own comment always said belonged at
    that layer: an HTTPS listener on 443 (new required `certificate_arn`
    variable, no default — needs a real domain's already-issued ACM
    cert, which this build environment doesn't have) forwarding to the
    app's target group, and an HTTP listener on 80 that only ever
    redirects to 443, never forwards a plaintext request.
    `terraform/modules/aws/outputs.tf` gained `alb_arn`/`target_group_arn`
    outputs so the root can attach to them; `container_runtime.tf` gained
    the matching port-80 security-group ingress rule (the redirect
    listener needs an inbound path to answer on before it can redirect
    anything). `.github/workflows/deploy.yml` + `docs/RUNBOOK.md` wired a
    new `AWS_ACM_CERTIFICATE_ARN` repo secret through both AWS
    `terraform apply` steps, added to the staging job's existing
    `if: secrets.* != ''` guard so the AWS deploy jobs stay honestly
    SKIPPED (not failed) until that secret exists too.
    `src/db/base.py`'s `make_engine` now defaults to `sslmode=require`
    whenever a `DATABASE_URL` doesn't already specify `sslmode`, instead
    of trusting psycopg's own default (`prefer`, which silently falls
    back to plaintext). AWS's own secret already carries an explicit
    `?sslmode=require`, so this changes nothing for AWS's real path —
    it's real defense in depth against exactly the gap B-43 documents
    for *Azure's* `DATABASE_URL` secret today (B-43 itself stays open;
    this doesn't fix it, just narrows its blast radius). Local dev and
    CI's plain (non-TLS) `postgres:16` containers needed an explicit
    `?sslmode=disable` added to their connection strings
    (`docker-compose.yml`, `.github/workflows/ci.yml`,
    `docs/DB_SETUP.md`) — an opt-out that's now visible in the
    connection string instead of a silent absence. New
    `tests/db/test_base.py` (4 cases, pure — `create_engine` never opens
    a connection until first use, so this needed no live Postgres).
  - **`f118ccf`** — a same-day follow-up closing the HSTS gap the same
    finding named but the first commit hadn't yet addressed. New
    `src/api/security_headers.py`: `SecurityHeadersMiddleware` (raw ASGI,
    matching `RequestIDMiddleware`'s established pattern) adds
    `Strict-Transport-Security: max-age=63072000; includeSubDomains` to
    every response, wired into `api/app.py` alongside the existing
    request-ID middleware. 3 new tests
    (`tests/api/test_security_headers.py`) prove it's unconditional —
    present on a 200, a 422, and an unauthenticated 401, not just the
    happy path.

Same verification ceiling as every prior Terraform-touching fix this
Wave: no Terraform CLI, no live AWS account in this environment, so the
`.tf` changes are offline-verified only (brace/paren-balance-checked,
matches every existing resource's HCL style, `certificate_arn`'s
no-default choice deliberately makes a real `terraform plan` fail loudly
and clearly rather than silently reference a nonexistent certificate).
The Python (`make_engine`, `SecurityHeadersMiddleware`) and YAML
(`deploy.yml`, `ci.yml`, `docker-compose.yml`) halves are genuinely,
fully verified — pure Python needing no live DB, YAML parse-checked.

## In progress

Nothing mid-write. F-07 went through the full Wave 3 loop (state the
finding → write/extend tests → minimal fix → show it passing locally →
full local gate → mark FIXED in the register with the commit SHA(s) →
commit) and is complete as a unit, same as F-01 through F-06. It happened
to land as two commits instead of one because the HSTS third of the
finding was caught on a second read-through of the finding text after
the first commit — see "Traps" below.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(153 files), `pytest -q` (440 passed, 29 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest --cov=src/domain --cov-branch -q` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `e5df688`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely (confirmed via `git stash` two sessions ago). Still not blocking,
still out of scope for this checkpoint — flagging again so it doesn't get
silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **`make_engine`'s new sslmode default only kicks in when `sslmode` is
  completely absent from the URL** — it never overrides an explicit
  value, including `sslmode=disable`. This was a deliberate choice to
  keep local dev and CI working (their Postgres containers have no TLS
  configured at all) without adding an environment-variable escape hatch
  or a `require_ssl: bool` parameter that production code could
  accidentally set to `False`. The opt-out is now a visible
  `?sslmode=disable` in three specific, known, non-production connection
  strings instead of an implicit absence — auditable by grepping for
  `sslmode=disable` and confirming every hit is local/CI, not a silent
  default anyone could quietly rely on in a real deployment.
- **`certificate_arn` has no default value.** A tempting alternative was
  a `null`-able variable with a `count = var.certificate_arn != null ? 1 : 0`
  guard on the listener resources, so `terraform plan` would succeed
  either way. Rejected: that would make "no certificate configured"
  degrade silently back to exactly F-07's original bug (ALB with no
  listener), just reachable again without anyone noticing. A required
  variable with no default fails `terraform plan` immediately and
  loudly instead — the correct failure mode for "this deployment is not
  actually configured for HTTPS yet."
- **B-43 (Azure's `DATABASE_URL` omitting `sslmode=require`) was
  deliberately left open**, not folded into this fix even though
  `make_engine`'s new default happens to blunt its practical impact
  (Azure's connection now gets `sslmode=require` injected client-side
  even though Terraform never states it explicitly). B-43 is really
  about the Terraform secret itself being unclear/inconsistent with
  AWS's, which is a real, separate documentation/explicitness gap this
  session's fix doesn't address — recorded in the register as its own
  backlog row and should stay that way until someone deliberately picks
  it up.
- **F-07 landed as two commits, not one** — a deliberate choice once the
  gap was noticed, not a sign of rework. `cf6c3e4` closed the listener
  and `sslmode` two-thirds of the finding; re-reading the finding's own
  text afterward ("no HTTPS termination, no HSTS") surfaced that HSTS
  had been silently dropped from scope during implementation. Rather
  than amend `cf6c3e4` (this repo's own git-safety convention prefers a
  new commit over amending), `f118ccf` closed the remaining third
  same-day, and both SHAs are recorded together in the register's FIXED
  cell. Worth knowing: always re-read a finding's *full* description
  once more right before marking it FIXED, not just the "Fix" column's
  summary — the summary and the "What breaks" column don't always list
  every sub-part in the same words.

## Traps for someone resuming cold

- **Everything F-01 through F-06's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content guardrail
  hook rejecting two specific words landing next to each other or a
  non-allowlisted synthetic email domain, the Makefile's
  `domain/variance.py`-only coverage gate, and remembering
  `dependencies=[Depends(enforce_rate_limit)]` on any new authenticated
  router).
- **A finding's "What breaks" / fix-column text can list more than one
  distinct sub-fix** (F-07 named three: listener, HSTS, sslmode). Before
  marking any future finding FIXED, re-read its full row one more time
  against the actual diff, not just the todo list built from a first
  pass — see this session's own near-miss above.
- **`terraform/README.md`'s "every module exposes the same outputs"
  table was intentionally *not* updated** for the new AWS-only
  `alb_arn`/`target_group_arn` outputs, matching the precedent F-02/F-03
  already set (AWS's `database_admin_secret_id`/`app_db_password` and
  Azure's `database_admin_username`/`database_admin_password` are also
  real, cloud-specific outputs never added to that shared table). If a
  future session decides the table should actually be exhaustive, that's
  a documentation-cleanup pass across all three fixes' outputs at once,
  not something to do piecemeal on the next finding that happens to add
  one more output.

## Next 3 steps

1. **F-08 and F-09 next, together** — the register itself notes they
   share one fix site: `main.py`'s `PostgresRepository(...)` construction
   never passes `instruments=`/`tracer=`, so every ingestion metric and
   the one span in the codebase both go to no-op providers. Same
   "do related findings together" call already made for F-04/F-05.
   Should be a small, fully offline-verifiable Python fix (construct a
   real in-memory exporter in a test, assert a metric/span actually
   reaches it) — no Terraform/infra half this time.
2. **After F-08/F-09**, continue F-10 through F-23 in `REGISTER.md`'s own
   listed order, same loop each time (state finding → test → fix → gate →
   mark FIXED with SHA → commit) — and per the decision above, re-read
   each finding's full row once more right before marking it FIXED.
3. **Once the MUST-FIX list is meaningfully further along**, update
   `docs/PHASES.md` to note Wave-3 remediation progress and re-run the
   Wave 0 baseline commands fresh across the accumulated batch, before
   telling the user this phase of remediation is done. 7/23 still isn't
   "meaningfully further along" territory by itself; revisit after
   roughly F-12 or so lands.
