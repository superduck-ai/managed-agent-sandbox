set shell := ["bash", "-uc"]

# 单架构配方使用的默认架构。
arch := env_var_or_default("SANDBOX_ARCH", "amd64")
# 构建、测试、推送使用的镜像标签。
tag := env_var_or_default("SANDBOX_IMAGE_TAG", "ghcr.io/superduck-ai/managed-agent-sandbox:local")
# 按架构发布时使用的子标签（<tag>-<arch>）。
arch_tag := tag + "-" + arch

# 列出声明的和当前可构建的架构。
sandbox-image-architectures:
  @echo "declared:  $(./scripts/sandbox-image.sh architectures | tr '\n' ' ')"
  @echo "buildable: $(./scripts/sandbox-image.sh buildable-architectures | tr '\n' ' ')"

# 运行主机侧测试、全架构来源合同和 Dockerfile 检查。
sandbox-image-check:
  ./tests/run-tests.sh
  ./scripts/sandbox-image.sh verify-source --all
  for a in $(./scripts/sandbox-image.sh architectures); do \
    ./scripts/sandbox-image.sh --arch "$a" check; \
  done

# 仅运行主机侧测试。
sandbox-image-test-scripts:
  ./tests/run-tests.sh

# 打印指定架构的合同元数据。
sandbox-image-metadata:
  ./scripts/sandbox-image.sh --arch {{arch}} metadata

# 从固定的 Environment Manager 和 rclone-filestore OCI 输入构建单个架构。
sandbox-image-build:
  SANDBOX_IMAGE_TAG="{{tag}}" SANDBOX_IMAGE_OUTPUT=load \
    ./scripts/sandbox-image.sh --arch {{arch}} build

# 执行单个架构的平台、大小与运行时合同验证。
sandbox-image-test:
  ./scripts/sandbox-image.sh --arch {{arch}} test-image "{{tag}}"

# 构建并验证单个架构（端到端）。
sandbox-image-verify: sandbox-image-build sandbox-image-test

# 在通过运行时合同后推送本地镜像。
sandbox-image-push: sandbox-image-verify
  docker push "{{tag}}"

# 使用按架构的子标签构建单个架构（用于 OCI Index 组装）。
sandbox-image-build-arch:
  SANDBOX_IMAGE_TAG="{{arch_tag}}" SANDBOX_IMAGE_OUTPUT=load \
    ./scripts/sandbox-image.sh --arch {{arch}} build

# 对按架构的子标签执行运行时合同验证。
sandbox-image-test-arch:
  ./scripts/sandbox-image.sh --arch {{arch}} test-image "{{arch_tag}}"

# 构建并验证按架构的子标签（端到端）。
sandbox-image-verify-arch: sandbox-image-build-arch sandbox-image-test-arch

# 推送已验证的子标签（不重新构建）。
sandbox-image-push-arch: sandbox-image-verify-arch
  docker push "{{arch_tag}}"

# 从每个可构建架构的不可变 digest 引用组装并验证 OCI 多架构索引。
sandbox-image-publish-manifest +sources:
  ./scripts/sandbox-image.sh publish-manifest "{{tag}}" {{sources}}
