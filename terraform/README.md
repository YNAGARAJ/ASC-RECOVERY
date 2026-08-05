# Terraform — cloud-agnostic deployment (Phase 9)

## The provider-agnostic contract

Terraform has no clean way to make one resource block conditionally
become `aws_db_instance` or `azurerm_postgresql_flexible_server` at plan
time — a single "universal" module with per-provider conditionals is a
well-known Terraform anti-pattern (you'd need every provider's plugin
installed regardless of which cloud you deploy to, and the diff output
becomes unreadable). The correct pattern — and what "provider-agnostic
core, thin per-cloud adapters" means in practice here — is an **interface
contract**: every per-cloud module accepts the same input variables and
exposes the same output values, so switching clouds means switching which
module an environment's root config points at, not rewriting the
application's understanding of its own infrastructure.

### Inputs every module accepts

| Variable | Type | Purpose |
|---|---|---|
| `project_name` | `string` | Resource naming/tagging prefix |
| `environment` | `string` | e.g. `"production"`, `"staging"` |
| `region` | `string` | Cloud region/location identifier |
| `container_image` | `string` | Fully-qualified image reference to deploy |
| `db_backup_retention_days` | `number` | Automated backup retention |

### Outputs every module exposes

| Output | Purpose |
|---|---|
| `database_endpoint` | `host:port` the app connects to (via `DATABASE_URL`, itself assembled from this plus the secret below — never a literal password in Terraform state as plaintext beyond what the provider's own resource requires) |
| `database_secret_id` | Where the DB credentials live in that cloud's secret store |
| `object_storage_bucket_name` | Where inbound 835 files land (see `src/ingestion/sources.py`'s `S3PollSource`/equivalent) |
| `kms_key_id` | The envelope-encryption KEK's cloud-side identifier |
| `container_service_endpoint` | Where the deployed app is reachable |

## Layout

```
terraform/
  modules/
    aws/      -- VPC, RDS for PostgreSQL, S3, Secrets Manager, KMS, ECS Fargate
    azure/    -- VNet, Azure Database for PostgreSQL Flexible Server,
                 Blob Storage, Key Vault, Container Apps
  environments/
    aws/main.tf     -- root module wiring modules/aws
    azure/main.tf   -- root module wiring modules/azure
```

**Two clouds, not three.** The Phase 9 gate requires "at least two."
A GCP module following the identical contract is real, meaningful future
work, but every additional line of Terraform here is unverifiable in the
environment this was authored in (no `terraform` CLI, no cloud account
credentials) — scoping to two keeps quality higher on what's actually
built rather than spreading equal, unverified effort across three.

## HIPAA BAA eligibility

Every resource type below is deliberately chosen from that provider's
published HIPAA-eligible-services list (AWS: 166+ services under their
BAA; Azure: 80+; both cover everything used here). If a future change
adds a new resource type, check it against the current list **before**
adding it — a service not on the BAA list cannot touch PHI, full stop,
per CLAUDE.md rule 7 and `docs/MASTER-BUILD-PROMPT.md`'s Phase 9 gate.

| Concern | AWS (BAA-eligible) | Azure (BAA-eligible) |
|---|---|---|
| Network | VPC, Subnets, Security Groups, NAT Gateway | Virtual Network, Subnets, NSGs, NAT Gateway |
| Database | RDS for PostgreSQL | Azure Database for PostgreSQL Flexible Server |
| Object storage | S3 | Blob Storage |
| Secrets | Secrets Manager | Key Vault |
| Key management | KMS | Key Vault (Keys) |
| Container runtime | ECS on Fargate | Container Apps |

## What is, and is not, verified here

**Not run in the environment this was authored in**: `terraform fmt`,
`terraform validate`, `terraform plan`, `terraform apply` — no Terraform
CLI is installed, and `apply` additionally requires real AWS/Azure
accounts with billing enabled. Every `.tf` file here was hand-authored
and manually reviewed line by line in place of that tooling; treat it as
"believed correct, not machine-verified" until `terraform validate`
actually runs against it (see `docs/RUNBOOK.md`'s deploy procedure for
the exact commands). Applying this to a real account for the first time
is a real, billed, hard-to-reverse action — get sign-off before running
`terraform apply` anywhere that isn't a disposable sandbox account.
