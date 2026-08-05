terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }

  # Real deployments use a remote backend (an Azure Storage Account
  # container with state locking) so state -- which contains sensitive
  # values, see terraform/README.md -- isn't a local file. Left
  # unconfigured here for the same reason as the AWS environment: which
  # backend to use is an operational decision for whoever runs this for
  # real, not something to hardcode into a module authored without a
  # real Azure subscription to provision that backend in yet.
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
  }
}

variable "region" {
  type    = string
  default = "eastus"
}

variable "container_image" {
  description = "Fully-qualified ACR image reference to deploy."
  type        = string
}

module "asc_recovery" {
  source = "../../modules/azure"

  project_name    = "asc-recovery"
  environment     = "production"
  region          = var.region
  container_image = var.container_image
}

output "database_endpoint" {
  value = module.asc_recovery.database_endpoint
}

output "container_service_endpoint" {
  value = module.asc_recovery.container_service_endpoint
}
