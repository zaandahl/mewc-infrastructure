#!/bin/sh

# Generate a new SSH key pair
ssh-keygen -t rsa -b 2048 -f ./keys/mewc-key -N ""

# Create the key pair in OpenStack
openstack keypair create --public-key ./keys/mewc-key.pub mewc-key
