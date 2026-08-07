variable "project_name" {
  description = "Resource naming/tagging prefix."
  type        = string
}

variable "environment" {
  description = "Deployment environment, e.g. \"production\" or \"staging\"."
  type        = string
}

variable "region" {
  description = "AWS region, e.g. \"us-east-1\"."
  type        = string
}

variable "container_image" {
  description = "Fully-qualified ECR image reference to deploy (e.g. <account>.dkr.ecr.<region>.amazonaws.com/asc-recovery:<tag>)."
  type        = string
}

variable "db_backup_retention_days" {
  description = "Automated RDS backup retention period, in days. HIPAA documentation retention is 6 years for records, but daily operational backups don't need to match that -- this controls point-in-time recovery window, not long-term archival."
  type        = number
  default     = 14
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  description = "RDS allocated storage, in GB."
  type        = number
  default     = 50
}

variable "container_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 512
}

variable "container_memory" {
  description = "Fargate task memory, in MB."
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Number of running Fargate tasks."
  type        = number
  default     = 2
}

variable "worker_desired_count" {
  description = "Number of running Fargate job-queue worker tasks (Phase 7). Scale this, not desired_count, to drain the jobs table faster -- see docs/RUNBOOK.md."
  type        = number
  default     = 1
}

variable "tags" {
  description = "Additional resource tags."
  type        = map(string)
  default     = {}
}
