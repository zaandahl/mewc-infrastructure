variable "deployment_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,47}$", var.deployment_id))
    error_message = "Use a unique deployment name."
  }
}
variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "availability_zone" {
  type     = string
  nullable = true
}
variable "image_id" {
  type = string
}
variable "flavor_id" {
  type = string
}
variable "network_id" {
  type = string
}
variable "keypair_name" {
  type = string
}
variable "volume_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.volume_id))
    error_message = "An existing external Cinder volume UUID is required."
  }
}
variable "ssh_cidr" {
  type = string
  validation {
    condition     = can(cidrnetmask(var.ssh_cidr)) && !endswith(var.ssh_cidr, "/0")
    error_message = "Use a restricted IPv4 SSH source CIDR."
  }
}
