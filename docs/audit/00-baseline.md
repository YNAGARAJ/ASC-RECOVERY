# Audit — Wave 0, Step 2: Ground truth

Every command below was actually executed in this session (Windows, Git Bash,
Python 3.12.1, no Docker CLI installed, no live Postgres). Output is real,
not predicted. Where a tool is genuinely unavailable in this environment,
that is stated plainly rather than skipped silently — per the audit's own
instruction, a broken/incomplete baseline is itself the valuable output.

---

## `make test`

Two sub-steps, run exactly as the Makefile defines them.

**`pytest -q`**
```
408 passed, 28 skipped in 18.75s
```
All 28 skips are DB-backed tests across the repo (`tests/db/`, plus
`*_live_db.py` files in `tests/api/` and `tests/ingestion/`) — every one
skips with an explicit `TEST_DATABASE_URL is not set` message, none silently
pass. This environment has no live Postgres, so these have never executed;
the last real execution was Phase 10's CI run against a real Postgres 16
service container (see `docs/PHASES.md`'s Phase 10 entry) — and every DB
change since then (Phase 12) has never been proven against a live database.

**`pytest --cov=src/domain --cov-branch --cov-report=term-missing -q` +
`coverage report --include="*/domain/variance.py" --fail-under=100`**
```
Name                      Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------
src\domain\__init__.py        0      0      0      0   100%
src\domain\contract.py      179      5     64      4    95%   142-144, 215, 224, 228->234
src\domain\deadlines.py      10      0      2      0   100%
src\domain\money.py         105     15     28      8    81%   20, 44, 48-50, 53, 56, 78, 84, 94, 114, 123, 130, 135, 145
src\domain\outcomes.py       27      0      6      0   100%
src\domain\variance.py       79      0     22      0   100%
src\domain\x835.py          347     22    110     24    90%   229-234, 269->266, 290->293, 336, 376, 383, 387,
                                                                396->365, 402, 418-421, 433, 442->365, 446, 455,
                                                                464, 485, 490, 493, 497, 501, 502->505, 508, 515,
                                                                520->522, 525->528
---------------------------------------------------------------------
TOTAL                       747     42    232     36    92%

src\domain\variance.py      79      0     22      0   100%   <- the only file the Makefile's
                                                                 --fail-under=100 gate actually checks
```
The Makefile's hard gate (100% branch coverage on `domain/variance.py` only)
passes. `domain/money.py` sits at 81% and `domain/x835.py` at 90% — neither
is gated by `make test`, and both have real, named uncovered branches (worth
a look in Wave 1's test-quality pass: is the uncovered 19% of `money.py`
dead defensive code, or real untested paths?).

## `make lint`

```
$ ruff check .
All checks passed!

$ mypy --strict .
Success: no issues found in 143 source files
```
Both clean. Note: the Makefile's `lint` target does not itself run the
lockfile-freshness check — that only exists as a separate step inside
`.github/workflows/ci.yml`'s `lint` job, not in `make lint` locally. Running
`make lint` alone will not catch `requirements.lock.txt` drift.

## `make eval`

```
golden cases:        504
lines scored:        571
recall:              100.0% (gate: 100%)
precision:           100.0% (gate: >= 98%)
root-cause accuracy: 100.0%
dollar accuracy:     100.0% (detected 158295.80 vs injected 158295.80)
GATE PASSED
```

## `make security`

```
$ bandit -r . -x ./tests,./evals
No issues identified.
Total lines of code: 6270 (bandit's own counter, comments/blank lines
excluded, differs from 00-inventory.md's raw `wc -l` count for src/ alone
since bandit also scans scripts/ and alembic/)

$ pip-audit
No known vulnerabilities found
(1 package skipped: "asc-recovery" itself isn't on PyPI, expected)

$ gitleaks detect --no-banner
NOT RUN -- gitleaks is not installed in this environment (`command not
found`). This has been true and stated plainly since Phase 4; the only real
execution of gitleaks against this repo's full history was Phase 10's CI
run, which needed a `.gitleaks.toml` allowlist for two confirmed false
positives (synthetic JWT/PHI-encryption test secrets). Nothing in this
session's local run can confirm gitleaks still passes after Phase 11/12's
new files -- that is only proven the next time CI's `security` job runs.
```

## `docker compose build` / `docker compose up`

```
$ docker --version
bash: docker: command not found
```
Docker is not installed in this environment at all — not "daemon not
running," the CLI itself is absent. This has been true since Phase 9 and is
already documented there and in `docs/RUNBOOK.md` ("no Docker... in the
environment this was authored in"). Nothing new to report; restated here
because the audit asked for it to be re-verified, not assumed.

## Attempt a real request against the running service

Docker being unavailable, this was done the only way this environment
allows: running the actual production entrypoint directly.

```
$ PHI_ENCRYPTION_KEY=$(python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")
$ DATABASE_URL="postgresql+psycopg://asc_app:asc_app_dev_password@localhost:5432/asc_recovery" \
  JWT_SECRET_KEY="audit-baseline-test-secret-not-for-production" \
  ANTHROPIC_API_KEY="sk-audit-baseline-placeholder-not-real" \
  PHI_ENCRYPTION_KEY="$PHI_ENCRYPTION_KEY" \
  PYTHONPATH=src python -m uvicorn main:create_app_from_env --factory --port 8124
```
No live Postgres exists at that `DATABASE_URL` (there is no Postgres running
in this environment at all) — this was a deliberate choice, to see whether
the app degrades honestly when its database is unreachable, rather than
crashing or lying about health.

```
GET /healthz  -> 200 {"status":"ok"}
GET /readyz   -> 503 {"status":"not ready"}
GET /findings (no Authorization header) -> 401
  {"error":"request_error","message":"missing bearer token","request_id":"..."}
```

All three are the **correct** shape: liveness (`/healthz`) doesn't touch the
database and reports healthy on process-up alone; readiness (`/readyz`)
correctly detects the unreachable database and reports not-ready rather than
lying; and the auth dependency rejects an unauthenticated request before
ever reaching a repository call, so an unreachable database doesn't even get
attempted for a request that was never going to be allowed anyway. This is
the first time this exact command (`uvicorn main:create_app_from_env
--factory`, the literal `Dockerfile` entrypoint) has been run directly in
this environment outside of `tests/test_main.py`'s in-process construction
test — a small but real step beyond what was previously verified.

**Not attempted**: a request that actually reaches the database (e.g.
`POST /remittances`) — there is no live Postgres here for it to reach, so it
would only prove what `/readyz`'s 503 already proved. That gap closes the
same way every other DB-backed gap in this repo closes: the next real
Postgres this branch's CI run gets.
