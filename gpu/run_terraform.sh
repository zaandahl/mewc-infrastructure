#!/bin/bash

# Set Terraform variables
export TF_VAR_user_name=$OS_USERNAME
export TF_VAR_tenant_name=$OS_PROJECT_NAME
export TF_VAR_password=$OS_PASSWORD
export TF_VAR_auth_url=$OS_AUTH_URL
export TF_VAR_domain_name=$OS_USER_DOMAIN_NAME

# Verify variables are set
echo "TF_VAR_user_name is set to '$TF_VAR_user_name'"
echo "TF_VAR_tenant_name is set to '$TF_VAR_tenant_name'"
echo "TF_VAR_password is set to '*********'"
echo "TF_VAR_auth_url is set to '$TF_VAR_auth_url'"
echo "TF_VAR_domain_name is set to '$TF_VAR_domain_name'"

# Initialize Terraform
terraform init

list_resources() {
  terraform state list
}

destroy_resource() {
  instance_name="$1"
  volume_name="$2"
  
  # Detach the volume from the instance
  terraform destroy -target="openstack_compute_volume_attach_v2.va" -var="instance_name=${instance_name}" -var="volume_name=${volume_name}" -auto-approve
  # Destroy the instance
  terraform destroy -target="openstack_compute_instance_v2.gpu-server" -var="instance_name=${instance_name}" -var="volume_name=${volume_name}" -auto-approve
  # Destroy the volume
  terraform destroy -target="openstack_blockstorage_volume_v3.mewc_volume" -var="instance_name=${instance_name}" -var="volume_name=${volume_name}" -auto-approve
}

destroy_secgroup() {
  # Destroy the SSH security group rule
  terraform destroy -target="openstack_networking_secgroup_rule_v2.secgroup_rule_ssh" -auto-approve
  # Destroy the security group
  terraform destroy -target="openstack_networking_secgroup_v2.secgroup" -auto-approve
}

# Check the first command-line argument
if [ "$1" == "apply" ]; then
  # Apply Terraform changes
  terraform apply -var="instance_name=$2" -var="volume_name=$3" -var="instance_flavor=$4" -auto-approve
  if [ $? -eq 0 ]; then
    instance_ip=$(terraform output -raw instance_ip)
    private_key_path="/app/keys/mewc-key"
    echo "Waiting for SSH at $instance_ip to be available..."
    sleep 30
    while ! ssh -i $private_key_path -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o PasswordAuthentication=no ubuntu@$instance_ip true; do
      echo "Still waiting for SSH at $instance_ip to be available..."
      sleep 30
    done
    ansible-playbook -i "$(terraform output instance_ip)," setup_volume.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," setup_sftp.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," setup_powershell.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," install_docker.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," setup_docker.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," setup_nvidiadocker.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," setup_containers.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
  else
      echo "Terraform apply failed. Exiting."
      exit 1
  fi
elif [ "$1" == "list" ]; then
  list_resources
elif [ "$1" == "destroy" ]; then
  # Destroy Terraform resources
  #terraform destroy -auto-approve
  destroy_resource $2 $3
elif [ "$1" == "destroy_secgroup" ]; then
  destroy_secgroup
else
  # echo a message describing usage with options apply list and destroy and the required arguments
  echo "Usage: $0 apply [instance_name] [volume_name] ['gpu' or 'small']"
  echo "Usage: $0 destroy [instance_name] [volume_name]"
  echo "Usage: $0 [list|destroy_secgroup]"
fi
