# Audit — Wave 1, Agent 9: Deployment & Portability

Read-only static review. **This environment has no Docker CLI and no cloud
account** (re-confirmed in `docs/audit/00-baseline.md`: `docker: command not
found`). Nothing here was built, `docker compose`-d, or `terraform
validate`/`plan`/`apply`-d. Every verdict below comes from reading the files,
not from a build/deploy result. Where the code's own honesty markers already
name a gap, that is called out rather than presented as a fresh discovery.

Scope: `Dockerfile`, `docker-compose.yml`, `.dockerignore`,
`terraform/modules/{aws,azure}`, `terraform/environments/{aws,azure}`,
`src/api/routes/health.py`, `src/main.py`, `src/security/kms*.py`,
`alembic/versions/0001-0005`, `docs/RUNBOOK.md`, `docs/SECURITY.md`,
`docs/compliance/`.

---

## Summary of categories

| # | Category | Verdict |
|---|---|---|
| 1 | Dockerfile quality | Multi-stage + non-root + no-secrets: CLEAN. Digest pin: gap. Cache ordering: minor. |
| 2 | Health / readiness / liveness | Endpoints correct. Wiring drifts between clouds + missing in compose. |
| 3 | Cloud-specific lock-in (src/) | No cloud SDK imports: CLEAN. Real cloud KMS adapter: totally missing. |
| 4 | Terraform interface parity | Shared vars + all 5 outputs match: CLEAN. Two real per-cloud drifts. |
| 5 | BAA-eligibility | Asserted, never verified against providers' published lists. |
| 6 | Migration strategy | Additive-only discipline HOLDS across 0001-0005: CLEAN. |
| 7 | Backup / restore | Documented procedure only; never executed or timed. |
| 8 | Twelve-factor | Mostly clean; one real statelessness gap, minor port/observability nits. |

---

## HIGH

### [HIGH] AWS module provisions an ALB with no listener — deployed AWS app is unreachable and has no TLS at ingress
- **File:** terraform/modules/aws/container_runtime.tf:193-231 (ALB + target group, no listener); terraform/environments/aws/main.tf:45-52 (root config wires none either)
- **What breaks:** The AWS module creates `aws_lb.main` and `aws_lb_target_group.app` and registers the ECS service against the target group, but **no `aws_lb_listener` resource exists anywhere** — the module comment (line 228-231) defers "the listener + certificate" to the environment root, and `environments/aws/main.tf` does not add one. Result on a real `terraform apply`: an internet-facing ALB that listens on nothing, so the app is 100% unreachable, and there is zero TLS termination for PHI-bearing traffic. Azure, by contrast, works out of the box (`azurerm_container_app.app` ingress `external_enabled = true` with platform-provided HTTPS on `*.azurecontainerapps.io`). The two "equivalent" clouds are not equivalent: one deploys a working, TLS-terminated endpoint, the other deploys a dead one. This also means the transmission-encryption control `docs/SECURITY.md` claims is "Enforced at the ingress/load-balancer layer" is not actually implemented on AWS.
- **Reproduce:** `grep -rn "aws_lb_listener" terraform/` → zero matches. Then read container_runtime.tf:228-231 and environments/aws/main.tf — neither creates a listener or ACM certificate.
- **Fix:** Add an `aws_lb_listener` on 443 with an ACM certificate (and a redirect listener on 80) in the environment root, plus a `certificate_arn`/`domain_name` input variable. This requires a real domain, so it cannot be fully closed until one exists — but until it is, the AWS path is non-functional and should be labeled as such, not presented as deploy-ready parity with Azure.
- **Effort:** M

### [HIGH] No real cloud KMS adapter — application PHI envelope-encryption uses a static in-secret KEK, not the provisioned cloud KMS key
- **File:** src/security/kms_env.py:32-58 (`EnvKMS`, static KEK); src/main.py:96-100 (wired as the only production KMS); terraform/modules/aws/secrets_and_kms.tf:7-15 and terraform/modules/azure/secrets_and_kms.tf:46-62 (cloud KMS keys provisioned but unused by the app)
- **What breaks:** The gap is **total** at the application layer: there is no `AwsKmsAdapter`/`AzureKeyVaultAdapter` implementing `security.kms.KeyManagementService` anywhere in `src/` (only the `LocalKMS` test adapter and the `EnvKMS` stopgap exist). Production (`main.py`) wraps every DEK with `EnvKMS`, whose KEK is a static 32-byte value read from `PHI_ENCRYPTION_KEY`. Meanwhile Terraform provisions `aws_kms_key.main` (rotation enabled) / `azurerm_key_vault_key.main` (30-day rotation policy) and grants the ECS task role `kms:Decrypt`/`kms:GenerateDataKey` — strongly implying the app uses cloud KMS. It does not. Consequences: (a) the KEK that actually protects patient names/member IDs **never rotates automatically** despite the RUNBOOK's key-rotation section implying it does; (b) there is **no per-operation KMS-side audit trail** for DEK wrap/unwrap, a HIPAA key-management control (§164.308(a)(5)(ii)(D)); (c) the provisioned cloud KMS key rotates cloud-side but nothing in the running app calls it, so that rotation protects only RDS/S3 storage-layer at-rest encryption, not the app's column-level envelope encryption. The gap is *partial* only in that sense: cloud KMS does back the storage layer, but is entirely absent from the app's own PHI encryption. AES-256-GCM at rest itself works (so the encryption requirement is met); this is a key-management-maturity gap, and it is honestly and repeatedly documented (docs/SECURITY.md, kms_env.py docstring, main.py comment).
- **Reproduce:** `ls src/security/` → no `kms_aws.py`/`kms_azure.py`. `grep -rn "current_kek_id\|wrap_key" src/` → only `kms.py` (port), `kms_local.py`, `kms_env.py`. Confirm main.py:100 constructs `EnvKMS`, never a cloud adapter.
- **Fix:** Implement `AwsKmsAdapter` (using `GenerateDataKey`/`Decrypt` against `kms_key_id`) and `AzureKeyVaultAdapter` (wrapKey/unwrapKey against the Key Vault key) behind the existing port; select by env/config in `main.py`. Requires a real cloud account to test, which is why it's deferred — but it is the single largest cloud-portability gap and the reason the provisioned KMS keys are currently dead weight for app encryption.
- **Effort:** L

### [HIGH] Backup/restore procedure is documented but has never been executed or timed — Phase 9 gate "restore rehearsed and timed" is unmet
- **File:** docs/RUNBOOK.md:128-151 ("Restore (backup verification)")
- **What breaks:** RDS and Azure Flexible Server both take automated backups (Terraform-managed `db_backup_retention_days`, default 14). The restore procedure is written out in five steps, but the section explicitly states it is "to be executed and timed against a real provisioned database — not done here, no such database exists in this environment," and step 4 says the wall-clock restore time "can only be produced by doing this for real." No timed restore has ever occurred (compliance tracker item #10, `docs/compliance/README.md:28`, reads "DRAFTED — not yet tested"). An untested backup is not a backup: a restore that fails or takes far longer than assumed is discovered only at disaster time, when real PHI and recovery dollars are at stake. This is exactly what the Phase 9 gate ("restore rehearsed and timed") requires and it is entirely unmet.
- **Reproduce:** Read docs/RUNBOOK.md:132-135 and 146-150; cross-check docs/compliance/README.md:28.
- **Fix:** Once a database is provisioned, execute the documented restore into a throwaway instance, verify row counts / `alembic_version` / RLS policy presence, record the start-to-finish wall-clock time, and paste the real number into the RUNBOOK. No code change; an operational drill.
- **Effort:** M

---

## MEDIUM

### [MEDIUM] Health-probe wiring drifts between clouds and is absent from docker-compose
- **File:** terraform/modules/aws/container_runtime.tf:217-223 (ALB target-group health check = `/healthz`); docker-compose.yml:31-47 (app service has no healthcheck); contrast terraform/modules/azure/container_runtime.tf:97-107 (both `/healthz` liveness and `/readyz` readiness)
- **What breaks:** The endpoints themselves are correct and correctly separated (src/api/routes/health.py: `/healthz` never touches the DB; `/readyz` calls `Repository.ping()` and returns 503 on any failure — verified degrading honestly in 00-baseline.md). But the *wiring* is inconsistent: Azure Container Apps uses `/healthz` for liveness AND `/readyz` for readiness (correct). The AWS ALB target group health-checks only `/healthz`, which is the sole rotation signal in ECS — so an instance that is up but cannot reach the database keeps passing `/healthz`, stays in rotation, and serves 503s from every real endpoint instead of being pulled out. And `docker-compose.yml`'s `app` service defines no healthcheck at all, so nothing downstream can gate on it and a broken app in local/Codespaces dev looks "up." (Note the tradeoff: switching the ALB to `/readyz` would pull *all* instances during a DB outage — the right answer is usually a shallow readiness gate plus separate liveness, as Azure already has.)
- **Reproduce:** Compare the three files above. AWS target group `path = "/healthz"`; Azure has both probes; compose `app:` block has no `healthcheck:` key.
- **Fix:** Give the AWS ALB target group a readiness-aware check (or add an ECS deployment health gate on `/readyz`); add a `healthcheck` to the compose `app` service hitting `/healthz` with sensible interval/retries.
- **Effort:** S

### [MEDIUM] Base image pinned by tag, not digest — build reproducibility drifts over time
- **File:** Dockerfile:17 and Dockerfile:48 (both `FROM python:3.12-slim`)
- **What breaks:** Both stages pin `python:3.12-slim` by mutable tag. The same `docker build` a month apart can pull a different underlying image as the tag is re-published, so "byte-for-byte reproducible" (which the lockfile achieves for Python deps) does not hold for the base OS layer — a supply-chain and reproducibility gap. The Dockerfile comment (lines 9-16) is honest that a real digest couldn't be obtained without a registry in this environment and warns to replace both `FROM` lines before shipping.
- **Reproduce:** Read Dockerfile:9-17,48. No `@sha256:...` on either FROM.
- **Fix:** In a Docker-capable environment (Codespace), `docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim` and pin both `FROM` lines to `python:3.12-slim@sha256:<digest>`; re-pin deliberately on each base update.
- **Effort:** S

### [MEDIUM] Azure DATABASE_URL omits `sslmode=require` that AWS includes — in-transit encryption drift between "equivalent" modules
- **File:** terraform/modules/azure/secrets_and_kms.tf:75 (no `?sslmode=require`); contrast terraform/modules/aws/secrets_and_kms.tf:64-70 (`...?sslmode=require`, paired with `rds.force_ssl=1`)
- **What breaks:** The AWS module deliberately appends `sslmode=require` to the connection string AND sets `rds.force_ssl=1` server-side, with a comment (secrets_and_kms.tf:66-68) that this ensures "the app never even offers" a plaintext connection. The Azure connection string has neither `sslmode=require` nor an explicit `require_secure_transport` server parameter (azure/database.tf sets none). psycopg's default `sslmode` is `prefer` — opportunistic TLS with no fallback guarantee. Azure Flexible Server does default `require_secure_transport=ON`, so a connection would still be encrypted today, but the belt-and-suspenders guarantee the AWS side went out of its way to add is silently missing on Azure. If that server default were ever changed, PHI-bearing queries would traverse the VNet in plaintext with no client-side objection.
- **Reproduce:** Diff the two `app_database_url` value strings; note AWS has `?sslmode=require`, Azure does not; grep azure module for `require_secure_transport` → absent.
- **Fix:** Append `?sslmode=require` to the Azure `azurerm_key_vault_secret.app_database_url` value, and/or set the `require_secure_transport` server configuration explicitly, matching the AWS posture.
- **Effort:** S

### [MEDIUM] 6-year retention lifecycle exists on AWS S3 but not on Azure Blob — HIPAA retention drift between modules
- **File:** terraform/modules/azure/storage.tf:18-24 (only a 30-day `delete_retention_policy`); contrast terraform/modules/aws/storage.tf:46-69 (`retain-six-years` lifecycle rule + noncurrent-version transition)
- **What breaks:** The AWS S3 bucket implements an explicit 6-year-oriented retention posture (versioning + a `retain-six-years` lifecycle rule transitioning noncurrent versions to Glacier), tied in-comment to the HIPAA 6-year documentation requirement. The Azure Blob account has versioning and a **30-day** `delete_retention_policy` and nothing else — no 6-year retention/lifecycle equivalent. Deploying the same system to Azure gives a materially weaker retention guarantee for inbound 835 files than deploying to AWS, breaking the "genuinely equivalent interface" claim on a compliance-relevant axis.
- **Reproduce:** Compare the two storage files; AWS has `aws_s3_bucket_lifecycle_configuration.remittances`, Azure has only `blob_properties { delete_retention_policy { days = 30 } }`.
- **Fix:** Add an Azure Storage lifecycle management policy (`azurerm_storage_management_policy`) with tier/retention rules mirroring the S3 6-year posture, and lengthen or supplement the 30-day delete-retention window.
- **Effort:** M

### [MEDIUM] BAA-eligibility of managed services is asserted in comments, never verified against the providers' published lists
- **File:** terraform/README.md:57-73 ("Every resource type below is deliberately chosen from that provider's published HIPAA-eligible-services list... AWS: 166+... Azure: 80+; both cover everything used here"); docs/compliance/README.md:19 (item #1 still "NEEDS EXTERNAL ACTION")
- **What breaks:** Every module `.tf` file tags its services "HIPAA-eligible" in comments (RDS, S3, Secrets Manager, KMS, ECS/Fargate, CloudWatch Logs; Azure PG Flexible Server, Blob, Key Vault, Container Apps, Log Analytics). This is an **assertion**, not a confirmation: by the project's own tracker (`compliance/README.md` item #1), the confirmation that every service used is on the provider's current HIPAA-eligible list is an open pre-production external action ("accept AWS's BAA via AWS Artifact... and confirm every AWS service actually used is on AWS's list"). The individually well-known services (RDS, S3, KMS, PG Flexible Server, Blob, Key Vault, ECS) are safe bets, but **Azure Container Apps** is a newer service (GA 2022) and is the one most warranting an explicit check against Azure's current in-scope-services documentation rather than an assumption — the app's entire compute tier rides on it.
- **Reproduce:** Read terraform/README.md:57-64 (assertion) against docs/compliance/README.md:19 (still open); note no cross-reference to a dated provider list anywhere.
- **Fix:** Before real PHI: accept both providers' BAAs, and record — with the retrieval date — each service checked against the provider's published HIPAA-eligible/in-scope list, Azure Container Apps explicitly among them. Documentation/process, no code change.
- **Effort:** S

### [MEDIUM] In-memory rate-limit / lockout state violates stateless-process discipline once replicas > 1
- **File:** src/security/rate_limit.py (`InMemoryTokenBucketRateLimiter`, `AccountLockoutTracker`); terraform/modules/aws/variables.tf:57-61 (`desired_count` default 2); terraform/modules/azure/variables.tf:57-60 (`min_replicas` default 2)
- **What breaks:** Both the token-bucket rate limiter and the account-lockout tracker hold their counters in process memory. Both cloud modules default to 2+ replicas. Twelve-factor requires processes to be stateless and share nothing; here, per-request throttling and failed-login lockout state live in each instance's RAM, so with 2+ replicas a client's effective rate limit is multiplied by the replica count and lockout can be evaded by landing on a different instance. Practical impact today is low because — per 00-inventory.md — neither is wired into any route (`api/rate_limit.py` is a zero-importer orphan). But this is a latent portability/scaling defect the moment they are wired, and `docs/SECURITY.md:32` already flags the "single-process only; a Redis-backed adapter is needed" limitation.
- **Reproduce:** Read the two classes (in-memory dicts), then the replica-count defaults. Confirm no shared store (Redis/Postgres) backs either.
- **Fix:** When wiring rate limiting/lockout, add a shared-store adapter (Redis or a Postgres table) behind the existing interfaces; keep the in-memory one for single-process/dev only.
- **Effort:** M

---

## LOW

### [LOW] Dockerfile copies src/ before the dependency install, busting the dep-layer cache on every code change
- **File:** Dockerfile:27-46
- **What breaks:** The builder `COPY`s `requirements.lock.txt`, `pyproject.toml`, AND `src/` (lines 27-29) all before the `RUN pip install ... -r requirements.lock.txt` (line 45). Because `src/` is in the same pre-RUN group, any application source change invalidates the cache for the entire RUN — including the slow `pip install -r requirements.lock.txt` step — so dependencies re-resolve on every code edit. This directly contradicts the file's own comment (lines 21-26) claiming the copy order exists "so Docker's layer cache isn't invalidated by every source-code change." Correctness is fine; build times are needlessly long.
- **Reproduce:** Read Dockerfile:27-46; note `COPY src/ ./src/` precedes the dependency-install RUN.
- **Fix:** Split into two RUNs: `COPY requirements.lock.txt pyproject.toml ./` → `RUN pip install ... -r requirements.lock.txt`, then `COPY src/ ./src/` → `RUN pip install --no-deps .`. Deps then re-install only when the lockfile changes.
- **Effort:** S

### [LOW] RUNBOOK "Zero-downtime migrations" section is stale — says "0002 through 0004", omits 0005
- **File:** docs/RUNBOOK.md:273-277
- **What breaks:** The section states "Every migration in this repo so far (`0002` through `0004`) has already followed the same pattern... additive only." Migration `0005_finding_outcomes` (Phase 12) is not mentioned. The discipline itself still holds (0005 adds four nullable columns — verified additive), so this is documentation staleness, not a broken invariant, but a reader auditing the additive-only claim against the actual `alembic/versions/` list will find a fifth migration the doc doesn't acknowledge.
- **Reproduce:** Read docs/RUNBOOK.md:273; `ls alembic/versions/` shows 0005.
- **Fix:** Update the range to "0002 through 0005."
- **Effort:** S

### [LOW] Minor twelve-factor nits: hardcoded port and silent observability fallback
- **File:** Dockerfile:75 (`--port 8000` hardcoded); src/main.py:87 (`OTEL_EXPORTER_OTLP_ENDPOINT` optional → console exporter)
- **What breaks:** (a) The listen port is hardcoded `8000` in the ENTRYPOINT rather than read from a `$PORT` env var; harmless given EXPOSE/infra all agree on 8000, but strictly a port-binding-via-config deviation. (b) If `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, `main.py` silently falls back to `ConsoleSpanExporter`/`ConsoleMetricExporter` (span/metric data to stdout) — fine as "logs as event streams," but in production an unset endpoint means observability data quietly goes nowhere useful instead of failing fast the way the required secrets do. Neither is a defect; both are polish.
- **Reproduce:** Read Dockerfile:75 and src/main.py:64-92.
- **Fix:** Optionally read `$PORT` (default 8000); consider warning at startup when no OTLP endpoint is configured in a production environment.
- **Effort:** S

---

## Categories that are genuinely CLEAN (stated explicitly, with evidence)

### Dockerfile — multi-stage, non-root, and no secrets in any layer: CLEAN
- **Multi-stage:** `builder` (compiler/pip) → `final` (venv + source only), Dockerfile:17,48. Final image carries no build toolchain, no pip cache, no dev/test deps.
- **Non-root:** dedicated `appuser` uid/gid 10001, `USER appuser` before ENTRYPOINT (Dockerfile:54-67). Fixed uid chosen deliberately for stable K8s/security-context references.
- **No secrets in layers:** every `COPY`/`RUN`/`ENV` line was inspected — there is **no `ARG` or `ENV` carrying a credential**, and no `COPY` of a secret, key, or `.env` (`.dockerignore` excludes `.env`, `.env.*`, `.git/`, tests, docs). No deleted-in-a-later-layer secret exists to recover from history. The only `ENV`s are `PATH`, `PYTHONPATH`, `PYTHONUNBUFFERED` (Dockerfile:63-65). CLEAN.
- Image size is reasonable (slim base + venv, no apt installs to clean up); no obvious large reduction available. The setuptools/msgpack force-reinstall (Dockerfile:43-44) is a documented Trivy-CVE remediation, not bloat.

### Cloud-specific SDK lock-in in src/ — CLEAN
- `grep` for `boto3|botocore|azure|google.cloud` imports across `src/` → **zero matches**. All cloud touch-points are behind ports: `security.kms.KeyManagementService`, `security.secrets.SecretStore`, and `ingestion.sources` (`S3Client`/`SFTPClient` are structural `Protocol`s a real boto3/paramiko client satisfies without this project depending on either SDK). `main.py` reads only already-materialized env vars; its docstring is explicit that no cloud SDK code lives in the app. The lock-in surface is genuinely clean — the only gap is the *missing* cloud KMS adapter (HIGH above), not any leaked cloud dependency.

### Terraform interface parity (shared inputs + all outputs) — CLEAN
- Both modules accept the five contract inputs (`project_name`, `environment`, `region`, `container_image`, `db_backup_retention_days`) documented in terraform/README.md, and both expose **all five** contract outputs with matching names and semantics: `database_endpoint`, `database_secret_id`, `object_storage_bucket_name`, `kms_key_id`, `container_service_endpoint` (aws/outputs.tf vs azure/outputs.tf, 1:1). Conceptual resource mapping is equivalent (VPC↔VNet, RDS↔Flexible Server, S3↔Blob, Secrets Manager↔Key Vault, KMS↔Key Vault Keys, ECS Fargate↔Container Apps). Switching clouds is switching which module the environment root points at — the intended design. The drifts I found (sslmode, 6-year retention, ALB listener, health probes) are *within* otherwise-parallel resources, filed as MEDIUM/HIGH above; the module interface contract itself holds.

### Migration additive-only discipline (0001-0005) — CLEAN
- 0001: initial `create_all` + RLS/grants. 0002: add nullable `remittances.quarantine_reason`. 0003: add `users` table + nullable `audit_log.request_id`. 0004: add `recovery_packets` table + `contracts.timely_filing_days` (NOT NULL but with `server_default="90"`, so existing rows and old-app inserts are safe) + nullable `contracts.packet_template`. 0005: add four nullable `findings` columns. **No `upgrade()` path contains a rename or a drop** — every change is add-column or add-table, exactly the expand-only pattern `docs/RUNBOOK.md`'s "Zero-downtime migrations" describes; a rolling deploy with old and new app versions coexisting is safe against all five. (Drops appear only in `downgrade()` paths, which are not the forward rolling-deploy path.) The additive-only claim is genuinely met; the only related nit is the stale doc range (LOW above).

---

## Finding count

- **CRITICAL:** 0
- **HIGH:** 3 (AWS ALB has no listener / no ingress TLS; no real cloud KMS adapter; backup restore never rehearsed/timed)
- **MEDIUM:** 6 (health-probe wiring drift; base image not digest-pinned; Azure sslmode drift; 6-year retention drift; BAA-eligibility asserted-not-verified; in-memory rate-limit/lockout statelessness)
- **LOW:** 3 (Docker cache ordering; stale RUNBOOK migration range; port/observability twelve-factor nits)
- **Total:** 12
