# Existing deployments and recovery

The new controller is for new deployments. Legacy profile roots remain as
historical context with an unsatisfiable Terraform version requirement; their
wrappers forward explicit new configurations or refuse unsupported operations.
Do not bypass that guard and run `destroy` against old state: old roots own data
volumes. No existing server, account, volume or state is migrated automatically.

## Review an existing installation

Record the project, server and volume UUIDs, attachment, filesystem UUID, runtime
roots, active workloads and private state location. Keep a recoverable copy of
state and an independently checked data export. Reconcile those records through
the authenticated cloud API before making changes. State and logs can contain
operational identifiers; store them privately with restricted permissions.

The repository removes reusable password setup and its tracked state backup.
That does not revoke passwords on already deployed hosts or erase Git history
and forks. Existing credential changes, exposure investigation and any history
rewrite need a separate coordinated operational decision. Preserve a tested
administrator key/recovery route during account remediation.

Existing Docker/containerd stores must be deliberately migrated during downtime.
Follow [host migration requirements](../playbooks/README.md#existing-runtime-data):
stop workload services and socket activation, stop both runtimes, preserve a
backup and copy all ownership/ACL/xattr/hardlink/sparse-file metadata. Check the
new mounted filesystem identity and effective runtime roots before restart.
Keep the old copy until recovery and retained workloads have been checked.
Provisioning refuses nonempty legacy stores rather than moving them implicitly.

Legacy web job directories are archival inputs only. They are not imported into
new attempts because old success markers lack the new completeness and provenance
contract. Review/export those results separately.

## Recover a failed new deployment

A command failure retains resources and state. Inspect the named phase and
private Terraform state, then query the exact server and attachment. Do not
change the deployment name/configuration to hide a failure. Re-plan after a source
change or stale plan, review it, and reapply. If configure fails, resolve the
reported host condition and repeat configure with the same registered identity.
For detailed Ansible diagnosis, use the generated private `ansible-vars.json` and
verified inventory with the named playbook; keep full logs private. The controller
suppresses subprocess output that could include credentials.

If SSH host identity changes, verify the new key through the authenticated cloud
console before updating the known-hosts entry. Do not disable host verification.
If SSH/cloud-init times out, inspect the exact server console, network, security
group and image rather than repeatedly creating replacement instances.

A missing data volume allows administrator SSH recovery, but storage guards and
mount dependencies prevent Docker, containerd and the web services from writing
to root-disk substitutes. Reattach only the recorded volume, verify cloud and
filesystem identity, mount it, then explicitly restart the required services.
Never format a disk to resolve an identity mismatch.

## Recreate compute around retained storage

After a reviewed `destroy-plan`/`destroy`, confirm the exact volume still exists
and has detached. The same deployment can be planned and applied again using its
original immutable configuration; the compute state now contains no resources.
The original GPU lease/reservation must still be active and all referenced
resources available. Otherwise register a new reviewed deployment identity.
Verify the replacement host key before configuring. With a retained filesystem,
the storage probe mounts it without formatting, even if the original configuration
contained initial-format authority. Verify a saved sentinel/hash, data inventory,
Docker/containerd roots and application results before resuming work.

Alternatively, register a new deployment identity against the now-available
volume with no `format_volume_id`. This is not an automatic state import. Never
attach one research filesystem read-write to concurrent independent hosts.
Permanent volume deletion occurs only after a separate explicit retention decision
and verified recovery/export; no wrapper performs it.
