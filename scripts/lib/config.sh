#!/usr/bin/env bash
# versions.env 加载、架构解析与 BuildKit 参数组装。
#
# versions.env 是所有运行时坐标、下载地址和校验和的唯一来源。
# 架构相关值以 <NAME>_<ARCH> 形式存储，每次调用只解析为一个架构的 <NAME>，
# 解析规则由 SANDBOX_ARCH_VARS 列表驱动（列表本身也在 versions.env 中）。

SANDBOX_ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SANDBOX_VERSIONS_FILE="$SANDBOX_ROOT_DIR/versions.env"
SANDBOX_DOCKERFILE="$SANDBOX_ROOT_DIR/Dockerfile"

# versions.env 中不传给 Dockerfile 的变量名：构建矩阵、报告目标、溯源记录、
# Registry namespace，以及仅供镜像内 verifier 和来源合同读取的两个值。
# shellcheck disable=SC2034  # 由 source-contract.sh 读取
SANDBOX_NON_BUILD_ARG_NAMES=(
  SANDBOX_PLATFORMS SANDBOX_ARCH_VARS TARGET_IMAGE_SIZE_BYTES
  UBUNTU_RELEASE_TAG OMA_ARTIFACT_REGISTRY UNAME_MACHINE JAVA_ARCH
  GH_VERSION D2_VERSION TYPST_VERSION GH_URL GH_SHA256 D2_URL D2_SHA256
  TYPST_URL TYPST_SHA256 CHROMIUM_REVISION CHROMIUM_URL CHROMIUM_SHA256
  WHISPER_REVISION WHISPER_MODEL_SHA256 WHISPER_CONFIG_SHA256
  WHISPER_TOKENIZER_SHA256 WHISPER_VOCABULARY_SHA256 WHISPER_README_SHA256
)

# 安全关键的构建参数——不允许从 Dockerfile 中静默消失。
# 每一项要么固定身份（平台、镜像 digest），要么用于验证字节。
# shellcheck disable=SC2034  # 由 source-contract.sh 读取
SANDBOX_REQUIRED_BUILD_ARG_NAMES=(
  SANDBOX_PLATFORM UBUNTU_DIGEST CA_BUNDLE_SHA256 UBUNTU_SNAPSHOT_TIMESTAMP
  APT_RUNTIME_MIRROR
  ENVD_SHA256 PYTHON_SHA256 NODE_SHA256 GO_SHA256 JAVA_SHA256 PHP_SHA256
  BUN_SHA256 RUST_SHA256 RUBY_SHA256 CLAUDE_SHA256 CLAUDE_PATCHED_SHA256 UV_SHA256 YARN_SHA256
  PNPM_SHA256 MAVEN_SHA512 GRADLE_SHA256 COMPOSER_SHA256 DOCKER_CLI_SHA256
  ENVIRONMENT_MANAGER_IMAGE_DIGEST ENVIRONMENT_MANAGER_SHA256
  RCLONE_FILESTORE_IMAGE_DIGEST RCLONE_FILESTORE_SHA256
)

# 加载 versions.env（整个合同的唯一值来源）。
sandbox_load_versions() {
  [[ -r "$SANDBOX_VERSIONS_FILE" ]] \
    || sandbox_die "missing version contract: $SANDBOX_VERSIONS_FILE" \
    || return 1
  # shellcheck source=../../versions.env
  source "$SANDBOX_VERSIONS_FILE"
}

# 列出所有声明的架构，从 SANDBOX_PLATFORMS 推导（确保二者不可能不一致）。
sandbox_arch_list() {
  local platform
  for platform in $SANDBOX_PLATFORMS; do
    printf '%s\n' "${platform#linux/}"
  done
}

# 判断给定架构是否在构建矩阵中。
sandbox_is_known_arch() {
  local candidate="$1" arch
  while IFS= read -r arch; do
    [[ "$arch" == "$candidate" ]] && return 0
  done < <(sandbox_arch_list)
  return 1
}

# 为指定架构解析所有 SANDBOX_ARCH_VARS 条目（<NAME>_<ARCH> → <NAME>），
# 同时设置 SANDBOX_PLATFORM。解析完成后，调用方看到的都是不带后缀的普通名字，
# 与单架构时期完全一致。
sandbox_select_arch() {
  local arch="$1" name suffix source_name

  sandbox_is_known_arch "$arch" \
    || sandbox_die "unknown architecture: $arch (SANDBOX_PLATFORMS = $SANDBOX_PLATFORMS)" \
    || return 1

  suffix="$(sandbox_upper "$arch")"
  for name in $SANDBOX_ARCH_VARS; do
    source_name="${name}_${suffix}"
    [[ -n "${!source_name+set}" ]] \
      || sandbox_die "versions.env does not define $source_name" \
      || return 1
    printf -v "$name" '%s' "${!source_name}"
    export "${name?}"
  done

  SANDBOX_ARCH="$arch"
  SANDBOX_PLATFORM="linux/$arch"
  export SANDBOX_ARCH SANDBOX_PLATFORM

  # 组装两个外部 OCI 制品的不可变引用。
  # shellcheck disable=SC2034  # 由 build.sh 读取
  ENVIRONMENT_MANAGER_IMAGE_REF="${OMA_ARTIFACT_REGISTRY}/environment-manager-rs@${ENVIRONMENT_MANAGER_IMAGE_DIGEST}"
  # shellcheck disable=SC2034  # 由 build.sh 读取
  RCLONE_FILESTORE_IMAGE_REF="${OMA_ARTIFACT_REGISTRY}/rclone-filestore@${RCLONE_FILESTORE_IMAGE_DIGEST}"
}

# 从 Dockerfile 的 ARG 声明推导需要的构建参数名列表。
# 这取代了过去手工维护、容易与 Dockerfile 漂移的构建参数数组。
sandbox_build_arg_names() {
  awk 'toupper($1) == "ARG" {
    name = $2
    sub(/=.*/, "", name)
    if (name != "DEBIAN_FRONTEND" && !seen[name]++) print name
  }' "$SANDBOX_DOCKERFILE" | sort
}

# 为已选架构填充 SANDBOX_BUILD_ARGS 数组。
# 循环体在当前 shell 中运行，缺失值会立即终止调用方，
# 而非让 BuildKit 静默回退到空 ARG。
# 不使用 Bash 4 特性（如 nameref），以兼容 macOS 自带的 Bash 3.2。
SANDBOX_BUILD_ARGS=()

sandbox_build_args() {
  local name value
  SANDBOX_BUILD_ARGS=()
  while IFS= read -r name; do
    value="${!name:-}"
    [[ -n "$value" ]] \
      || sandbox_contract_fail "$name is unset or empty in versions.env for $SANDBOX_PLATFORM" \
      || return 1
    SANDBOX_BUILD_ARGS+=(--build-arg "$name=$value")
  done < <(sandbox_build_arg_names)
  [[ "${#SANDBOX_BUILD_ARGS[@]}" -gt 0 ]] \
    || sandbox_contract_fail "Dockerfile declares no build arguments"
}

# 打印已选架构的合同元数据。
sandbox_metadata() {
  printf '%s\n' \
    "arch=$SANDBOX_ARCH" \
    "platform=$SANDBOX_PLATFORM" \
    "target_image_size_bytes=$TARGET_IMAGE_SIZE_BYTES" \
    "ubuntu_release_tag=$UBUNTU_RELEASE_TAG" \
    "ubuntu_digest=$UBUNTU_DIGEST" \
    "ubuntu_snapshot_timestamp=$UBUNTU_SNAPSHOT_TIMESTAMP" \
    "apt_runtime_mirror=$APT_RUNTIME_MIRROR" \
    "runtime_revision=$RUNTIME_REVISION" \
    "codex_universal_recipe_revision=$CODEX_UNIVERSAL_RECIPE_REVISION" \
    "envd_version=$ENVD_VERSION" \
    "envd_sha256=$ENVD_SHA256" \
    "environment_manager_image_digest=$ENVIRONMENT_MANAGER_IMAGE_DIGEST" \
    "environment_manager_revision=$ENVIRONMENT_MANAGER_REVISION" \
    "environment_manager_version=$ENVIRONMENT_MANAGER_VERSION" \
    "environment_manager_sha256=$ENVIRONMENT_MANAGER_SHA256" \
    "rclone_filestore_image_digest=$RCLONE_FILESTORE_IMAGE_DIGEST" \
    "rclone_filestore_sha256=$RCLONE_FILESTORE_SHA256" \
    "python=$PYTHON_VERSION" \
    "node=$NODE_VERSION" \
    "go=$GO_VERSION" \
    "java=$JAVA_VERSION" \
    "php=$PHP_VERSION" \
    "gcc=$GCC_VERSION" \
    "bun=$BUN_VERSION" \
    "rust=$RUST_VERSION" \
    "ruby=$RUBY_VERSION" \
    "claude=$CLAUDE_VERSION" \
    "claude_patched_sha256=$CLAUDE_PATCHED_SHA256"
}

# 按架构生成子标签，格式为 <tag>-<arch>。
sandbox_arch_tag() {
  printf '%s-%s\n' "$1" "$2"
}
