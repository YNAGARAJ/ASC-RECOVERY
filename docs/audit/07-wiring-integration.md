# Audit — Wave 1, Agent 7: Wiring & Integration

Read-only pass. No application code was modified. Every inventory-derived claim
below was re-verified against the actual source (not copied from
`00-inventory.md`), and the analysis goes beyond "is this referenced" to "does
the wired-together path actually work end to end."

Scope reminder (categories from the prompt), each addressed explicitly at the
end even when clean:
config-read-but-never-set · config-set-but-never-read · migrations-not-applied ·
unreachable-routes · DI-wired-but-unused · import-cycles · earlier-phase-work-
bypassed · Makefile/CI/Terraform cross-references.

---

## HIGH

### [HIGH] `PHI_ENCRYPTION_KEY` is required at startup but injected by neither cloud
- **File:** `src/main.py:86` (and `src/security/kms_env.py:33`) vs `terraform/modules/aws/container_runtime.tf:131-144` and `terraform/modules/azure/container_runtime.tf:84-95`
- **What breaks:** `create_app_from_env()` calls `_require(secrets, "PHI_ENCRYPTION_KEY")` and then constructs `EnvKMS(secrets)`, which reads the same variable (default `secret_name="PHI_ENCRYPTION_KEY"`). `docker-compose.yml:44` sets it, so local works — but the AWS ECS task definition's `secrets` block injects only `DATABASE_URL`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, and the Azure Container App's `env` block injects the same three. Neither cloud even defines a secret resource for `PHI_ENCRYPTION_KEY` (`secrets_and_kms.tf` in both modules creates only JWT/Anthropic/DB-URL secrets). The container therefore raises `MissingConfigurationError: required environment variable PHI_ENCRYPTION_KEY is not set` and exits before serving a single request, in *both* production clouds.
- **Reproduce:** `terraform apply` either environment, push an image, let the ECS/Container-App task start → the process crashes in `create_app_from_env` at import/startup; the container restarts in a crash loop and never binds :8000.
- **Fix:** Add `PHI_ENCRYPTION_KEY` to `aws_secretsmanager_secret.app` (and its ECS `secrets` entry) and to a `azurerm_key_vault_secret` + Container-App `secret`/`env` pair, populated out of band like the JWT/Anthropic keys. Keep it out of Terraform's plaintext (same `REPLACE_ME_OUT_OF_BAND` / `ignore_changes` pattern already used for the others).
- **Effort:** S

### [HIGH] No automated `alembic upgrade head` (and no `asc_app` role bootstrap) in the deploy pipeline
- **File:** `.github/workflows/deploy.yml` (entire file — no migration step); `Dockerfile:75` (entrypoint is `uvicorn` only); `scripts/db/init_roles.sql:14` vs `terraform/modules/aws/secrets_and_kms.tf:49-69`
- **What breaks:** `ci.yml:97` is the *only* place `alembic upgrade head` runs. The deploy workflow builds the image, pushes it, and runs `terraform apply` (which provisions a brand-new RDS / Azure Flexible Server), then starts the container — whose entrypoint is `uvicorn main:create_app_from_env` with no migration. Nothing applies the schema to the freshly provisioned production database, so every real endpoint 500s (`relation "..." does not exist`). Worse, the smoke test (`deploy.yml:78-89`) only curls `/healthz` (static `{"status":"ok"}`) and `/readyz` (which runs `SELECT 1` — succeeds against an empty schema), so the pipeline reports **green** while the app cannot serve a single business request. Compounding it: the runtime `DATABASE_URL` uses role `asc_app` with `random_password.app_db` (32 random chars), but that role is only ever created by `init_roles.sql`, which hardcodes password `asc_app_dev_password`; no automated step creates `asc_app` with the random password, so even the connection string would fail auth.
- **Reproduce:** Trigger `Deploy` against a fresh workspace with real cloud creds → staging goes healthy/ready/green → `curl -H "Authorization: Bearer …" $url/findings` → 500 from a missing table; DB login as `asc_app` fails.
- **Fix:** Add an explicit migration + role-bootstrap job to `deploy.yml` before the service is pointed at the new DB (run `alembic upgrade head` as the master/owner role, and create `asc_app` with `random_password.app_db.result` piped from Terraform output), OR add a one-shot ECS/Container-App "migrate" task. Also make the smoke test hit one authenticated read so an unmigrated DB fails the gate.
- **Effort:** M

### [HIGH] Rate limiting and account-lockout are fully built and tested but wired to zero routes
- **File:** `src/api/rate_limit.py:16` (`enforce_rate_limit`), `src/security/rate_limit.py` (`AccountLockoutTracker`), vs `src/api/app.py:24-33` and every handler in `src/api/routes/*.py`
- **What breaks:** `enforce_rate_limit` is a complete FastAPI dependency (429 on bucket exhaustion) and `AccountLockoutTracker` is a complete failed-attempt tracker, both with passing tests — but `src/api/rate_limit.py` is imported by **nothing** (verified: only its own file and docs reference the name; no test even imports it), and no route uses `Depends(enforce_rate_limit)`. `require_permission` (`api/auth.py:82`) does not call it either. Production therefore has no request throttling and no lockout on any endpoint, including the PHI-bearing `/findings` and packet routes. A client can brute-force or hammer the API without limit.
- **Reproduce:** `grep -rn enforce_rate_limit src/api/routes/ src/api/app.py` → no hits; hit any endpoint in a tight loop → no 429 ever returned.
- **Fix:** Apply `enforce_rate_limit` as a router-level or app-level dependency (and swap the in-memory limiter for a shared store before running >1 replica — `desired_count`/`min_replicas` default to 2, so the in-memory bucket is already per-instance and undercounts). Wire `AccountLockoutTracker` into the (still-missing) auth path.
- **Effort:** M

### [HIGH] MFA implemented and tested but unreachable — no enrollment/verification route, no enforcement
- **File:** `src/security/mfa.py` vs `src/api/routes/` (no MFA route anywhere); `src/security/session.py::issue_session` `mfa_verified` parameter
- **What breaks:** `generate_enrollment_secret` / `provisioning_uri` / `verify_code` exist and are tested (`tests/security/test_mfa.py` is their only importer — verified), but there is no API route to enroll or verify a TOTP code, and nothing sets `issue_session(..., mfa_verified=True)` off a real verification. MFA cannot be enforced against any real user today. (This overlaps Agent 5's PHI/security scope; recorded here because it is a concrete wiring gap: a built component with no call site.)
- **Reproduce:** `grep -rn "verify_code\|provisioning_uri" src/api` → no hits.
- **Fix:** Add `POST /auth/mfa/enroll` and `POST /auth/mfa/verify` routes, and gate `mfa_verified` in session issuance on a real `verify_code` result. (Depends on there being a login/credential path at all — none exists yet, per Phase 6 scope note.)
- **Effort:** L

---

## MEDIUM

### [MEDIUM] Worklist ranking built in Phase 7 is bypassed — API returns findings in `created_at DESC`, not priority order
- **File:** `src/packets/worklist.py` (`rank_worklist`) vs `src/api/routes/findings.py:55-63` / `:66-108` and `src/db/repository.py:703`
- **What breaks:** `rank_worklist` (deadline-then-dollar-value ordering) is implemented and tested but imported only by its own test (verified). Both `GET /findings` and `GET /findings/export.csv` call `repository.list_findings`, which orders strictly by `FindingModel.created_at.desc()` (`db/repository.py:703`) and applies no deadline/dollar prioritization. The "worklist" the product's own design calls a differentiator is unreachable through the API; a user exporting `worklist.csv` gets newest-first, not most-urgent-first, so time-sensitive high-dollar appeals can sort below trivial recent ones and blow their filing deadline.
- **Reproduce:** Seed findings with mixed deadlines/shortfalls, `GET /findings` → rows come back newest-first regardless of `rank_worklist`'s intended order.
- **Fix:** Have `list_findings`/`export_findings_csv` route results through `rank_worklist` (or push an equivalent `ORDER BY deadline, shortfall DESC` into the query), and add a test asserting the order.
- **Effort:** M

### [MEDIUM] `request_id` is captured but never threaded into any audit write; the api-level `write_audit_log` wrapper is dead
- **File:** `src/api/auth.py:76-79` and `src/api/request_context.py:5` (docstring: "threaded into every audit_log entry") vs `src/api/repository.py:695,706,729,807` and `src/api/repository.py:620` (`PostgresRepository.write_audit_log`)
- **What breaks:** `RequestIDMiddleware` stashes `request.state.request_id` and `get_auth_context` copies it into `AuthContext.request_id`, but no route handler ever reads `ctx.request_id` (verified: zero `request_id` references under `src/api/routes/`), and none of the repository write methods accept it. Every real audit write in the API path (`packet_generated`, `packet_approved`, `packet_rejected`, `packet_draft_rejected`, `packet_generation_failed`) calls `db_repository.write_audit_log(...)` **without** `request_id`, which defaults to `None`. The ingestion path (`from db import repository`) does the same. Result: `audit_log.request_id` is always NULL for every write in the entire app, and `AuthContext.request_id` is dead. The dedicated api-level `Repository.write_audit_log` wrapper (ABC `api/repository.py:323`, impl `:620`, the only variant that accepts `request_id` from a caller) is invoked by nobody in `src/` (ingestion calls the *db-module* function of the same name, not this method; verified). The request_context docstring's claim is false, weakening forensic traceability of PHI-affecting writes.
- **Reproduce:** Perform any write over the API, inspect the row: `SELECT request_id FROM audit_log` → all NULL. `grep -rn "\.write_audit_log" src/api/routes` → no hits.
- **Fix:** Thread `ctx.request_id` from each route into the repository write methods (add the param to `generate_packet`/`decide_packet`/`record_finding_outcome` and forward it to `db_repository.write_audit_log`), then either use or delete the unused api-level wrapper. `source_ip` is likewise never populated on any write — same fix location.
- **Effort:** M

### [MEDIUM] `docker compose up` leaves the schema unmigrated — first real request 500s
- **File:** `docker-compose.yml:19-24` (comment acknowledges it) and absence of any migration step in compose
- **What breaks:** The compose stack starts Postgres + app but never runs `alembic upgrade head` (the app entrypoint is uvicorn only). The behavior is documented as intentional, but it is a live trap: a developer/reviewer who runs `docker compose up` and curls a real endpoint gets a 500 (missing tables) with no hint that a manual migration step was skipped. Wave 4's own gate ("POST a synthetic 835 through the real API → show findings") fails from a clean `docker compose up` today.
- **Reproduce:** `docker compose up`, then `POST /remittances` → 500, relation does not exist.
- **Fix:** Add a `migrate` one-shot service (runs `alembic upgrade head` as `asc_owner`, `depends_on: postgres healthy`) that the `app` service `depends_on: service_completed_successfully`, or document the manual step directly in the compose `app` comment as a required first command.
- **Effort:** S

### [MEDIUM] SFTP/S3/Upload ingestion sources are built and tested but never constructed by any production path
- **File:** `src/ingestion/sources.py` (`IngestionSource`, `UploadSource`, `SFTPClient`, `SFTPPollSource`, `S3Client`, `S3PollSource`) vs `src/api/routes/remittances.py` and `src/ingestion/pipeline.py:61`
- **What breaks:** Verified: only `tests/ingestion/test_sources.py` imports this module. The one real ingestion entry point (`POST /remittances`) reads `content: bytes` from an `UploadFile` and passes bytes straight to `ingest_file`; `ingest_file` takes raw bytes and never touches any `IngestionSource`. There is no scheduled poller, job, or route that pulls from SFTP or S3, even though the AWS S3 remittances bucket is provisioned (`storage.tf`) and IAM-granted. Any customer expecting automated payer-drop ingestion (the usual 835 delivery channel) has no code path that does it.
- **Reproduce:** `grep -rn "SFTPPollSource\|S3PollSource\|UploadSource" src/ --include=*.py` → only `sources.py` itself.
- **Fix:** Either wire a scheduled poller that constructs `S3PollSource`/`SFTPPollSource` and feeds `ingest_file`, or record these as explicitly deferred and stop provisioning the S3 bucket/IAM as if they were used.
- **Effort:** M

---

## LOW

### [LOW] `db/repository.py::get_contract_by_payer_id` is dead — the packet-deadline path re-implements the lookup
- **File:** `src/db/repository.py` (`get_contract_by_payer_id`, ~line 225 per inventory) vs `src/api/repository.py:660-667`
- **What breaks:** Its docstring says it exists for Phase 7's `timely_filing_days`/`packet_template` lookup, but `generate_packet` instead resolves the contract via `session.get(ContractVersionORM, ...)` then `session.get(ContractORM, version.contract_id)`. Verified the function is referenced nowhere in `src/` or `tests/` (not even a test) — pure dead code that will silently drift from the path that actually runs.
- **Reproduce:** `grep -rn get_contract_by_payer_id src tests` → only its own definition.
- **Fix:** Delete it, or call it from `generate_packet` in place of the inline joins.
- **Effort:** S

### [LOW] `api/schemas.py::ErrorOut` is unused documentation-as-code
- **File:** `src/api/schemas.py:332`
- **What breaks:** A Pydantic model shaped like the error body, but `api/errors.py::_error_body` builds a plain `dict[str,str]` and never references `ErrorOut`. Verified referenced only in `schemas.py` and docs. It drifts silently from the real error shape and misleads OpenAPI consumers.
- **Reproduce:** `grep -rn ErrorOut src tests` → only its definition.
- **Fix:** Type the error handlers' responses against `ErrorOut` (and register it as the error `response_model`), or delete it.
- **Effort:** S

### [LOW] `OTEL_EXPORTER_OTLP_ENDPOINT` is read but set in no deployment — prod traces/metrics silently go to stdout
- **File:** `src/main.py:87` vs `docker-compose.yml`, both terraform `container_runtime.tf`, and `.github/workflows/*.yml`
- **What breaks:** When the var is absent, `main.py` deliberately falls back to `ConsoleSpanExporter`/`ConsoleMetricExporter`. It is set nowhere — not in compose, not in either cloud task/container definition. So in production all spans and metrics are written to container stdout and never reach a collector; there is no wiring to any OTLP backend despite the OTel dependency stack being installed.
- **Reproduce:** `grep -rn OTEL_EXPORTER_OTLP_ENDPOINT` → only the read site in `main.py`.
- **Fix:** Inject `OTEL_EXPORTER_OTLP_ENDPOINT` (env, not secret) in each deployment pointing at the collector, or record console-only export as an accepted, documented gap.
- **Effort:** S

### [LOW] CI re-implements Makefile targets inline; production deploy jobs lack their own credential guard
- **File:** `.github/workflows/ci.yml:99-104,116-127` vs `Makefile:3-19`; `.github/workflows/deploy.yml:105-129,198-221`
- **What breaks:** (a) CI's `test`/`security` steps duplicate the exact command sequences in `make test`/`make security` rather than invoking the targets, so the two can drift (a change to a Makefile target won't be reflected in CI). Today they match; the risk is maintainability. (b) `deploy-production-aws`/`deploy-production-azure` have no `if:` credential guard of their own and rely purely on `needs:` skip-propagation from the guarded staging job — functionally correct today, but a reordering or an added `always()` would let a prod `terraform apply` run without the intended gate.
- **Reproduce:** Inspect both files.
- **Fix:** Have CI call `make test`/`make security`/`make eval`; add an explicit `if: secrets.AWS_DEPLOY_ROLE_ARN != ''` (resp. Azure) guard to the production deploy jobs as defense-in-depth.
- **Effort:** S

---

## Categories verified CLEAN (with evidence)

### Import cycles — CLEAN (independently verified, confirms `00-inventory.md`)
Ran an AST scan over all 60 `.py` modules under `src/` (parsed every `import`/
`from … import`, resolved each target to a known module or its owning package,
DFS cycle detection): **0 cycles found.** Layering holds: `domain/` imports
nothing else in `src/`; `db`/`security`/`packets`/`ingestion`/`observability`
depend only downward; `api` sits above them; `main` composes. The inventory's
"no import cycles" claim is correct.

### Unreachable routes / routing conflicts — CLEAN
All six routers are included in `api/app.py:27-32`. Every route module defines
exactly one `router` and it is registered. No prefix is dropped. The one
ordering hazard is handled correctly: in `findings.py`, `GET /findings/export.csv`
(`:66`) is declared *before* `GET /findings/{finding_id}` (`:111`), so FastAPI
matches the literal path first — `export.csv` is not shadowed by the `{finding_id}`
converter. `/findings/{finding_id}/packets` (packets router) and
`/findings/{finding_id}` (findings router) are different depths and do not
collide. `/readyz` (used by Azure's readiness probe and the deploy smoke test)
and `/healthz` (ALB / both clouds) both exist and match the Terraform health-check
paths. All handlers are exercised over HTTP by `tests/api/`.

### Terraform variables set-but-never-read — CLEAN
Every variable in both `modules/aws/variables.tf` and `modules/azure/variables.tf`
is referenced by a resource (`container_cpu`/`container_memory`/`desired_count`/
`min_replicas`/`max_replicas`/`tags` in `container_runtime.tf`; `vpc_cidr`/
`vnet_cidr`, `db_*` in `network.tf`/`database.tf`; `region`/`environment`/
`project_name`/`container_image` throughout). No orphaned variable found.

### Terraform variables required-but-unset — CLEAN
The environment roots (`environments/{aws,azure}/main.tf`) pass `project_name`,
`environment`, `region`, `container_image`; every other module variable has a
default, so `terraform apply` will not fail for a missing required variable.
Module outputs referenced by `deploy.yml` (`container_service_endpoint`) exist in
both `outputs.tf` files.

### Config keys read but never set (application env) — mostly CLEAN, two exceptions above
`DATABASE_URL`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY` are each required by
`create_app_from_env` and set in `docker-compose.yml` **and** injected by both
cloud runtimes — fully wired. The two gaps are `PHI_ENCRYPTION_KEY` (required,
injected by neither cloud — HIGH above) and `OTEL_EXPORTER_OTLP_ENDPOINT`
(optional, set nowhere — LOW above).

---

## Method note
Findings marked "verified" were checked with a targeted `grep`/`Grep` across
`src/`, `tests/`, `evals/`, `alembic/`, plus a direct read of the relevant
source — not taken from `00-inventory.md`. The import-cycle result is from a
fresh AST scan, not the inventory's prose. Where a finding overlaps another
agent's lane (MFA → Agent 5; rate limiting → Agent 5) it is noted as such.
