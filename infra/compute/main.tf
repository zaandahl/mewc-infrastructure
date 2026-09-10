# Fresh deployment root only. Never point this at legacy profile state.
terraform {
  required_version = ">= 1.16.0, < 1.17.0"
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "= 3.4.0"
    }
  }
  backend "local" {}
}

# Authentication comes exclusively from the operator's OpenStack environment.
provider "openstack" {
  tenant_id = var.project_id
  region    = var.region
}

resource "openstack_networking_secgroup_v2" "ssh" {
  name        = "${var.deployment_id}-ssh"
  description = "Private MEWC administration; application ports are not public"
}

resource "openstack_networking_secgroup_rule_v2" "ssh" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = var.ssh_cidr
  security_group_id = openstack_networking_secgroup_v2.ssh.id
}

resource "openstack_compute_instance_v2" "worker" {
  name              = var.deployment_id
  image_id          = var.image_id
  flavor_id         = var.flavor_id
  key_pair          = var.keypair_name
  availability_zone = var.availability_zone
  security_groups   = [openstack_networking_secgroup_v2.ssh.name]
  metadata = {
    mewc_deployment = var.deployment_id
  }
  network {
    uuid = var.network_id
  }
}

# External data volume: compute owns ONLY the attachment, never the volume.
resource "openstack_compute_volume_attach_v2" "data" {
  instance_id = openstack_compute_instance_v2.worker.id
  volume_id   = var.volume_id
}

output "instance_ip" {
  value = openstack_compute_instance_v2.worker.network[0].fixed_ip_v4
}

output "instance_id" {
  value = openstack_compute_instance_v2.worker.id
}

output "retained_volume_id" {
  value = var.volume_id
}
