# Use an official Python runtime as a parent image
FROM python:3.11.4-slim-bookworm

# Set environment variables
ENV TERRAFORM_VERSION=0.15.3
ENV ANSIBLE_VERSION=4.1.0

# Install necessary packages
RUN apt-get update && \
    apt-get install -y curl unzip gcc libffi-dev libssl-dev openssh-client && \
    rm -rf /var/lib/apt/lists/*

# Install Terraform
RUN curl -O https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip && \
    unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip -d /usr/local/bin/ && \
    rm terraform_${TERRAFORM_VERSION}_linux_amd64.zip

# Install Ansible
RUN pip install ansible==${ANSIBLE_VERSION}

# Install OpenStack clients
RUN pip install python-openstackclient python-glanceclient python-ironicclient python-manilaclient python-novaclient python-neutronclient python-swiftclient

# Check versions
RUN terraform -v && ansible --version && openstack --version

# Set the default command
CMD ["tail", "-f", "/dev/null"]
