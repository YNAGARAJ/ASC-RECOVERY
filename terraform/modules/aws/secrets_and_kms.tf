# KMS -- HIPAA-eligible, backs both the envelope-encryption KEK
# (src/security/kms.py's port; the real cloud KMS adapter itself is a
# named, deliberate gap -- see docs/SECURITY.md) and S3/RDS encryption
# above. Rotation enabled so a future EnvelopeEncryptor.rotate_kek()-style
# cloud adapter has something real to rotate against.

resource "aws_kms_key" "main" {
  description             = "${local.name_prefix} envelope-encryption KEK and data-at-rest encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-kms"
  })
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name_prefix}"
  target_key_id = aws_kms_key.main.key_id
}

# Secrets Manager -- HIPAA-eligible. The app's own runtime secrets
# (JWT signing key, Anthropic API key) live here; a real orchestration
# platform (ECS task definition `secrets` block) materializes these as
# environment variables at container start -- src/main.py only ever
# reads already-materialized env vars via security.secrets.EnvSecretStore,
# per this module's README.
resource "aws_secretsmanager_secret" "app" {
  name        = "${local.name_prefix}-app-secrets"
  description = "JWT_SECRET_KEY, ANTHROPIC_API_KEY -- populated out of band, never by Terraform"
  kms_key_id  = aws_kms_key.main.arn

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-app-secrets"
  })
}

# The RDS instance's master user (asc_owner) password is managed
# entirely by AWS via `manage_master_user_password` (database.tf) -- read
# it back here only to assemble the runtime `asc_app` connection string
# below. asc_app itself does not exist until the migration step
# (docs/RUNBOOK.md) creates it, connected as asc_owner using this same
# master secret -- Terraform provisions the database, it does not create
# application-level DB roles.
data "aws_secretsmanager_secret_version" "db_master" {
  secret_id = aws_db_instance.main.master_user_secret[0].secret_arn
}

resource "random_password" "app_db" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "app_database_url" {
  name        = "${local.name_prefix}-app-database-url"
  description = "Full DATABASE_URL for the asc_app runtime role -- injected into the ECS task as an env var"
  kms_key_id  = aws_kms_key.main.arn

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-app-database-url"
  })
}

resource "aws_secretsmanager_secret_version" "app_database_url" {
  secret_id = aws_secretsmanager_secret.app_database_url.id
  secret_string = "postgresql+psycopg://asc_app:${random_password.app_db.result}@${aws_db_instance.main.address}:5432/asc_recovery"
}
