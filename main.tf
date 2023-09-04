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

resource "openstack_blockstorage_volume_v3" "mewc_volume" {
  name = "mewc-volume"
  size = 500  # Size in GB
  description = "My Terraform-managed volume"
  availability_zone = "tasmania-02"
}

# Create a web server
resource "openstack_compute_instance_v2" "test-server" {
  name      = "mewc-cloud-test"
  image_id  = "3fdc6cfa-f197-4dfd-a6b4-b0b9f7795b41" # Ubuntu 22.04 LTS with Docker
  flavor_id = "d692a518-6939-465e-a4b9-58a388f468d3" # c3.small
  key_pair  = "mewc-key"
  security_groups = [openstack_networking_secgroup_v2.secgroup.name]
  availability_zone = "tasmania-02"
}

resource "openstack_compute_volume_attach_v2" "va" {
  instance_id = openstack_compute_instance_v2.test-server.id
  volume_id   = openstack_blockstorage_volume_v3.mewc_volume.id
}

# Create a GPU server
# resource "openstack_compute_instance_v2" "gpu-server" {
#   name      = "mewc-cloud-gpu"
#   image_id  = "0dfdea2d-5f10-4117-8dd0-186b1bc99df2" # Ubuntu 22.04 LTS (Jammy) amd64 with GPU
#   flavor_id = "d692a518-6939-465e-a4b9-58a388f468d3" # c3.small (need to update this after reservation)
#   key_pair  = "mewc-key"
#   security_groups = [openstack_networking_secgroup_v2.secgroup.name]
#   availability_zone = "tasmania-02"
# }

output "instance_ip" {
  value = openstack_compute_instance_v2.test-server.access_ip_v4
}
