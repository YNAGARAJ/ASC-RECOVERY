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

`scripts/onboard_customer.py` (Phase 12) creates a tenant, its first admin
user, and optionally an initial contract + fee-schedule version. It is a
script, not an API endpoint, on purpose: `security/rbac.py` is entirely
tenant-scoped, and there is no "platform superadmin" role that could gate a
`POST /tenants` endpoint without breaking the no-cross-tenant-access
boundary this build has maintained since Phase 3. It connects directly with
the application's own `DATABASE_URL` (the `asc_app` role) and calls
straight into `db.repository`, the same adapter `PostgresRepository` wraps
-- bypassing HTTP entirely, the same way `scripts/db/init_roles.sql` is a
direct-DB-access, operator-run step rather than an endpoint.

1. Write a JSON config, e.g. `onboard_riverside.json`:
   ```json
   {
     "tenant_name": "Riverside ASC",
     "admin_subject": "auth0|riverside-admin",
     "admin_role": "admin",
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
   `users` row that maps it to a tenant and role, it does not create the
   IdP account itself, which is out of this codebase's scope. `contract`
   is optional; omit it to onboard a tenant with no fee schedule yet and
   load one later via `POST /contracts` + `POST /contracts/{id}/versions`.
   Payment rules (MPPR, bilateral, assistant surgeon, implant carve-out)
   always start disabled here for the same reason -- configure them via
   that same endpoint once the tenant can authenticate.

2. Run it:
   ```
   DATABASE_URL="<asc_app connection string>" \
     PYTHONPATH=src python scripts/onboard_customer.py onboard_riverside.json
   ```
   Prints the new tenant/user/contract ids on success. Not run against a
   real Postgres in the environment this was authored in -- verified so far
   only via `ruff`/`mypy --strict` and a fake `DATABASE_URL` that exercises
   config validation without a live connection; get a real run against a
   provisioned database before trusting it for an actual pilot customer.

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
   `terraform apply` for `terraform/modules/aws` plus ECR push access.
3. Add repo secrets: `AWS_DEPLOY_ROLE_ARN` (the role from step 2),
   `AWS_ECR_REPOSITORY` (an ECR repository URI — Terraform doesn't
   provision this; create it once, separately), `AWS_REGION` (optional,
   defaults to `us-east-1`).

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
expiry). Neither cloud's automatic key rotation is connected to a real
`KeyManagementService` adapter yet — that adapter is a named, deferred
gap (`docs/SECURITY.md`), so today this rotates the cloud-managed key
material but nothing in the running application calls it.

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
