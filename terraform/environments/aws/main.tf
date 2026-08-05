terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Real deployments use a remote backend (S3 + DynamoDB lock table, or
  # Terraform Cloud) so state -- which contains sensitive values, see
  # terraform/README.md -- isn't a local file. Left unconfigured here
  # deliberately: which backend/bucket to use is an operational decision
  # for whoever runs this for real, not something to hardcode into a
  # module authored without a real AWS account to provision that bucket
  # in yet.
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "container_image" {
  description = "Fully-qualified ECR image reference to deploy."
  type        = string
}

module "asc_recovery" {
  source = "../../modules/aws"

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
