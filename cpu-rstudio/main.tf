# Define required providers
terraform {
required_version = ">= 0.14.0"
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "~> 1.48.0"
    }
  }
}

# Configure the OpenStack Provider
provider "openstack" {
  user_name   = var.user_name
  tenant_name = var.tenant_name
  password    = var.password
  auth_url    = var.auth_url
  domain_name = var.domain_name
}

# Define the security group
resource "openstack_networking_secgroup_v2" "secgroup" {
  name        = "terraform_secgroup"
  description = "Security Group managed by Terraform"
}

# Define the security group rule for SSH
resource "openstack_networking_secgroup_rule_v2" "secgroup_rule_ssh" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.secgroup.id
}

# Open a security group rule for R Shiny Server port 8787
resource "openstack_networking_secgroup_rule_v2" "secgroup_rule_shiny" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 8787
  port_range_max    = 8787
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.secgroup.id
}

# Create a cpu server
resource "openstack_compute_instance_v2" "cpu-server" {
  name      = "cloud-cpu"
  image_id  = "3fdc6cfa-f197-4dfd-a6b4-b0b9f7795b41" # Ubuntu 22.04 LTS with Docker
  flavor_id = "d64c82e2-9a19-41d1-a4fc-c555fda25921" # c3.medium
  key_pair  = "mewc-key"
  security_groups = [openstack_networking_secgroup_v2.secgroup.name]
  availability_zone = "tasmania-02"
}


output "instance_ip" {
  value = openstack_compute_instance_v2.cpu-server.access_ip_v4
}
