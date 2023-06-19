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
  terraform apply
elif [ "$1" == "destroy" ]; then
  # Destroy Terraform resources
  terraform destroy
else
  echo "Usage: $0 {apply|destroy}"
fi

