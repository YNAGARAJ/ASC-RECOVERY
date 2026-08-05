# Azure Database for PostgreSQL Flexible Server -- HIPAA-eligible.
# VNet-integrated (no public network access), encrypted at rest,
# automated backups with a Terraform-managed retention window. This
# provisions the database only -- migrations and the asc_owner/asc_app
# role split are a separate, explicit step (docs/RUNBOOK.md), same as
# the AWS module.

resource "random_password" "admin" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                = "${local.name_prefix}-postgres"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  version    = "16"
  sku_name   = var.db_sku_name
  storage_mb = var.db_storage_mb

  administrator_login    = "asc_owner"
  administrator_password = random_password.admin.result

  delegated_subnet_id = azurerm_subnet.database.id
  private_dns_zone_id = azurerm_private_dns_zone.postgres.id

  backup_retention_days        = var.db_backup_retention_days
  geo_redundant_backup_enabled = true

  high_availability {
    mode = "ZoneRedundant"
  }

  tags = var.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "asc_recovery"
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "utf8"
}
