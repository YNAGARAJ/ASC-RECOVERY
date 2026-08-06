output "database_endpoint" {
  description = "host:port the app connects to."
  value       = "${aws_db_instance.main.address}:${aws_db_instance.main.port}"
}

output "database_secret_id" {
  description = "Secrets Manager secret holding the asc_app DATABASE_URL."
  value       = aws_secretsmanager_secret.app_database_url.id
}

output "object_storage_bucket_name" {
  description = "S3 bucket for inbound 835 files."
  value       = aws_s3_bucket.remittances.bucket
}

output "kms_key_id" {
  description = "KMS key backing envelope encryption and data-at-rest encryption."
  value       = aws_kms_key.main.key_id
}

output "container_service_endpoint" {
  description = "ALB DNS name the app is reachable at."
  value       = aws_lb.main.dns_name
}

# F-03 (docs/audit/REGISTER.md): the deploy pipeline needs these two to
# bootstrap the asc_app role and run migrations against a fresh database
# before anything smoke-tests the service -- see scripts/deploy/migrate.sh
# and .github/workflows/deploy.yml.
output "database_admin_secret_id" {
  description = "Secrets Manager secret holding the AWS-managed asc_owner (admin) credentials, as {username, password} JSON."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "app_db_password" {
  description = "The asc_app role's real, Terraform-generated password."
  value       = random_password.app_db.result
  sensitive   = true
}
