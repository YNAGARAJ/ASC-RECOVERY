# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit (`git status` empty) as of this checkpoint: `1521f23`
"Phase 4: security and PHI controls". Read `docs/PHASES.md` first for the
phase checklist — this file adds the texture that isn't in that summary.

## Phase: 4 done and verified. 3 is code-complete but its gate is unverified.

Phases 0–2 and 4 are fully done: code written, gate criteria genuinely
checked, nothing hand-waved. **Phase 3 is the exception** — it is code
complete (schema, migrations, tenancy, repository layer, all four gate
tests written) but its actual hard gate (Row-Level Security blocking a
cross-tenant read, proven against a *live* Postgres) has never been run.
This machine has no Docker, no WSL, and no local Postgres — only `pip`
works. The user explicitly chose "write it now, verify later" and later
chose to continue past it rather than block. **Do not treat Phase 3 as
passed.** `docs/PHASES.md` reflects this correctly (Phase 3 unchecked,
Phase 4 checked).

Current phase per `docs/PHASES.md`: **Phase 5 — Ingestion pipeline**, not
yet started.

## Done (this session, Phases 2–4)

**Phase 2 — eval harness** (`evals/`, `tests/evals/`):
- `evals/generator.py` — synthetic X12 835 generator, 9 defect categories
  (implant carve-out ignored, MPPR applied to primary, bilateral modifier
  dropped, stale fee schedule, duplicate line, reversal after payment,
  secondary-payer underpayment, unpriced code, correct-payment control),
  56 cases each. Ground truth is derived independently of
  `domain.variance.evaluate_claim` — see the module docstring for why that
  matters (it's what makes the regression gate meaningful, not tautological).
- `evals/golden/cases.py` — **generated, frozen, committed.** 504 cases as
  literal Python (not JSON). Never hand-edit; regenerate via
  `python -m evals.generator` only if the generator itself changes.
- `evals/run.py` — runs parse→price→evaluate against the golden set,
  reports recall/precision/root-cause/dollar accuracy. `make eval` fails
  the build below 100% recall / 98% precision.

**Phase 3 — persistence** (`src/db/`, `alembic/`, `tests/db/`,
`docker-compose.yml`, `scripts/db/init_roles.sql`, `docs/DB_SETUP.md`):
- `src/db/models.py` — SQLAlchemy 2.0 ORM models: tenants, contracts,
  contract_versions (rule sub-structures as JSONB), fee_schedule_lines,
  remittances (unique on tenant_id+file_hash), claims, service_lines,
  adjustments, findings (carries `rule_version`), audit_log,
  phi_access_log.
- `alembic/versions/0001_initial_schema.py` — creates tables via
  `Base.metadata.create_all`, then hand-written RLS: `ENABLE` + `FORCE ROW
  LEVEL SECURITY` and a `tenant_isolation` policy on every tenant-scoped
  table, plus grants scoped to a non-superuser `asc_app` role and an
  explicit `REVOKE UPDATE, DELETE` on `audit_log`/`phi_access_log`.
  Verified via `alembic upgrade head --sql` (offline SQL generation) —
  this is NOT the same as running it against real Postgres.
- `src/db/tenancy.py` — `tenant_session()`: sets `app.tenant_id` via
  `SELECT set_config(..., true)` (transaction-local) as the first
  statement of every transaction. Deliberately not `SET LOCAL` directly —
  see Decisions below.
- `src/db/repository.py` — thin persistence functions bridging Postgres
  and the Phase 1 domain layer (JSONB↔dataclass serialization for contract
  rules, idempotent remittance recording, effective-dated contract lookup
  that hands off to the already-tested `domain.contract.find_effective_contract`).
- `tests/db/` — four gate-proving test files + `conftest.py`. All skip
  with an explicit message (not silently pass) unless `TEST_DATABASE_URL`
  is set. **Never actually run against a live database in this session.**

**Phase 4 — security** (`src/security/`, `tests/security/`,
`docs/SECURITY.md`):
- `src/security/kms.py` + `kms_local.py` — `KeyManagementService` port +
  in-memory dev/test adapter. No real cloud KMS adapter exists yet
  (Phase 9 scope).
- `src/security/encryption.py` — `EnvelopeEncryptor`, AES-256-GCM.
  `rotate_kek()` re-wraps only the DEK, proven not to touch ciphertext.
- `src/security/secrets.py` — `SecretStore` port + `EnvSecretStore` dev
  adapter (env vars only, never a committed file).
- `src/security/rbac.py` — `Role`/`Action` enums, deny-by-default
  `can(role, action)` lookup table.
- `src/security/mfa.py` — TOTP enrollment/verification via `pyotp`.
- `src/security/session.py` — JWT access+refresh issuance.
  `issue_session()` is the only function that can mint a session from a
  bare role, and refuses without `mfa_verified=True`. Refresh rotates the
  token id and carries the original `auth_time` forward.
- `src/security/rate_limit.py` — token-bucket rate limiter +
  `AccountLockoutTracker`, both in-memory/single-process for now.
- `src/security/redaction.py` — `PHIRedactionFilter` (logging.Filter):
  redacts denylisted structured fields, regex-scrubs SSN/MBI-shaped text
  in messages, args, and exception text.
- `docs/SECURITY.md` — every control mapped to its HIPAA citation, plus an
  honest "not yet built" section.

Also this session: fixed the `Makefile`'s `bandit` exclude (`-x
tests,evals` was excluding nothing on this platform; now `-x
./tests,./evals`), added `sqlalchemy`, `alembic`, `psycopg[binary]`,
`cryptography`, `pyjwt`, `pyotp` to `pyproject.toml`'s `dev` extra.

## In progress

Nothing is half-written. The working tree is clean and every file
committed is in a finished state for its phase's scope. Phase 5
(ingestion pipeline) has not been started — no files, no plan.

## Failing

Nothing is currently failing. `pytest -q` → 219 passed, 11 skipped (the 11
are `tests/db/`, skipped by design, not broken). `mypy --strict .` and
`ruff check .` both clean across 53 source files. `python -m evals.run` →
100% recall/precision/root-cause/dollar accuracy, gate passed.

`make security`: `bandit -r . -x ./tests,./evals` clean; `pip-audit`
reports 14 findings but every one is in `dulwich`/`msgpack` (transitive
deps of `poetry`/`CacheControl` — unrelated global tools sharing this
machine's Python install) or in `pip` itself, none in this project's
actual dependencies — see the note in `docs/SECURITY.md`'s control table.
`gitleaks` was never run (binary not installed, not pip-installable).

## Decisions worth knowing (not obvious from the code)

- **Phase 3's gate was left unverified rather than faked or skipped.**
  Both explicitly discussed with the user, who chose to continue rather
  than block. If you're picking this up cold: don't "fix" this by writing
  a mock Postgres or an in-memory RLS simulation — that would prove
  nothing about real RLS enforcement and would be actively misleading.
  The only real fix is running `docs/DB_SETUP.md`'s steps against an
  actual Postgres 16.
- **`tenant_session()` uses `set_config(..., true)`, not `SET LOCAL`
  directly.** `SET LOCAL app.tenant_id = :param` does not reliably accept
  a bind parameter for its value (Postgres's `SET` grammar doesn't support
  placeholders the way normal DML does) — `set_config()` is a real
  function call that does. Don't "simplify" this back to `SET LOCAL`.
- **Migration uses `Base.metadata.create_all(bind=op.get_bind())`
  instead of per-table `op.create_table()` calls.** Deliberate: avoids
  duplicating every column definition between `models.py` and the
  migration. The security-critical parts (RLS, grants, policies) are
  still hand-written explicitly in the migration, which is where control
  actually matters.
- **`asc_app` (the app runtime role) is not a superuser and has no
  `BYPASSRLS`**, created by `scripts/db/init_roles.sql` (mounted into
  Postgres's `docker-entrypoint-initdb.d`) before Alembic runs. This is
  what makes the RLS test a genuine proof rather than a superuser
  false-pass. Table grants (including `tenants`: `SELECT, INSERT`) are set
  in the migration, not the init script, since tables don't exist yet at
  initdb time.
- **Golden eval dataset (`evals/golden/cases.py`) ground truth is
  deliberately NOT computed by calling `domain.variance.evaluate_claim`.**
  It's computed independently (expected side via `price_claim`, actual
  side via hand-written injection arithmetic). If you ever see someone
  "simplify" the generator to just call `evaluate_claim` and snapshot its
  output — don't. That makes recall/precision trivially 100% forever and
  defeats the entire harness. This was caught and fixed once already this
  session (a scoring-logic bug, not the generator, but same principle: see
  next point).
- **`evals/run.py`'s `score_cases` checks BOTH `shortfall > tolerance` AND
  `root_cause != CORRECT_NO_VARIANCE`** for a line to count as "detected."
  Originally it only checked shortfall magnitude, which meant a
  classifier bug that computed the right dollar amount but mislabeled the
  root cause `CORRECT_NO_VARIANCE` would NOT have been caught by the
  eval. Found this by literally breaking `variance.py` on purpose to prove
  the gate works, per the Phase 2 gate requirement — don't remove the
  root_cause check thinking it's redundant with the shortfall check; it
  isn't, they catch different bug classes.
- **`session.py`'s MFA-bypass proof is a structural/introspection test**
  (`inspect.signature` — no other public function accepts a `role`
  parameter), not just a happy-path assertion. It proves the module has no
  second door, not that a future Phase 6 login endpoint will always pass
  an honest `mfa_verified` value — that's a Phase 6 concern.

## Traps for someone resuming cold

- **`evals/golden/cases.py` is excluded from ruff via
  `pyproject.toml`'s `[tool.ruff] extend-exclude`** — it's generated,
  ~650KB, one case per line. Don't hand-edit it, don't be alarmed that it
  fails a normal line-length lint if you check it manually outside `ruff
  check .`.
- **No project-local virtualenv.** Everything (`sqlalchemy`, `alembic`,
  `psycopg`, `cryptography`, `pyjwt`, `pyotp`, `bandit`, `pip-audit`, ...)
  was installed into the global user Python 3.12 install
  (`C:\Users\523na\AppData\Local\Programs\Python\Python312`). This is why
  `pip-audit` picks up unrelated tools (`poetry`, `CacheControl`). Worth
  fixing with a real venv before this matters more, but wasn't done this
  session — don't assume isolation that doesn't exist.
- **`git add -A` on Windows will warn about LF→CRLF on every file** —
  harmless, not a sign anything is wrong, don't try to "fix" line endings
  mid-session.
- **`tests/db/` fixtures depend on fixture *scope* ordering, not
  declaration order or `autouse`.** `app_session_factory` and
  `owner_engine` are session-scoped and do the `TEST_DATABASE_URL` check
  themselves — a separate function-scoped `autouse` skip-guard fixture was
  tried first and silently ran *after* the session-scoped fixtures had
  already failed, because pytest sets up broader-scoped fixtures first
  regardless of `autouse`. If you add new fixtures to that conftest, keep
  the skip check inside every fixture that's actually depended on, not in
  a separate guard.
- **`docker-compose.yml` and `scripts/db/init_roles.sql` use fixed local
  dev passwords** (`asc_owner_dev_password`, `asc_app_dev_password`).
  These are fine for a local-only container never exposed beyond
  localhost — don't mistake them for something needing rotation, and
  don't reuse them anywhere real.

## Next 3 steps

1. **If Postgres becomes available:** follow `docs/DB_SETUP.md` exactly
   (`docker compose up -d`, `alembic upgrade head` as `asc_owner`, then
   `TEST_DATABASE_URL=... pytest tests/db/ -v` as `asc_app`). If all four
   gate tests pass, check off Phase 3 in `docs/PHASES.md` — don't check it
   off for any lesser reason.
2. **Otherwise, start Phase 5 (ingestion pipeline)** per
   `docs/MASTER-BUILD-PROMPT.md`: file/SFTP/S3 upload adapters behind a
   port (same pattern as `security.kms`/`security.secrets` — one
   interface, swappable adapters), idempotency by content hash (reuse
   `db.repository.record_remittance_if_new`, already built in Phase 3),
   quarantine for invalid files, partial-batch handling, BPR
   reconciliation, reversal/takeback netting against prior findings. Enter
   plan mode first, same as every prior phase this session — don't skip
   the plan step even though the pattern is now familiar.
3. Either way, **do not skip `/clear`-equivalent context hygiene** if this
   checkpoint is being read at the start of a fresh session — that's the
   point of this file existing at all.
