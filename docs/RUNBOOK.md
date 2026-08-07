# Runbook

Operational procedures for deploying, operating, and recovering the ASC
Underpayment Recovery Platform. Written alongside Phase 9's Terraform
modules (`terraform/`) and Dockerfile — see `terraform/README.md` for the
infrastructure layout and `docs/SECURITY.md` for the control matrix
these procedures assume.

**Honesty check before anything else**: the commands below have not been
run against real infrastructure. No Docker, Terraform CLI, or cloud
account exists in the environment this was authored in. Each section
below says explicitly what is and isn't verified.

## Deploy

1. **Build and push the image.**
   ```
   docker build -t <registry>/asc-recovery:<tag> .
   docker push <registry>/asc-recovery:<tag>
   ```
   Not run here — no Docker in this environment. Verify in a real Docker
   environment (e.g. a Codespace) before trusting this step blindly.

2. **Provision infrastructure** (first deploy to a given cloud/environment
   only — subsequent deploys skip straight to step 4):
   ```
   cd terraform/environments/<aws|azure>
   terraform init
   terraform plan -var="container_image=<registry>/asc-recovery:<tag>"
   terraform apply -var="container_image=<registry>/asc-recovery:<tag>"
   ```
   **Get explicit sign-off before running `apply` against a real account**
   — it creates billed resources and is not trivially reversible.
   `terraform validate`/`plan`/`apply` have not been run anywhere; no
   Terraform CLI or cloud account exists in the environment this was
   authored in.

3. **Bootstrap database roles** (first deploy only). Terraform provisions
   the database and its `asc_owner` master credentials (in Secrets
   Manager / Key Vault, per `terraform/README.md`'s outputs) — it does
   not create the `asc_app` runtime role or run migrations. Connected as
   `asc_owner`:
   ```
   psql "$(terraform output -raw database_admin_connection_string)" \
     -f scripts/db/init_roles.sql
   ```
   `scripts/db/init_roles.sql` currently hardcodes a local-dev password
   for `asc_app` — for a real deploy, edit the `CREATE ROLE` statement to
   use the password Terraform generated (`random_password.app_db` in
   either cloud module) before running it, or the `asc_app` role won't
   match the `DATABASE_URL` secret Terraform already created.

4. **Run migrations, as a separate step, never automatically on
   container start** (see "Zero-downtime migrations" below for why):
   ```
   DATABASE_URL="<asc_owner connection string>" alembic upgrade head
   ```

5. **Deploy the app** (ECS service update / Container App revision) to
   pick up the new image. Both Terraform modules' `container_runtime.tf`
   already point at `var.container_image`; re-running `terraform apply`
   with a new tag is sufficient to roll out.

6. **Verify.** `GET /healthz` returns 200 immediately; `GET /readyz`
   returns 200 once the app can reach the database — confirms the whole
   chain (image, secrets injection, network path to the DB) actually
   works, not just that the container started.

## Onboarding a new customer

`scripts/onboard_customer.py` (Phase 12, config shape updated in Phase 4)
creates an organization, one facility, its first admin user, a membership
binding them together, and optionally an initial contract + fee-schedule
version. It is a script, not an API endpoint, on purpose: everything the
API layer does starts from an already-authenticated `AuthContext`, which
by definition doesn't exist yet for a brand-new customer, and there is no
"platform superadmin" HTTP route that could gate a `POST /organizations`
endpoint without breaking the resolved-access boundary this build has
maintained since Phase 4. It connects directly with **the owner role's**
`DATABASE_URL` (see below for why) and calls straight into
`db.repository`, the same adapter `PostgresRepository` wraps -- bypassing
HTTP entirely, the same way `scripts/db/init_roles.sql` is a
direct-DB-access, operator-run step rather than an endpoint.

1. Write a JSON config, e.g. `onboard_riverside.json`:
   ```json
   {
     "org_name": "Riverside ASC",
     "org_type": "ASC",
     "admin_subject": "auth0|riverside-admin",
     "admin_role": "org_admin",
     "contract": {
       "payer_id": "ACME-PPO",
       "name": "Acme Health PPO",
       "effective_from": "2026-01-01",
       "fee_schedule": { "99213": "100.00", "99214": "150.00" }
     }
   }
   ```
   `admin_subject` is the bearer token `sub` claim the real IdP will issue
   for this user (see `src/api/auth.py`) -- this script only creates the
   `users` row plus a `memberships` row binding it to the new org and
   role, it does not create the IdP account itself, which is out of this
   codebase's scope. `org_type` defaults to `"ASC"` (a single-facility
   customer, the common case); use `"ASC_GROUP"` or `"BILLING_COMPANY"`
   for a multi-facility customer and onboard its facilities separately
   via the existing API once the admin can authenticate. `facility_name`
   defaults to `org_name` if omitted. `contract` is optional; omit it to
   onboard a customer with no fee schedule yet and load one later via
   `POST /contracts` + `POST /contracts/{id}/versions`. Payment rules
   (MPPR, bilateral, assistant surgeon, implant carve-out) always start
   disabled here for the same reason -- configure them via that same
   endpoint once the customer can authenticate. `kms_key_id` is also
   optional (Phase 6, per-org encryption keys) -- omit it for "use the
   platform default"; only set it when the deployment already runs a
   real cloud KMS (`KMS_PROVIDER=aws-kms`/`azure-keyvault`), see "Per-org
   encryption keys (BYOK)" below for why setting it any earlier breaks
   ingestion for that org.

2. Run it, with **owner-role** credentials (not `asc_app`):
   ```
   DATABASE_URL="<asc_owner connection string>" \
     PYTHONPATH=src python scripts/onboard_customer.py onboard_riverside.json
   ```
   `organizations`/`facilities`/`memberships` are RLS-protected against
   *resolved* access, and there is no membership yet for a brand-new
   customer to resolve against -- the same bootstrap problem
   `alembic/versions/0001_initial_schema.py`'s own docstring describes
   for the `resolve_accessible_*` functions, and why `asc_owner` needs
   `BYPASSRLS` (see `docs/DB_SETUP.md`). Running this with `asc_app`
   credentials will fail with a row-level-security policy violation on
   the very first insert.

   Prints the new organization/facility/user/membership/contract ids on
   success. Not run against a real Postgres in the environment this was
   authored in -- verified so far only via `ruff`/`mypy --strict` and a
   fake `DATABASE_URL` that exercises config validation without a live
   connection; get a real run against a provisioned database before
   trusting it for an actual pilot customer.

## Managing users, API keys, and org policy

`docs/MASTER-BUILD-PROMPT-V2.md`'s Phase 5 (not to be confused with this
runbook's other "Phase" references above, which are the original
12-phase build's numbering — see `docs/PROGRESS.md`'s own note on this
collision). Everything below is an ordinary authenticated HTTP call, not
a script — the operator is a logged-in `org_admin`/`platform_admin`
(`manage_users`, `docs/PERMISSIONS.md`), never a direct DB connection.
`$TOKEN` below is that admin's access token from `POST /auth/login`.

### Inviting a user

```
curl -X POST https://<host>/invitations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"subject": "new-hire@example.com", "role": "biller", "scope": "ALL_FACILITIES"}'
```

Returns the raw invitation token **once** — there is no email-sending
infrastructure in this codebase, so deliver it to the invitee out of
band (Slack, a ticket, whatever the operator already uses). The invitee
then, unauthenticated, walks the token through:

```
GET  /invitations/{token}                 # preview: subject, role, status, expiry
POST /invitations/{token}/accept          # sets password, returns an MFA secret + otpauth:// URI once
POST /invitations/{token}/confirm-mfa     # verifies the TOTP code before their first real login
```

After `confirm-mfa` succeeds, `POST /auth/login` (subject + password +
TOTP code) just works — no separate "activate" step.

### Offboarding a user

Find their `membership_id` (there is no by-subject lookup; list and
match):

```
curl -H "Authorization: Bearer $TOKEN" https://<host>/organizations/members
```

Then revoke it:

```
curl -X POST https://<host>/organizations/members/{membership_id}/revoke \
  -H "Authorization: Bearer $TOKEN"
```

Takes effect on the *very next request* the offboarded user (or any API
key resolving through their identity) makes, on any route — role/access
is resolved fresh from `memberships` on every request, so there is no
session-cache or token-revocation-list propagation delay to wait out.
Revoking an already-revoked membership, or one belonging to another org,
returns 404 either way (`tests/api/test_offboarding.py`).

### Provisioning and revoking an API key

```
curl -X POST https://<host>/api-keys \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Zapier integration", "scope": "ALL_FACILITIES"}'
```

Returns the raw key **once**, prefixed `ask_` — store it in whatever
secret manager the integration uses; it cannot be recovered afterward,
only revoked and replaced. Use it exactly like a JWT, as a bearer token:
`Authorization: Bearer ask_...`. It authenticates as its own narrowly
scoped `api_service`-role service user (`docs/PERMISSIONS.md`), never as
the admin who created it.

```
curl -H "Authorization: Bearer $TOKEN" https://<host>/api-keys        # list, masked
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://<host>/api-keys/{api_key_id}/revoke                         # revoke
```

A key expires automatically 365 days after creation regardless (fixed
server-side TTL, not client-configurable); revoke it sooner the moment
an integration is decommissioned or a key is suspected leaked.

### Configuring per-org session/access policy

```
curl -H "Authorization: Bearer $TOKEN" https://<host>/org-policy

curl -X PUT https://<host>/org-policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"session_timeout_seconds": 1800, "ip_allowlist": ["203.0.113.0/24"]}'
```

`PUT` is a full replace — always send both fields, `ip_allowlist: []`
means "no restriction." `session_timeout_seconds` (60–86400) shortens or
lengthens how long a login session lasts before re-authentication is
required; it only takes effect for *new* logins, not sessions already
issued. **`ip_allowlist` takes effect immediately, for every already-
issued token and API key too** — setting a restrictive one from outside
the range it names locks the operator out on their very next request, so
double-check the value (a bare IP, or CIDR — `10.0.0.0/8`) before
sending it, from a network that's actually in it. There is no `mfa_required`
field to set here at all — MFA is unconditional for every org, no
exceptions, by design.

## Scheduling remittance polling (SFTP/S3)

`scripts/ingestion/poll_remittances.py` (F-18, `docs/audit/REGISTER.md`)
polls one facility's configured SFTP directory or S3 prefix once,
ingesting whatever's new, then exits. It is a script, not a service this
codebase runs continuously, and it stays that way deliberately -- it
already satisfies "not inside an HTTP request" via external scheduling,
so Phase 7's job queue (below) never routes through it; the phase's own
gate is specifically about the `POST /remittances` upload path. A real
deployment schedules it with whatever it already has: a Kubernetes
`CronJob`, a cloud
provider's scheduled task (ECS Scheduled Task, Azure Container Apps Jobs,
etc.), or plain cron on a long-lived host. Direct upload
(`POST /remittances`) needs none of this and keeps working regardless --
polling is for payers that only ever push files to a mailbox, never a
person uploading through the UI.

1. Install the extra SDKs this one script needs (deliberately not in the
   main application's dependencies -- see `pyproject.toml`'s `[poller]`
   extra):
   ```
   pip install -e ".[poller]"
   ```

2. Run it once, by hand, to prove the configuration is right, e.g. for S3:
   ```
   USER_ID="<service-account user uuid>" FACILITY_ID="<facility uuid>" SOURCE_KIND=s3 \
     S3_BUCKET="acme-remittances" S3_PREFIX="incoming/" AWS_REGION=us-east-1 \
     DATABASE_URL="<asc_app connection string>" \
     PHI_ENCRYPTION_KEY="<same value main.py uses>" \
     PYTHONPATH=src python scripts/ingestion/poll_remittances.py
   ```
   or for SFTP:
   ```
   USER_ID="<service-account user uuid>" FACILITY_ID="<facility uuid>" SOURCE_KIND=sftp \
     SFTP_HOST="sftp.payer.example" SFTP_USERNAME="..." SFTP_PASSWORD="..." \
     SFTP_DIR="/outgoing" \
     DATABASE_URL="<asc_app connection string>" \
     PHI_ENCRYPTION_KEY="<same value main.py uses>" \
     PYTHONPATH=src python scripts/ingestion/poll_remittances.py
   ```
   `USER_ID` must be a user holding a membership that resolves access to
   `FACILITY_ID` (Phase 4) -- ideally a service-account user provisioned
   for this poller once Phase 5's API-key/`api_service` role machinery
   exists; until then, any qualifying user works. Prints one line per
   file it found (ingested, quarantined, or duplicate). A file already
   ingested on a prior run is re-fetched and re-hashed every time
   (there's no persisted "already seen" state across invocations -- the
   `remittances` table stores a content hash, not the original filename)
   and correctly comes back a duplicate; this is wasteful on a very
   large mailbox, never incorrect.

3. Point your scheduler at the same command, on whatever interval matches
   the payer's actual delivery cadence (hourly is a reasonable default
   for most payers). One scheduled job per facility per source.

Not run against a real SFTP server or a real AWS account in the
environment this was authored in -- the two real client adapters in that
script (`_ParamikoSFTPClient`, `_Boto3S3Client`) are verified only via
`ruff`/`mypy --strict` and the fully-tested, fake-client-backed
`SFTPPollSource`/`S3PollSource`/`poll_and_ingest` underneath them (see
`tests/ingestion/test_sources.py`, `test_poller.py`); get a real run
against a real mailbox before trusting this for a live payer feed.

## Operating the job queue (Phase 7)

`POST /remittances` no longer runs ingestion in-request -- it enqueues a
row in the `jobs` table and returns `202` with a `job_id`. One or more
worker processes (`python -m worker`, `src/worker.py`; the `worker`
service in `docker-compose.yml`, the second ECS service/Container App
Terraform provisions alongside `app`) poll that table and run jobs to
completion. `GET /jobs/{job_id}` reports status/progress/result;
`POST /jobs/{job_id}/cancel` requests cooperative cancellation.

**Scaling worker throughput.** The API and worker scale independently --
`desired_count`/`min_replicas` (API, request-serving) vs.
`worker_desired_count`/`worker_min_replicas`+`worker_max_replicas`
(worker, queue-draining) are separate Terraform variables in
`terraform/modules/{aws,azure}/variables.tf`. Concurrency is also capped
*per org*, not per worker count: `claim_next_job`'s claim query
(`db/repository.py`) refuses to hand out a job to a org that already has
`per_org_limit` (default 3) jobs `running`, regardless of how many idle
workers exist -- adding workers speeds up draining the queue *across*
orgs, not a single large customer's own backlog past that ceiling.

**Inspecting the queue.** Query `jobs` directly (as `asc_owner` --
`status` isn't PHI, but this bypasses RLS, so treat the connection itself
the same as any other owner-role access):

```sql
-- what's queued or running right now, oldest first
SELECT id, facility_id, job_type, status, attempts, next_run_at, locked_by
FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at;

-- stuck: claimed by a worker that's gone quiet (stale_lock_after is 10
-- minutes by default, db/repository.py's claim query already reclaims
-- these automatically -- this query is for confirming *why* one hasn't
-- moved yet, not for manually intervening)
SELECT id, job_type, locked_by, locked_at FROM jobs
WHERE status = 'running' AND locked_at < now() - interval '10 minutes';

-- dead-lettered: exhausted max_attempts (default 5), needs a human
SELECT id, facility_id, job_type, attempts, error, completed_at
FROM jobs WHERE status = 'dead_letter' ORDER BY completed_at DESC;
```

A dead-lettered job already fired a `job_dead_lettered` alert
(`observability/alerts.py::evaluate_job_dead_lettered_alert`, via the
same `NotificationPort` every other alert in this codebase uses) and
wrote a `job_dead_lettered` audit-log entry at the moment it happened --
there is no separate polling step to catch these after the fact, only to
investigate `error` (already redacted via `security/redaction.py` before
storage, so it's safe to read directly) and decide whether to re-enqueue
(a fresh `POST /remittances`, not a status flip -- there is no "retry a
dead-lettered row in place" primitive) once the underlying cause is
fixed.

**Cancellation.** `POST /jobs/{id}/cancel` sets `cancel_requested`; it is
cooperative, not instant -- the worker checks it roughly every 25 claims
processed (`ingestion/apply.py::_CALLBACK_INTERVAL`) inside the ingestion
loop, so a job already deep into a large file can take a moment to
actually stop. When it does, the whole job's DB work rolls back (the
`JobCancelledError` propagates out of the `access_session` transaction
uncaught) -- a cancelled job never leaves partial claims/findings behind,
it is as if it never ran, and `GET /jobs/{id}` reports `status:
"cancelled"` once the rollback has happened.

## Rollback

Re-run step 5 above with the previous image tag — both Terraform modules
treat `container_image` as a plain variable, so rolling back is "apply
with the old value," not a special procedure. **If the rollback also
needs to undo a migration**, do not run `alembic downgrade` against a
live system with real data without a specific, reviewed plan for that
migration — see "Zero-downtime migrations" below for why every migration
so far has been written to make this unnecessary in the common case.

## Restore (backup verification)

RDS and Azure Database for PostgreSQL Flexible Server both take automated
daily backups with point-in-time recovery, provisioned by Terraform
(`db_backup_retention_days`, default 14 days in both modules).

**Procedure** (to be executed and timed against a real provisioned
database — not done here, no such database exists in this environment):

1. Restore to a new, separate instance from the most recent automated
   backup (AWS: `aws rds restore-db-instance-to-point-in-time`; Azure:
   `az postgres flexible-server restore`) — never restore over the live
   instance.
2. Point a disposable copy of the app (or just `psql`) at the restored
   instance's endpoint.
3. Confirm: `alembic_version` matches what's expected, row counts on
   `claims`/`findings`/`recovery_packets` are sane, RLS policies are
   present (`\d+ claims` shows `Policies` — the migration re-creates
   these, so a fresh restore should already have them).
4. **Record the wall-clock time the restore took**, start to finish
   (data usable + verified, not just "instance says available"). An
   untested, untimed backup is not a backup — this number is what "restore
   rehearsed and timed" in the Phase 9 gate actually means, and it can
   only be produced by doing this for real.
5. Tear down the restored instance once verified (avoid double-billing).

`scripts/db/restore_drill.sh` (F-21, `docs/audit/REGISTER.md`) automates
steps 1–5 above end to end — restore, wait, verify, print the elapsed
time, tear down (via a `trap` so a failed verification still cleans up).
It has never been run against a real AWS or Azure account, same
disclosure as every other real-cloud script in this repo; running it for
real, once a provisioned database exists, is what actually closes F-21:

```
CLOUD=aws \
AWS_SOURCE_DB_IDENTIFIER=asc-recovery-prod-postgres \
DATABASE_URL_TEMPLATE='postgresql://asc_owner:PASSWORD@%s:5432/asc_recovery?sslmode=require' \
  ./scripts/db/restore_drill.sh
```

or for Azure:

```
CLOUD=azure \
AZURE_SOURCE_SERVER_NAME=asc-recovery-prod-postgres \
AZURE_RESOURCE_GROUP=asc-recovery-prod-rg \
DATABASE_URL_TEMPLATE='postgresql://asc_owner:PASSWORD@%s:5432/asc_recovery?sslmode=require' \
  ./scripts/db/restore_drill.sh
```

Record the printed elapsed-time result in this section and in F-21's
`docs/audit/REGISTER.md` row once run for real.

## CI/CD pipeline

Phase 10 added three GitHub Actions workflows. Unlike everything else in
this section, **the `ci.yml` stages actually run and are actually
verified** — GitHub-hosted runners have Docker and can spin up a real
Postgres service container, which is more than this repo has ever had
available before. `deploy.yml`'s cloud-dependent stages are the exception:
they're written and structurally correct but cannot run for real until the
one-time setup below happens.

- **`.github/workflows/ci.yml`** — runs on every push/PR to `master`:
  `lint` (ruff, mypy --strict, lockfile-freshness check) → `test` (real
  Postgres 16 service container; `scripts/db/init_roles.sql`; `alembic
  upgrade head`; the full `pytest -q`, including every test that has been
  silently skipping locally since Phase 3 for lack of a live database) →
  `security` (bandit, pip-audit, full-history `gitleaks`) → `sbom`
  (CycloneDX, uploaded as a build artifact, not committed) →
  `container-scan` (`docker build` + `trivy`, fails on fixable
  CRITICAL/HIGH) → `iac-scan` (`terraform validate` for both clouds +
  `tfsec`).
- **`.github/workflows/deploy.yml`** — triggers after `ci.yml` succeeds on
  `master`, or manually via `workflow_dispatch`: build/push image → deploy
  to a `staging` Terraform workspace → smoke test (`/healthz`, `/readyz`)
  → OWASP ZAP baseline (passive) scan against staging → **manual
  approval gate** → deploy to the `production` workspace. AWS and Azure
  are separate, parallel job chains — see "One-time cloud setup" below.
- **`.github/workflows/scheduled-security-scan.yml`** — runs every six
  months (`cron: "0 0 1 1,7 *"`), re-running bandit/pip-audit/trivy against
  the current codebase and opening a GitHub issue if anything new turns
  up. This satisfies the 2026 HIPAA Security Rule's six-monthly
  vulnerability-scan requirement; **the Rule's annual penetration test is
  not something this repo can automate** — that's hiring a third-party
  firm, a Phase 11 process/procurement item, tracked outside of code.

### One-time cloud setup (before `deploy.yml`'s cloud jobs can run for real)

Every cloud-dependent job in `deploy.yml` is guarded by
`if: secrets.<...> != ''`, so until the secrets below exist it shows as
**skipped**, not failed — an accurate status, not a permanently red
pipeline for infrastructure nobody has provisioned yet.

**AWS** (OIDC role assumption — no long-lived access keys stored in
GitHub):
1. Create an IAM OIDC identity provider for `token.actions.githubusercontent.com`
   (one per AWS account, if one doesn't already exist for other repos).
2. Create an IAM role trusting that provider, scoped (via the trust
   policy's `sub` condition) to this repo, with permissions to run
   `terraform apply` for `terraform/modules/aws` plus ECR push access, plus
   `secretsmanager:GetSecretValue` on the RDS-managed master-user secret
   (`aws_db_instance.main.master_user_secret[0].secret_arn`) and on
   `aws_secretsmanager_secret.app` — needed by `deploy.yml`'s migration
   step (`scripts/deploy/migrate.sh`, F-03 in `docs/audit/REGISTER.md`) to
   bootstrap the `asc_app` role and run Alembic against a fresh database.
3. Add repo secrets: `AWS_DEPLOY_ROLE_ARN` (the role from step 2),
   `AWS_ECR_REPOSITORY` (an ECR repository URI — Terraform doesn't
   provision this; create it once, separately), `AWS_REGION` (optional,
   defaults to `us-east-1`), `AWS_ACM_CERTIFICATE_ARN` (an ACM
   certificate for the app's real domain, already issued and validated —
   Terraform doesn't provision Route53/ACM either, same reasoning as the
   ECR repository above; without this the AWS deploy jobs stay skipped,
   same as without `AWS_DEPLOY_ROLE_ARN`, per F-07 in
   `docs/audit/REGISTER.md`).

**Azure** (federated credentials — same no-static-secrets principle):
1. Create an Azure AD App Registration with a federated credential
   trusting `token.actions.githubusercontent.com`, scoped to this repo.
2. Grant that App Registration's service principal Contributor on the
   target subscription/resource group.
3. Add repo secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID`, `AZURE_ACR_REGISTRY` (an ACR login server —
   also not Terraform-provisioned, create it once, separately).

**Both clouds**: in this repo's Settings → Environments, create a
`production` environment with a required-reviewers rule. That's what
turns `deploy-production-*`'s `environment: production` reference into an
actual pause-for-approval gate — the workflow YAML alone doesn't create
that requirement, GitHub's environment protection rules do.

None of the above has been done in the environment this was authored in
— no AWS/Azure account exists here. This is the same category of gap as
Terraform `apply` in the "Deploy" section above: written and structurally
reviewed, not exercised against real infrastructure.

## Key rotation

`EnvelopeEncryptor.rotate_kek()` (Phase 4, `src/security/encryption.py`,
already tested) re-wraps only the DEK, never touching ciphertext — this
is the application-level mechanism. At the infrastructure level: AWS KMS
key rotation is enabled (`enable_key_rotation = true` in
`terraform/modules/aws/secrets_and_kms.tf`) and rotates automatically
annually; Azure Key Vault's key has an explicit `rotation_policy`
(`terraform/modules/azure/secrets_and_kms.tf`, 30 days before a 1-year
expiry).

By default the app still runs on `EnvKMS` (a static in-secret KEK, see
`src/security/kms_env.py`), so today's cloud key rotation described above
rotates key material that nothing in the running application actually
calls. Real adapters exist to close that gap (F-20, `docs/audit/REGISTER.md`;
`src/security/kms_aws.py`, `src/security/kms_azure.py`) but neither has
ever been exercised against a real AWS or Azure account — enabling one is
a deliberate, opt-in operator choice, not a default:

- **`KMS_PROVIDER` unset or `"env"`** (default, unchanged): `EnvKMS`,
  requires `PHI_ENCRYPTION_KEY`.
- **`KMS_PROVIDER=aws-kms`**: `AwsKmsAdapter`. Requires
  `AWS_KMS_KEY_ID` — set this to a KMS *alias* (e.g.
  `alias/asc-recovery-kek`), not a raw key id, so AWS's own annual
  rotation takes effect with zero redeploys. `AWS_REGION` optional
  (falls back to the SDK's normal resolution). Install
  `pip install -e ".[cloud-kms]"` first — `boto3` is not a base
  dependency.
- **`KMS_PROVIDER=azure-keyvault`**: `AzureKeyVaultAdapter`. Requires
  `AZURE_KEY_VAULT_KEY_ID`, the full versioned key id
  (`https://<vault>.vault.azure.net/keys/<name>/<version>`) — unlike an
  AWS alias, an Azure Key Vault key *version* is a distinct
  cryptographic object, so picking up a rotation means redeploying with
  the new version's id and running `EnvelopeEncryptor.rotate_kek()`
  against it; there is no automatic "use whatever's current" option.
  Credentials come from `DefaultAzureCredential` (managed identity in a
  real deployment), never a static secret this codebase stores. Install
  `pip install -e ".[cloud-kms]"` first.

## Per-org encryption keys (BYOK)

`docs/MASTER-BUILD-PROMPT-V2.md` Phase 6: an organization can have its
own dedicated KMS key (`organizations.kms_key_id`) instead of sharing
the platform's default KEK — set at onboarding time via
`scripts/onboard_customer.py`'s optional `kms_key_id` config field, or
afterward via a direct, reviewed `UPDATE organizations SET kms_key_id =
'...' WHERE id = '...'` through the **owner** connection (there is no
self-service API for this — an org admin accidentally pointing their
own org at an unreadable key is exactly the kind of high-blast-radius
mistake this deliberately keeps behind an operator, not a self-service
endpoint). Existing data is never re-encrypted by setting this column —
only claims ingested *after* the change use the new key; each already-
encrypted value's own stored `kek_id` is what it always decrypts under
(`security/encryption.py`'s `EncryptedPayload.kek_id`), regardless of
what an org's "current" key is today.

**Only meaningful once `KMS_PROVIDER` is `aws-kms` or `azure-keyvault`.**
`EnvKMS` (the default stopgap adapter) holds exactly one static key and
deliberately raises `KeyError` for any other `kek_id` — setting
`kms_key_id` on an org while running on `EnvKMS` will make ingestion
fail (loudly, not silently) for that org's claims the moment a patient
name/member id needs encrypting. Do not set this column until the
deployment is actually running a real cloud KMS.

## Per-org data residency

`docs/MASTER-BUILD-PROMPT-V2.md` Phase 6's last item —
`org_policies.data_residency_region`, set via `PUT /org-policy` like
any other per-org setting (`org_admin`/`platform_admin`, `docs/PERMISSIONS.md`):

```
curl -X PUT https://<host>/org-policy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"session_timeout_seconds": null, "ip_allowlist": [], "data_residency_region": "us-east-1"}'
```

**This is a stored declaration, not a technical control.** This
platform runs one shared Postgres instance in one region today — there
is no per-org routing, replication, or physical placement this field
actually drives. It exists so "what region is this org's data in" has a
real, queryable answer (useful for a customer-facing security
questionnaire, `docs/compliance/SECURITY-QUESTIONNAIRE-ANSWERS.md`) —
setting it does not move any data or change where anything is deployed.
Unlike per-org encryption keys above, there is no misconfiguration risk
that locks anyone out of anything, so this one *is* a normal, self-
service `PUT`, not an operator-only DB write. If this platform ever
adds real multi-region deployment, this column becomes the source of
truth to build routing against, not a promise already kept — don't
represent it as enforced before that work exists.

## Incident response

1. **Identify**: alert fires (`observability.alerts`, Phase 8) or
   external report.
2. **Contain**: for a suspected credential compromise, rotate the
   affected secret in Secrets Manager / Key Vault immediately — every
   secret the app reads is already indirected through
   `security.secrets.EnvSecretStore`, so rotating the underlying cloud
   secret and restarting the service (no code change) is the fastest
   containment path.
3. **Assess scope**: use `GET /claims/{claim_id}/access-history` (Phase
   8) and `GET /audit-log` to reconstruct exactly which claims/PHI were
   accessed, by whom, and when.
4. **Named owner**: to be assigned before real PHI is processed — Phase
   11 scope, not a code deliverable.

## Breach notification (60-day HIPAA clock)

The 2026 HIPAA Security Rule requires notification within 60 days of
discovery. This repo's role is making discovery and scoping *fast*, not
running the notification process itself:

- Day 0 (discovery): incident response above determines scope using the
  access-history/audit-log tooling this system already has.
- The 60-day clock, the decision of *who* is notified (affected
  individuals, HHS, potentially media for large breaches), and the legal
  review of notification content are Phase 11 process/compliance work,
  requiring counsel — not something to template here without legal
  review, and explicitly out of this phase's scope per
  `docs/MASTER-BUILD-PROMPT.md`.

## Zero-downtime migrations

Every migration in this repo so far (`0002` through `0004`) has already
followed the same pattern this section formalizes: **additive only** —
a new nullable column or a new table, never a rename or drop that an
already-running old version of the app couldn't tolerate. That's what
makes rolling deploys safe without a maintenance window:

1. **Expand**: ship a migration that only adds (nullable column / new
   table). Old and new app versions both run fine against the expanded
   schema.
2. **Deploy**: roll out the new app version (reads/writes the new
   column) while old and new instances briefly coexist during a rolling
   deploy — safe, because step 1 didn't remove anything the old version
   needs.
3. **Backfill**, if needed, as a separate script/job — not inline in the
   migration that added the column (keeps the migration itself fast and
   lock-light).
4. **Contract** (only once every instance is confirmed running the new
   version): a later migration drops the now-unused old column. This is
   the one step that *isn't* zero-downtime-safe if done too early — wait
   for confirmation, don't chain it directly after step 2.

Migrations are never run automatically by the app container's entrypoint
(see `docker-compose.yml`'s comment and `terraform/README.md`) —
concurrent migration attempts from multiple replicas starting
simultaneously is exactly the failure mode this whole pattern exists to
avoid, and `asc_app` (the app's own DB role) isn't the migration-owning
role (`asc_owner`) regardless.
