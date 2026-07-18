set shell := ["bash", "-uc"]

# Validate the pinned source contract and Dockerfile.
sandbox-image-check:
  ./scripts/sandbox-image.sh verify-source
  ./scripts/sandbox-image.sh check

# Build the linux/amd64 image. ENVIRONMENT_MANAGER_BINARY must match versions.env.
sandbox-image-build:
  ./scripts/sandbox-image.sh build

# Enforce platform, uncompressed size, and runtime contracts.
sandbox-image-test:
  ./scripts/sandbox-image.sh test-image "${SANDBOX_IMAGE_TAG:-ghcr.io/superduck-ai/managed-agent-sandbox:local}"
