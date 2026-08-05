# Key Vault -- HIPAA-eligible, serves both roles AWS splits across two
# services: secret storage (JWT_SECRET_KEY, ANTHROPIC_API_KEY, the
# assembled asc_app DATABASE_URL) and key management (a Key Vault Key
# backing envelope encryption, once a real cloud KMS adapter exists --
# src/security/kms.py). Purge protection enabled so a key/secret can't be
# permanently destroyed by an accidental delete within the retention
# window -- relevant given the 6-year HIPAA documentation requirement.

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "main" {
  name                = "${local.name_prefix}-kv"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  purge_protection_enabled   = true
  soft_delete_retention_days = 90

  public_network_access_enabled = false

  tags = var.tags
}

resource "azurerm_key_vault_access_policy" "deployer" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = data.azurerm_client_config.current.object_id

  secret_permissions = ["Get", "Set", "List", "Delete", "Purge"]
  key_permissions    = ["Get", "Create", "List", "Rotate"]
}

resource "azurerm_key_vault_key" "main" {
  name         = "${local.name_prefix}-kek"
  key_vault_id = azurerm_key_vault.main.id
  key_type     = "RSA"
  key_size     = 4096
  key_opts     = ["decrypt", "encrypt", "wrapKey", "unwrapKey"]

  rotation_policy {
    automatic {
      time_before_expiry = "P30D"
    }
    expire_after         = "P1Y"
    notify_before_expiry = "P30D"
  }

  depends_on = [azurerm_key_vault_access_policy.deployer]
}

resource "random_password" "app_db" {
  length  = 32
  special = false
}

# asc_app role creation happens in the migration step (docs/RUNBOOK.md),
# connected as asc_owner using the admin password above -- Terraform
# provisions the database, it does not create application-level DB roles.
resource "azurerm_key_vault_secret" "app_database_url" {
  name         = "app-database-url"
  key_vault_id = azurerm_key_vault.main.id
  value        = "postgresql+psycopg://asc_app:${random_password.app_db.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/asc_recovery"

  depends_on = [azurerm_key_vault_access_policy.deployer]
}

resource "azurerm_key_vault_secret" "jwt_secret_key" {
  name         = "jwt-secret-key"
  key_vault_id = azurerm_key_vault.main.id
  value        = "REPLACE_ME_OUT_OF_BAND" # populated out of band, never by Terraform

  lifecycle {
    ignore_changes = [value]
  }

  depends_on = [azurerm_key_vault_access_policy.deployer]
}

resource "azurerm_key_vault_secret" "anthropic_api_key" {
  name         = "anthropic-api-key"
  key_vault_id = azurerm_key_vault.main.id
  value        = "REPLACE_ME_OUT_OF_BAND" # populated out of band, never by Terraform

  lifecycle {
    ignore_changes = [value]
  }

  depends_on = [azurerm_key_vault_access_policy.deployer]
}
