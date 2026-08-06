output "database_endpoint" {
  description = "Fully-qualified domain name the app connects to."
  value       = "${azurerm_postgresql_flexible_server.main.fqdn}:5432"
}

output "database_secret_id" {
  description = "Key Vault secret holding the asc_app DATABASE_URL."
  value       = azurerm_key_vault_secret.app_database_url.id
}

output "object_storage_bucket_name" {
  description = "Blob Storage container for inbound 835 files."
  value       = azurerm_storage_container.remittances.name
}

output "kms_key_id" {
  description = "Key Vault key backing envelope encryption and data-at-rest encryption."
  value       = azurerm_key_vault_key.main.id
}

output "container_service_endpoint" {
  description = "Container App's public ingress FQDN."
  value       = azurerm_container_app.app.latest_revision_fqdn
}

# F-03 (docs/audit/REGISTER.md): the deploy pipeline needs these three to
# bootstrap the asc_app role and run migrations against a fresh database
# before anything smoke-tests the service -- see scripts/deploy/migrate.sh
# and .github/workflows/deploy.yml.
output "database_admin_username" {
  description = "asc_owner admin login name."
  value       = "asc_owner"
}

output "database_admin_password" {
  description = "asc_owner admin password (Terraform-managed random_password)."
  value       = random_password.admin.result
  sensitive   = true
}

output "app_db_password" {
  description = "The asc_app role's real, Terraform-generated password."
  value       = random_password.app_db.result
  sensitive   = true
}
