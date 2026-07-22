set shell := ["bash", "-uc"]

# Validate the pinned source contract and Dockerfile.
sandbox-image-check:
  ./scripts/sandbox-image.sh verify-source
  ./scripts/sandbox-image.sh check

# Build linux/amd64 from the pinned Environment Manager and rclone-filestore OCI inputs.
sandbox-image-build:
  ./scripts/sandbox-image.sh build

# Enforce platform, uncompressed size, and runtime contracts.
sandbox-image-test:
  ./scripts/sandbox-image.sh test-image "${SANDBOX_IMAGE_TAG:-ghcr.io/superduck-ai/managed-agent-sandbox:local}"
