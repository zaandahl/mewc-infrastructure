variable "user_name" {}
variable "tenant_name" {}
variable "password" {}
variable "auth_url" {}
variable "domain_name" {}

variable "instance_name" {
  description = "Name of the instance."
  type        = string
  default     = "mewc-cloud-gpu"
}

variable "instance_flavor" {
  description = "The flavor of the instance (small/gpu)"
  default     = "small"
}

variable "volume_name" {
  description = "Name of the volume."
  type        = string
  default     = "mewc-volume"
}