variable "project_name" {
  description = "Resource naming/tagging prefix."
  type        = string
}

variable "environment" {
  description = "Deployment environment, e.g. \"production\" or \"staging\"."
  type        = string
}

variable "region" {
  description = "Azure region, e.g. \"eastus\"."
  type        = string
}

variable "container_image" {
  description = "Fully-qualified ACR image reference to deploy (e.g. <registry>.azurecr.io/asc-recovery:<tag>)."
  type        = string
}

variable "db_backup_retention_days" {
  description = "Automated PostgreSQL Flexible Server backup retention period, in days."
  type        = number
  default     = 14
}

variable "vnet_cidr" {
  description = "CIDR block for the virtual network."
  type        = string
  default     = "10.1.0.0/16"
}

variable "db_sku_name" {
  description = "PostgreSQL Flexible Server SKU."
  type        = string
  default     = "GP_Standard_D2s_v3"
}

variable "db_storage_mb" {
  description = "PostgreSQL Flexible Server storage, in MB."
  type        = number
  default     = 65536
}

variable "container_cpu" {
  description = "Container App CPU cores."
  type        = number
  default     = 0.5
}

variable "container_memory" {
  description = "Container App memory, e.g. \"1Gi\"."
  type        = string
  default     = "1Gi"
}

variable "min_replicas" {
  type    = number
  default = 2
}

variable "max_replicas" {
  type    = number
  default = 4
}

variable "tags" {
  description = "Additional resource tags."
  type        = map(string)
  default     = {}
}
