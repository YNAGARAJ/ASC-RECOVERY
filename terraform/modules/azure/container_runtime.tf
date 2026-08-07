# Azure Container Apps -- HIPAA-eligible container runtime, VNet
# integrated so traffic to the database stays inside the VNet. Ingress
# health checks target /healthz (liveness), matching
# src/api/routes/health.py.

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${local.name_prefix}-logs"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "${local.name_prefix}-env"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = azurerm_subnet.container_apps.id

  tags = var.tags
}

# User-assigned identity so the Container App can read secrets from Key
# Vault without embedding a credential anywhere -- the standard Azure
# integration point mentioned in terraform/README.md: the platform
# materializes these as env vars at container start, src/main.py only
# ever reads already-materialized environment variables.
resource "azurerm_user_assigned_identity" "app" {
  name                = "${local.name_prefix}-app-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags
}

resource "azurerm_key_vault_access_policy" "app" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.app.principal_id

  secret_permissions = ["Get"]
}

resource "azurerm_container_app" "app" {
  name                         = "${local.name_prefix}-app"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = azurerm_key_vault_secret.app_database_url.id
    identity            = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = "jwt-secret-key"
    key_vault_secret_id = azurerm_key_vault_secret.jwt_secret_key.id
    identity            = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = "anthropic-api-key"
    key_vault_secret_id = azurerm_key_vault_secret.anthropic_api_key.id
    identity            = azurerm_user_assigned_identity.app.id
  }

  # F-02 (docs/audit/REGISTER.md): required by src/main.py at startup.
  secret {
    name                = "phi-encryption-key"
    key_vault_secret_id = azurerm_key_vault_secret.phi_encryption_key.id
    identity            = azurerm_user_assigned_identity.app.id
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "app"
      image  = var.container_image
      cpu    = var.container_cpu
      memory = var.container_memory

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "JWT_SECRET_KEY"
        secret_name = "jwt-secret-key"
      }
      env {
        name        = "ANTHROPIC_API_KEY"
        secret_name = "anthropic-api-key"
      }
      env {
        name        = "PHI_ENCRYPTION_KEY"
        secret_name = "phi-encryption-key"
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8000
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/readyz"
        port      = 8000
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  tags = var.tags

  depends_on = [azurerm_key_vault_access_policy.app]
}

# Phase 7's job-queue worker -- same image as `app`, `command` overridden
# to run the polling loop (src/worker.py) instead of uvicorn. No `ingress`
# block at all (not "external_enabled = false" -- omitted entirely): this
# process never accepts inbound traffic, only claims rows from the `jobs`
# table, so it needs no target port. Reuses the same user-assigned
# identity/Key Vault access policy as `app` -- its actual permission needs
# (read three Key Vault secrets) are a subset, so a separate identity
# isn't worth the duplication yet, same choice this file's AWS counterpart
# makes reusing aws_iam_role.task for its worker task definition.
resource "azurerm_container_app" "worker" {
  name                         = "${local.name_prefix}-worker"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = azurerm_key_vault_secret.app_database_url.id
    identity            = azurerm_user_assigned_identity.app.id
  }

  # BYPASSRLS role, worker-only -- see secrets_and_kms.tf's own comment on
  # why this is never wired into the `app` container app above.
  secret {
    name                = "queue-database-url"
    key_vault_secret_id = azurerm_key_vault_secret.queue_database_url.id
    identity            = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = "phi-encryption-key"
    key_vault_secret_id = azurerm_key_vault_secret.phi_encryption_key.id
    identity            = azurerm_user_assigned_identity.app.id
  }

  template {
    min_replicas = var.worker_min_replicas
    max_replicas = var.worker_max_replicas

    container {
      name    = "worker"
      image   = var.container_image
      cpu     = var.container_cpu
      memory  = var.container_memory
      command = ["python", "-m", "worker"]

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "QUEUE_DATABASE_URL"
        secret_name = "queue-database-url"
      }
      env {
        name        = "PHI_ENCRYPTION_KEY"
        secret_name = "phi-encryption-key"
      }
    }
  }

  tags = var.tags

  depends_on = [azurerm_key_vault_access_policy.app]
}
