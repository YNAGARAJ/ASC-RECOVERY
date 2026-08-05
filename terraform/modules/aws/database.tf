# RDS for PostgreSQL -- on AWS's HIPAA-eligible services list. Private
# (no public IP), encrypted at rest, automated backups with the retention
# window Terraform-managed rather than left to console defaults. This
# provisions the database; it does not run Alembic migrations or create
# the asc_owner/asc_app roles -- see docs/RUNBOOK.md for that separate,
# explicit step (never automatic on deploy -- see docs/PROGRESS.md's
# Phase 3 notes on why asc_app is deliberately not the migration-owning
# role).

resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db"
  subnet_ids = aws_subnet.private[*].id

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-db-subnet-group"
  })
}

resource "aws_db_instance" "main" {
  identifier     = "${local.name_prefix}-postgres"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage_gb
  max_allocated_storage = var.db_allocated_storage_gb * 4
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.main.arn

  db_name  = "asc_recovery"
  username = "asc_owner"
  # The owner password is generated once and stored only in Secrets
  # Manager (see secrets_and_kms.tf) -- never a plaintext var, never
  # echoed in `terraform plan` output for a value that changes.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false
  multi_az               = true

  backup_retention_period = var.db_backup_retention_days
  backup_window           = "03:00-04:00"
  maintenance_window      = "mon:04:30-mon:05:30"
  copy_tags_to_snapshot   = true
  deletion_protection     = true
  skip_final_snapshot     = false
  final_snapshot_identifier = "${local.name_prefix}-postgres-final"

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-postgres"
  })
}
