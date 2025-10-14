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

# Check the first command-line argument
if [ "$1" == "apply" ]; then
  # Apply Terraform changes
  terraform apply -auto-approve
  if [ $? -eq 0 ]; then
    instance_ip=$(terraform output -raw instance_ip)
    private_key_path="/app/keys/mewc-key"
    echo "Waiting for SSH at $instance_ip to be available..."
    sleep 30
    while ! ssh -i $private_key_path -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o PasswordAuthentication=no ubuntu@$instance_ip true; do
      echo "Still waiting for SSH at $instance_ip to be available..."
      sleep 30
    done
#    ansible-playbook -i "$(terraform output instance_ip)," setup_sftp.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," ../playbooks/setup_volume.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
#    ansible-playbook -i "$(terraform output instance_ip)," ../playbooks/setup_powershell.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
#    ansible-playbook -i "$(terraform output instance_ip)," ../playbooks/install_docker.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," ../playbooks/setup_docker.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
#    ansible-playbook -i "$(terraform output instance_ip)," ../playbooks/setup_nvidiadocker.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
#    ansible-playbook -i "$(terraform output instance_ip)," ../playbooks/setup_containers.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
    ansible-playbook -i "$(terraform output instance_ip)," web_site.yml --ssh-extra-args='-o StrictHostKeyChecking=no'
  else
      echo "Terraform apply failed. Exiting."
      exit 1
  fi

elif [ "$1" == "destroy" ]; then
  # Destroy Terraform resources
  terraform destroy -auto-approve
else
  echo "Usage: $0 {apply|destroy}"
fi
