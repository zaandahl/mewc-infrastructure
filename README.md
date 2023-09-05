# mewc-infrastructure
Docker container with Terraform and OpenStack for setting up Nectar Cloud infrastructure


## Building and Launching the Docker Container

1. Build the Docker image by running the following command in the terminal:
```
docker-compose build
```

2. Start the container with the following command:
```
docker-compose up -d
```

3. You can then access the container's shell with:
```
docker-compose exec mewc_infra_setup bash
```

## Generating OpenStack Key Pair

We have included a script to help you generate a new key pair:

1. Run the `create_keypair.sh` script inside the Docker container:
```
./create_keypair.sh
```

2. This will create a new key pair and register it with OpenStack. The private key will be saved in the `keys/` directory.

## Listing Available Images and Flavors in OpenStack

You can list available images and flavors using the OpenStack CLI:

- To list images, use the following command:
```
openstack image list
```

- To list flavors, use:
```
openstack flavor list
```

## Configuring and Running Terraform

1. Modify `main.tf` to use the appropriate `image_id` and `flavor_id` based on your needs. You can use the `openstack image list` and `openstack flavor list` commands to find the IDs of available images and flavors.

2. Run the `run_terraform.sh` script to create resources:
```
./run_terraform.sh apply
```

3. After verifying that the resources were created successfully, you can destroy them with:
```
./run_terraform.sh destroy
```

## Environment Variables and Their Usage

We use environment variables to store OpenStack credentials and pass them to the Docker container. These environment variables are set in the `nectar.env` file, which is loaded when the Docker container is started.

- `nectar.env` should be set up with your OpenStack credentials. It is listed in the `.gitignore` file to prevent accidental uploading of sensitive information.

- The Docker Compose configuration file, `docker-compose.yaml`, specifies that the environment variables should be taken from the `nectar.env` file.

- These environment variables are then accessible to any processes running inside the Docker container, including the OpenStack and Terraform commands.

- They are used by the OpenStack CLI to authenticate with the OpenStack API, and by Terraform to authenticate with the OpenStack provider.

Remember, always keep your `nectar.env` file secure and do not share it with anyone. Also, always remember to add any new files containing sensitive information to your `.gitignore` file.


## Setting Up OpenStack Credentials

Before you can use the OpenStack client with Terraform, you'll need to set up your OpenStack credentials. These credentials are different from the login you use for the Nectar Dashboard.

Follow these steps to setup your OpenStack credentials:

1. Log on to the [Nectar Dashboard](https://dashboard.rc.nectar.org.au) and ensure you're working in the right project (Use the project selector on the top left-hand side).

2. Click your email address from the top right corner and click `OpenStack RC File` to download the authentication file.

3. Save the authentication file to your computer. This file contains all the settings required for authentication, except for your password.

4. Click `Settings` in the same drop-down menu to get to the `Settings` page. Then click `Reset Password` to generate a new OpenStack password. This password is used only when working with the CLIs and APIs. This password does not replace the password you use to log into the Dashboard.

You can read more about these steps in the [Nectar Tutorial on OpenStack Credentials](https://tutorials.rc.nectar.org.au/openstack-cli/04-credentials).

After you have your OpenStack credentials, create an `nectar.env` file in the root directory of this project and set your OpenStack environment variables there. It should look something like this:

```env
OS_AUTH_URL=http://your-openstack-url:5000/v3
OS_USERNAME=your-username
OS_PASSWORD=your-password
# ... other variables ...
```

Replace the placeholders with your actual OpenStack credentials. 

**Important:** The `nectar.env` file contains sensitive information and should not be included in the git repository. Be sure to add it to your `.gitignore` file to prevent accidentally pushing it:

```git
echo "nectar.env" >> .gitignore
```

This project's `compose.yaml` file is configured to load the environment variables from `nectar.env` when starting the Docker container.

## Connecting to Your Instance

Once your instance is up and running, you can connect to it using SSH. Terraform has created an instance using a key pair for SSH access.

### SSH Access

To connect to your instance via SSH, use the `ssh` command followed by the username (for Ubuntu instances, this is usually `ubuntu`) and the IP address of the instance:

```bash
ssh ubuntu@<your-instance-ip-address>
```

Make sure to replace `<your-instance-ip-address>` with the actual IP address of your instance.

If you get a permissions error, your private key file (`mewc-key.pem`) may not be properly secured. Ensure that it has the correct permissions by running:

```bash
chmod 600 mewc-key.pem
```

You also need to specify the path to your private key file using the `-i` option:

```bash
ssh -i path/to/mewc-key.pem ubuntu@<your-instance-ip-address>
```

Replace `path/to/mewc-key.pem` with the actual path to your private key file.

### Managing Your SSH Keys

The private key (`mewc-key.pem`) should be kept secure - treat it like a password. If someone else gains access to this file, they could access your instances.

Also, remember to add the public key (`mewc-key.pem.pub`) to any new instances you create if you want to be able to connect to them with the associated private key.

## Access volume and object storage using CyberDuck
Download the CyberDuck client from https://cyberduck.io/download/

To upload to volumne storage you just need to connect to the instance. You can find the IP address of the instance in the Nectar dashboard.
Use SFTP to connect with a username of ubuntu and the private key you generated before.

For object storage (S3) follow instructions on the Nectar page https://tutorials.rc.nectar.org.au/object-storage/04-object-storage-cyberduck

They key point is to download the Nectar CyberDuck profile and enter your OpenStack credentials that you created before in your nectar.env file.

Your credentials will look something like this: projectname:Default:your.username@domain.com
Project name is from OS_PROJECT_NAME, Default is from OS_USER_DOMAIN_NAME
You enter your OS_PASSWORD as the password.

## Creating a sftp user with access to a specific directory

You can use mkpasswd to create a password for the user:

```bash
mkpasswd -m sha-512
```
