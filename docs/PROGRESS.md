# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint (about to commit "Phase 6: API
layer"). Read `docs/PHASES.md` first for the phase checklist — this file
adds the texture that isn't in that summary.

## Phase: 6 code-complete, DB-writing half unverified. Same story as 3 and 5.

Phases 0–2 and 4 are fully done. Phases 3, 5, and 6 each have an honest
gap: this machine has no Docker, no WSL, and no local Postgres — only
`pip` works (re-checked again this session; unchanged). Everything
checkable without a live database is green for all three. The DB-writing
parts are written, tested where a test can exist without Postgres, and
explicitly **not** claimed as passed.

Current phase per `docs/PHASES.md`: **Phase 6 — API layer**, code-complete,
pure/authz-matrix half verified, live-Postgres half unverified.

## Done (this session, Phase 6)

**Key architectural decision**, same shape as Phase 5's plan/apply split:
route handlers depend on an `api.repository.Repository` Protocol port
(mirrors `security.kms`/`ingestion.sources`), never on SQLAlchemy
directly. Two adapters: `PostgresRepository` (real, wraps `db.repository` +
`db.tenancy.tenant_session`) and `FakeRepository` (test-only, in-memory,
tenant-partitioned, `tests/api/fakes.py`). This is what let the **full
authorization matrix** — every one of 4 roles x 8 endpoints x
own-tenant/other-tenant — run as real, passing tests in this Postgres-less
environment instead of joining Phase 3 in permanent-skip limbo.

**Two things discovered during exploration that shaped the whole design**:
1. FastAPI/Pydantic v2/uvicorn/httpx were not installed despite CLAUDE.md's
   stack line naming them — added to `pyproject.toml`'s `dev` extra and
   installed (`fastapi`, `uvicorn`, `pydantic`, `httpx`, `python-multipart`,
   `openapi-spec-validator`).
2. `security.session.AccessTokenClaims` (Phase 4) carries no `tenant_id` —
   a JWT only proves `(user_id, role, auth_time)`. Resolving `user_id ->
   tenant_id` needs a lookup that can't itself be tenant-scoped (RLS needs
   `app.tenant_id` set, but that's exactly what's being looked up) — solved
   with a new `users` table, structured ungated like `tenants` (see
   `src/db/models.py`'s `User` docstring). This was **not** a change to
   Phase 4's `security/session.py` — that file is untouched.

**`src/api/`** (new package):
- `repository.py` — the `Repository` Protocol, all the shared
  DB-agnostic dataclasses (`FindingSummary`, `FindingDetail`,
  `ContractSummary`, `AuditLogEntry`, `PagedResult[T]`, ...) with money
  always `str` (CLAUDE.md rule 2), and `PostgresRepository`.
- `auth.py` — `AuthContext` (`user_id, role, tenant_id, request_id`);
  `get_auth_context` dependency (bearer token -> `validate_access_token`
  (Phase 4, unchanged) -> `Repository.get_user_by_subject` -> tenant_id);
  `require_permission(action)` wrapping `security.rbac.can`. **No route
  anywhere accepts a client-supplied tenant identifier** — not path,
  query, or body. Proven two ways in `test_tenant_param_absence.py`:
  inspecting every route's actual function parameters, and inspecting the
  generated OpenAPI schema. This is what makes "no endpoint returns
  another tenant's data under any parameter manipulation" true by
  construction rather than by a defensive check.
- `rate_limit.py`, `errors.py` (structured errors that never echo PHI —
  full detail logged through a `PHIRedactionFilter`-attached logger,
  response body only ever gets a generic code+message+request_id),
  `schemas.py` (Pydantic v2 request/response models), `request_context.py`
  (request-id middleware — see the `BaseHTTPMiddleware` trap below),
  `app.py` (FastAPI app factory, takes `Repository` and JWT secret as
  explicit arguments, injected — not constructed internally).
- `routes/`: `remittances.py` (`POST /remittances`), `findings.py`
  (`GET /findings`, `GET /findings/{id}`, `GET /findings/export.csv`),
  `contracts.py` (`GET /contracts`, `POST /contracts`,
  `POST /contracts/{id}/versions`), `audit.py` (`GET /audit-log`).

**`src/db/`**: `User` model (ungated) + `AuditLog.request_id` column,
`alembic/versions/0003_users_and_audit_request_id.py` (offline-verified
only). `repository.py` additions (additive, confirmed via grep these
didn't exist before): `get_user_by_subject`, `create_user`,
`list_contracts`, `list_findings`, `get_finding_detail` (+ new
`FindingDetail` dataclass), `list_audit_log`; `write_audit_log` gained an
optional `request_id` param.

**`src/security/rbac.py`**: added `Action.READ_CONTRACT` (there was
previously no way to express "can view contracts" separately from the
existing all-or-nothing `MANAGE_CONTRACT`). Granted to all four roles.
`tests/security/test_rbac.py`'s hand-maintained exhaustive matrix updated
in lockstep — still green, now 47 parametrized cases (was 40).

**`tests/api/`** — `fakes.py` (`FakeRepository`), `conftest.py` (seeds two
tenants' worth of findings/contracts/audit entries/users, mints real JWTs
per role via `issue_session` — the auth path itself stays real, only the
Postgres-backed data layer is faked), `test_authz_matrix.py` (the core
gate: 32 passing cases), `test_tenant_param_absence.py`,
`test_openapi.py`, `test_csv_export.py`, `test_error_redaction.py`,
`test_pagination.py`, `test_smoke.py`, and `test_endpoints_live_db.py`
(2 tests against real `PostgresRepository` + real RLS — skip cleanly
without `TEST_DATABASE_URL`, never executed here).

**Deliberate scope boundaries** (not gaps to "fix" later without cause):
- No login/credential/OIDC-callback HTTP endpoint. Phase 4 built the
  token primitives but no password/MFA-code verification exists anywhere
  to wire a real login endpoint to. Tests mint tokens directly via
  `issue_session()`, exactly as a real login endpoint would after
  verifying credentials.
- No user-management HTTP endpoint. Not in the Phase 6 prompt's endpoint
  list; `users` provisioning is operational/seeding here, not an API
  surface.
- CSV export and finding-detail responses may include PHI (patient
  name/member id) for authorized roles — realistic (a biller needs the
  patient's name to work an appeal). The "no PHI in URLs or query
  strings" requirement is specifically about query strings landing in
  access logs, not response bodies.

## Failing

Nothing. `pytest -q` → **282 passed, 16 skipped** (11 from `tests/db/`, 3
from `tests/ingestion/`, 2 new from `tests/api/test_endpoints_live_db.py`
— all honest skips, not broken). `mypy --strict .` and `ruff check .`
clean across **96 source files** (was 71). `python -m evals.run` →
100%/100%/100%/100%, unaffected. `bandit -r . -x ./tests,./evals` clean.
Branch coverage on `domain/variance.py` still 100% (Phase 6 didn't touch
domain files). `alembic upgrade head --sql` clean through 0003.

## Decisions worth knowing (not obvious from the code)

- **`RequestIDMiddleware` is raw ASGI middleware, not
  `BaseHTTPMiddleware`.** Found this the hard way: `BaseHTTPMiddleware`
  has a well-documented Starlette gotcha where it interacts badly with
  registered exception handlers for specific exception types (like our
  `HTTPException` handler) — user middleware added via
  `BaseHTTPMiddleware` sits at a layer where downstream exceptions don't
  reliably route through `app.exception_handler`-registered handlers
  first. Switched to a plain ASGI middleware class (`__call__(scope,
  receive, send)`) instead, which sits at the correct layer. If you're
  tempted to add a new middleware and reach for `BaseHTTPMiddleware`
  because it looks more ergonomic — don't, without checking this class
  first as the reference pattern.
- **`ServerErrorMiddleware` (Starlette, wraps the whole app) always
  re-raises the original exception after generating the 500 response** —
  by design, so a real ASGI server's logs see it, even though the client
  still gets the proper response. `TestClient`'s default
  `raise_server_exceptions=True` re-surfaces that re-raise as a test
  failure. `test_error_redaction.py` constructs its own
  `TestClient(app, raise_server_exceptions=False)` for the one test that
  deliberately triggers an unhandled exception — every other test uses
  the shared `client` fixture (default `raise_server_exceptions=True`,
  which is what you want everywhere else, since an unhandled exception in
  any other test should fail loudly).
- **`db.repository.list_contract_versions`'s return type change from
  Phase 5 (`list[tuple[UUID, ContractVersion]]`) is exactly why
  `api.repository`'s `create_contract_version` doesn't need a `payer_id`
  parameter.** `db_repository.create_contract_version` never actually
  persists `ContractVersion.payer_id` (it's not a `contract_versions`
  column — payer_id lives on the parent `Contract` row) — confirmed by
  reading that function before adding the API-layer plumbing, then
  deliberately *removing* a `payer_id` parameter that had been threaded
  through for no functional reason. If you're extending contract-version
  creation later and need the payer_id for something new, it currently
  gets discarded as `payer_id=""` in
  `api.repository._rule_input_to_contract_version` — that's the one spot
  to revisit.
- **`Action.READ_CONTRACT` is additive, not a Phase 4 reopening.** Only
  `security/rbac.py`'s permission table and
  `tests/security/test_rbac.py`'s hand-maintained expected-matrix dict
  changed; `can()`'s logic, `Role`, and every other `Action` are
  untouched. The matrix test's own self-check
  (`test_expected_matrix_covers_every_role_action_pair`) is what forces
  this kind of change to update both files together — it would have
  failed loudly if only one had been touched.
- **Two different `assert`-guarding-an-invariant patterns were caught and
  fixed this session, continuing the pattern from Phase 5's bandit
  finding**: `db.repository.get_finding_detail` and (implicitly, by
  writing it correctly from the start this time)
  `api.repository`'s `_finding_to_summary` avoid bare `assert
  isinstance(...)`/`assert x is not None` in favor of explicit `if ... :
  raise` or proper type annotations, specifically because `assert` is
  stripped under `python -O`. Bandit (`B101`) is the thing that would
  catch a regression here — `make security` / `bandit -r . -x
  ./tests,./evals` should stay clean.

## Traps for someone resuming cold

- Everything from the Phase 3/4/5 checkpoints still applies (no
  project-local virtualenv, CRLF warnings on `git add`, generated/ruff-
  excluded `evals/golden/cases.py`).
- **`tests/api/conftest.py`'s `repo` fixture forces specific finding ids**
  via `dataclasses.replace()` (not by mutating — these are frozen
  dataclasses) so tests can request "tenant A's exact finding" or
  "tenant B's exact finding" deliberately, rather than only being able to
  assert on counts. If you add more seeded fixtures, prefer this pattern
  (build via the normal constructor, then `replace()` the id) over trying
  to pre-supply ids through constructor kwargs everywhere.
- **`FakeRepository` and `PostgresRepository` must be kept in lockstep**
  with the `Repository` Protocol — there's no automated check that a new
  Protocol method got implemented on both adapters beyond mypy structural
  typing catching a missing method at the call site where `Repository` is
  used. If you add a Protocol method, add it to both adapters in the same
  change, or `FakeRepository`-backed tests will pass while
  `PostgresRepository` silently doesn't implement the real thing.
- **`tests/api/test_endpoints_live_db.py` generates a unique `subject`
  string per `_seed_tenant()` call** (`live-user-{label}-{uuid4 suffix}`)
  because `users.subject` is unique-constrained — a fixed label would
  collide on a second run against a persistent test database. Don't
  simplify this back to a fixed string.

## Next 3 steps

1. **If Postgres becomes available:** run `docs/DB_SETUP.md`'s steps,
   then `TEST_DATABASE_URL=... pytest tests/db/ tests/ingestion/
   tests/api/ -v`. If Phase 3's four gate tests, Phase 5's three, and
   Phase 6's two (`test_findings_list_returns_only_own_tenant_row`,
   `test_finding_detail_cross_tenant_lookup_is_404_against_real_rls`) all
   pass, check off all three phases in `docs/PHASES.md` — don't check off
   any of them for a lesser reason.
2. **Otherwise, start Phase 7 (recovery packet generation)** per
   `docs/MASTER-BUILD-PROMPT.md` — the only phase where an LLM appears,
   with a hard boundary: it drafts prose only, never computes or restates
   a dollar amount. All figures get injected from the deterministic
   finding record via template substitution, then a validator extracts
   every currency figure from the output and rejects the draft if any
   doesn't exactly match the finding record. Minimum-necessary PHI in
   prompts (no names/member IDs — placeholders, re-inserted after
   generation). Timely-filing deadline tracking. Human approval required
   before anything is "ready to send." Enter plan mode first, same as
   every prior phase.
3. Either way, keep this checkpoint current — don't let a future session
   inherit a stale picture of what's verified vs. just written.
