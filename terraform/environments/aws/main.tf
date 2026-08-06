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

variable "environment" {
  description = <<-EOT
    Deployment environment name. Paired with a Terraform workspace of the
    same name (see docs/RUNBOOK.md's CI/CD section) so staging and
    production get separate state and separate provisioned resources from
    this one environment directory, instead of duplicating the whole
    directory per environment.
  EOT
  type        = string
  default     = "production"
}

# F-07 (docs/audit/REGISTER.md): without this, module.asc_recovery
# provisions an ALB with no listener at all -- the AWS deployment target
# is unreachable end to end, not just missing TLS. No default: an ALB
# with a listener pointed at a certificate that doesn't exist is worse
# than failing `terraform plan` with a clear "you must supply this"
# error. Issuing + validating the certificate itself needs a real
# Route53-hosted domain, which this build environment doesn't have --
# out of scope for Terraform authored here to provision; whoever runs
# this for real supplies an already-issued, already-validated ACM
# certificate ARN for their own domain.
variable "certificate_arn" {
  description = "ACM certificate ARN for the HTTPS listener, already issued and validated in this account/region."
  type        = string
}

module "asc_recovery" {
  source = "../../modules/aws"

  project_name    = "asc-recovery"
  environment     = var.environment
  region          = var.region
  container_image = var.container_image
}

# The module exposes the ALB and target group but can't provision the
# listener itself (see container_runtime.tf's comment) -- these two
# resources are what actually make the AWS deployment target reachable.
resource "aws_lb_listener" "https" {
  load_balancer_arn = module.asc_recovery.alb_arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = module.asc_recovery.target_group_arn
  }
}

# Plaintext HTTP is never served -- this listener's only job is the
# redirect, so port 80 never forwards a request to a target.
resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = module.asc_recovery.alb_arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

output "database_endpoint" {
  value = module.asc_recovery.database_endpoint
}

output "container_service_endpoint" {
  value = module.asc_recovery.container_service_endpoint
}
