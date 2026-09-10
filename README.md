<img src="mewc_logo_hex.png" alt="MEWC Hex Sticker" width="160" align="right"/>

# MEWC infrastructure

Create a private Nectar compute instance with Terraform and configure it with
Ansible. Research data lives on an explicitly selected, independently retained
Cinder volume. The optional web interface runs **detection only** through an SSH
tunnel; detector categories are not species identifications.

This revised path targets **new Ubuntu 24.04 x86-64 instances**. Existing
installations require the [migration review](docs/migration.md) before changes.
The old Terraform roots are deliberately disabled.

| Profile | Scope |
| --- | --- |
| `cpu` | Verified volume and Docker/containerd host. |
| `gpu` | Same host plus NVIDIA runtime integration using an existing working image driver and an explicit active Nectar reservation. |
| `cpu-web` | CPU host plus private, single-operator detector upload, execution and downloads. |
| RStudio / public web / full five-stage MEWC pipeline | Outside this change's supported scope. |

See [validation evidence and limits](docs/validation.md),
[review responses](docs/audit-response.md), and [dependency policy](docs/toolchain.md).

## Start a new deployment

You need a Nectar project, an existing data volume in the intended storage zone,
a compatible image, network, flavor and an SSH keypair. For GPU use, select the
exact active lease, reservation and reserved flavor in Nectar; do not select the
first available flavor. GPU placement is normally left to the reservation
scheduler (`availability_zone: null`). Storage placement remains explicit.

1. Clone this repository and create an operator directory outside it:

   ```sh
   export MEWC_OPERATOR_DIR="$HOME/.local/share/mewc-operator"
   install -d -m 700 "$MEWC_OPERATOR_DIR"
   cp nectar.env.example nectar.env
   chmod 600 nectar.env
   # On the host, generate a new key only if this path is unused:
   test ! -e "$MEWC_OPERATOR_DIR/ssh-key" && \
     test ! -e "$MEWC_OPERATOR_DIR/ssh-key.pub" && \
     ssh-keygen -t ed25519 -f "$MEWC_OPERATOR_DIR/ssh-key" -N ''
   touch "$MEWC_OPERATOR_DIR/known_hosts"
   chmod 600 "$MEWC_OPERATOR_DIR/known_hosts"
   ```

   Fill `nectar.env` with your Nectar OpenStack credentials, using the HTTPS
   endpoint supplied by Nectar. Keep credentials out of deployment JSON, Git,
   logs and shared plans. Generate the key on the host so your host account can
   use it for the browser tunnel; alternatively use an existing operator-owned
   keypair and keep its public key beside it. Do not overwrite an existing key.
   See the official
   [OpenStack credentials tutorial](https://tutorials.rc.nectar.org.au/openstack-cli/04-credentials).

2. Build and enter the Linux amd64 controller:

   ```sh
   docker compose build
   docker compose run --rm mewc_infra_setup bash
   ```

   Inside the container, use the same absolute operator directory path. The
   checkout is mounted read-only; state and keys are written only in the operator
   directory. The container runs as root: newly created state files are accessed
   through the controller container. Register/check the host-generated keypair:

   ```sh
   ./create_keypair.sh YOUR_KEYPAIR /absolute/operator/path/ssh-key
   ```

3. Copy [the CPU example](docs/examples/cpu.json) or
   [the GPU example](docs/examples/gpu.json) into the operator directory. Replace
   **every example identifier and path**. Use a unique `deployment_id` and a
   `state_dir` whose final component is that same name. Restrict `ssh_cidr` to
   your actual operator egress address/range. The complete configuration is frozen
   when first used; changing it requires a reviewed migration or a new deployment.

   `volume_id` must name an existing, available volume belonging to this project
   in `volume_availability_zone`. The controller never creates or owns that
   volume. For a **new blank volume only**, add `format_volume_id` with the exact
   same UUID to authorise initial formatting. Omit it when retaining a filesystem.
   Never substitute `/dev/vdb` for volume identity.

4. Validate selection, save a plan, review its resources, then apply:

   ```sh
   python -m mewc_infra preflight --config /absolute/operator/path/deployment.json
   python -m mewc_infra plan --config /absolute/operator/path/deployment.json
   python -m mewc_infra apply --config /absolute/operator/path/deployment.json
   ```

   A new plan creates one instance, its SSH security group/rule and an attachment
   to the selected volume. It must not create or delete a data volume. Errors stop
   execution and retain resources for diagnosis. Plans are private artifacts;
   applying requires the saved plan and unchanged configuration/Terraform source.

5. In a separate **host terminal**, obtain the instance IP from Nectar. Verify
   its SSH host key using the authenticated cloud console or another trusted
   channel, and add the verified key to the host-owned `known_hosts_file`.
   `ssh-keyscan` alone is not verification. Back in the controller container, run:

   ```sh
   python -m mewc_infra configure --config /absolute/operator/path/deployment.json
   ```

   This waits a bounded time for verified SSH and cloud-init, checks the exact
   cloud attachment, mounts by filesystem UUID, and configures runtime storage.
   A successful configure prints `Host configuration completed.` Repeating the
   command should converge without disrupting unchanged runtime configuration.
   Host details and recovery rules are in [playbooks/README.md](playbooks/README.md).

## Private detector interface

Select `cpu-web` and supply an immutable `detector_image` reference in the JSON.
The tested image digest and worker limits are documented in the
[web role guide](cpu-mewc-web/roles/mewc_web/README.md). For GPU detection, configure
profile `gpu`, then apply that role with `detect_gpus: "all"` and the same verified
inventory, storage identity and pinned detector. GPU web deployment is an explicit
operator composition, not a fourth controller profile.

Leave the controller container (`exit`) and run this command **on your host**.
The browser and SSH tunnel must run on the same machine; a tunnel inside the
controller container is not reachable through the host's localhost.

```sh
ssh -i /absolute/operator/path/ssh-key \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/absolute/operator/path/known_hosts \
  -L 8080:127.0.0.1:8080 ubuntu@INSTANCE_IP
```

Open `http://localhost:8080`. Upload JPEG/PNG files or a ZIP, start the job, and
inspect its processed/failed counts before downloading results. Each retry has a
fresh attempt and provenance. An empty detection list is valid; missing/failed
images are not silently counted as processed. Archive completed jobs before the
volume fills; there is no automatic retention deletion.

## Retain data and stop compute

Re-enter the controller with `docker compose run --rm mewc_infra_setup bash`
from the checkout on your host, then run:

```sh
python -m mewc_infra destroy-plan --config /absolute/operator/path/deployment.json
# Review the saved compute-only teardown plan.
python -m mewc_infra destroy --config /absolute/operator/path/deployment.json
```

The Cinder data volume remains available after compute teardown. Keep the private
state/configuration and an independently verified export. Deleting a volume is a
separate operator action after a retention/recovery decision; this controller has
no permanent-data-deletion command. For recovery, including retained-volume
reattachment and missing mounts, see [migration.md](docs/migration.md).

## Local checks

```sh
uv venv --python 3.12
uv pip sync --python .venv/bin/python requirements-dev.txt
.venv/bin/ansible-galaxy collection install -r ansible-requirements.yml
.venv/bin/python -m pytest -q
```

Credential-free CI checks contracts, shell/Python/Ansible syntax, dependency
advisories, the controller build and Terraform schema. Real cloud and detector
checks are separate evidence; tests do not establish ecological accuracy.
