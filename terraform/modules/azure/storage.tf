# Blob Storage -- HIPAA-eligible, for inbound 835 files. Public network
# access disabled at the storage-account level (not just a container-level
# setting), matching the Phase 9 prompt's "public-access-blocked at
# account level, not just bucket level" -- see terraform/README.md.

resource "azurerm_storage_account" "main" {
  name                = replace("${local.name_prefix}remit", "-", "")
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  account_tier             = "Standard"
  account_replication_type = "GRS"
  min_tls_version          = "TLS1_2"

  public_network_access_enabled    = false
  allow_nested_items_to_be_public  = false

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }
  }

  tags = var.tags
}

resource "azurerm_storage_container" "remittances" {
  name                  = "remittances"
  storage_account_name  = azurerm_storage_account.main.name
  container_access_type = "private"
}
