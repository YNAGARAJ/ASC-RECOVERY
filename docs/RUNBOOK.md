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
