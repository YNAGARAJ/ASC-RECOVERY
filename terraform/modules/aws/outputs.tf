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
