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
  name        = "cpu-mewc-web-sg"
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

# Define the security group rule for HTTP
resource "openstack_networking_secgroup_rule_v2" "http" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 80
  port_range_max    = 80
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.secgroup.id
}

# Create a cpu server for mewc-web
resource "openstack_compute_instance_v2" "cpu-mewc-web" {
  name      = "mewc-web"
  image_id  = "3e900014-17dc-4f5c-8b01-7890473ff404" # Ubuntu 22.04 LTS with Docker
  flavor_id = "f08d61e5-edb2-4a0d-ba14-d1526741d346" # m3.xxlarge 32 cpu - public
  key_pair  = "mewc-key"
  security_groups = [openstack_networking_secgroup_v2.secgroup.name]
  # Use your tenant network by UUID
  network {
    uuid = "00ad2ec0-343c-48ff-af3e-1f63bdf86e87" # tas-02
  }
  availability_zone = "tasmania-02"
}

# Cinder volume for jobs/results
resource "openstack_blockstorage_volume_v3" "mewc_data" {
  name = "cpu-mewc-web-data"
  size = 200  # bump later if needed, size in GB
  availability_zone = "tasmania-02"
}

# Attach to the instance (virtio -> /dev/vdb on Ubuntu)
resource "openstack_compute_volume_attach_v2" "attach_data" {
  instance_id = openstack_compute_instance_v2.cpu-mewc-web.id
  volume_id   = openstack_blockstorage_volume_v3.mewc_data.id
  device      = "/dev/vdb"
}

output "instance_ip" {
  value = openstack_compute_instance_v2.cpu-mewc-web.access_ip_v4
}

# --- DNS apex A -> instance public IP (no floating IP) ---

# Look up your DNS zone (Designate)
data "openstack_dns_zone_v2" "mewcgpu" {
  name = "mewcgpu.cloud.edu.au."  # trailing dot
}

# Create/maintain the apex A record pointing to the instance's public IP
resource "openstack_dns_recordset_v2" "apex_a" {
  zone_id = data.openstack_dns_zone_v2.mewcgpu.id
  name    = data.openstack_dns_zone_v2.mewcgpu.name  # apex == zone name
  type    = "A"
  ttl     = 300
  records = [openstack_compute_instance_v2.cpu-mewc-web.access_ip_v4]

  depends_on = [openstack_compute_instance_v2.cpu-mewc-web]
}

output "site_url" {
  value = "http://${data.openstack_dns_zone_v2.mewcgpu.name}"
}
