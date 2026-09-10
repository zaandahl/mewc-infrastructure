# This legacy root must never operate on its old instance/volume state.
# Use python -m mewc_infra with a fresh external deployment configuration.
# Existing installations require a separately reviewed state/ownership migration.
terraform {
  required_version = "= 0.0.0" # Intentional hard stop, including terraform destroy.
}
