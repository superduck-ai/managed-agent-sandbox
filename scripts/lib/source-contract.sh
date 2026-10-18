#!/usr/bin/env bash
# 静态来源合同：在构建任何镜像之前运行的来源验证。
#
# 分为三层：
#   * sandbox_verify_shared_source   - 对所有架构都成立的事实
#   * sandbox_verify_arch_source     - 每个架构检查一次的事实
#   * sandbox_verify_source          - 在整个构建矩阵上执行以上两层

# ── 基础值格式校验 ──────────────────────────────────────────────

# 校验值是否为合法的 SHA-256（64 位十六进制小写）。
sandbox_verify_sha256_value() {
  local name="$1" value="${!1:-}"

  [[ "$value" =~ ^[0-9a-f]{64}$ ]] || sandbox_contract_fail "$name is not a SHA-256"
}

# 校验值是否为合法的 OCI SHA-256 digest（sha256:前缀 + 64 位十六进制）。
sandbox_verify_oci_image_digest() {
  local name="$1" value="${!1:-}"

  [[ "$value" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || sandbox_contract_fail "$name is not an OCI SHA-256 digest"
}

# 校验 Dockerfile 中每个 curl 下载都使用了完整的重试策略。
sandbox_verify_curl_retry_policy() {
  local file="$1" curl_count retry_count

  curl_count="$(grep -Ec '(^[[:space:]]*|RUN[[:space:]]+|&&[[:space:]]+)curl[[:space:]]+-' "$file" || true)"
  retry_count="$(grep -Ec 'curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15([[:space:]]|$)' "$file" || true)"
  [[ "$retry_count" == "$curl_count" ]] \
    || sandbox_contract_fail "$file has $curl_count curl downloads but $retry_count complete retry policies"
}

# 校验 CI workflow 中所有第三方 Actions 都使用 commit SHA 固定。
sandbox_verify_workflow_action_pins() {
  local file="$1" action_count pinned_action_count

  action_count="$(grep -Ec '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+[^.]' "$file" || true)"
  pinned_action_count="$(grep -Ec '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+[^[:space:]@]+@[0-9a-f]{40}([[:space:]]+#.*)?$' "$file" || true)"
  [[ "$action_count" -gt 0 && "$pinned_action_count" == "$action_count" ]] \
    || sandbox_contract_fail "$file has $action_count third-party Actions but $pinned_action_count immutable commit pins"
}

# ── 版本单一来源合同 ──────────────────────────────────────────

# 确保 Dockerfile 和 versions.env 之间的构建参数是完整且一致的：
#   1. 每个 Dockerfile ARG 都由 versions.env 提供值，不允许内联默认值
#   2. 安全关键 ARG 必须始终存在于 Dockerfile 中
#   3. versions.env 中的每个键都被某个 ARG 消费，不允许孤立键
#   4. 架构相关变量不允许同时存在无后缀的扁平版本
sandbox_verify_build_arg_wiring() {
  local dockerfile="$SANDBOX_DOCKERFILE"
  local name required_name key stored_name arch suffix

  # 拒绝 Dockerfile 中带内联默认值的 ARG（DEBIAN_FRONTEND 除外）。
  while IFS= read -r name; do
    if grep -Eq "^ARG ${name}=" "$dockerfile"; then
      sandbox_contract_fail "Dockerfile duplicates $name instead of using versions.env" || return 1
    fi
  done < <(sandbox_build_arg_names)

  # 安全关键 ARG 不允许从 Dockerfile 中消失。
  for required_name in "${SANDBOX_REQUIRED_BUILD_ARG_NAMES[@]}"; do
    if ! grep -Eq "^ARG ${required_name}([[:space:]]|$)" "$dockerfile"; then
      sandbox_contract_fail "Dockerfile no longer declares required ARG $required_name" || return 1
    fi
  done

  # 检查 versions.env 中的每个键都被消费（要么是 Dockerfile ARG，要么是已知的非构建参数）。
  local consumed_names non_build_names
  consumed_names=" $(sandbox_build_arg_names | tr '\n' ' ')"
  non_build_names=" ${SANDBOX_NON_BUILD_ARG_NAMES[*]} "

  while IFS= read -r key; do
    if [[ "$consumed_names" != *" $key "* && "$non_build_names" != *" $key "* ]]; then
      sandbox_contract_fail "versions.env defines $key, which no Dockerfile ARG consumes" || return 1
    fi
  done < <(sandbox_versions_resolved_keys)

  # 架构相关变量不允许同时存在扁平键——扁平值会遮盖已解析的架构值。
  for name in $SANDBOX_ARCH_VARS; do
    if grep -Eq "^${name}=" "$SANDBOX_VERSIONS_FILE"; then
      sandbox_contract_fail "versions.env defines flat $name alongside its per-architecture entries" || return 1
    fi
    for arch in $(sandbox_arch_list); do
      suffix="$(sandbox_upper "$arch")"
      stored_name="${name}_${suffix}"
      if ! grep -Eq "^${stored_name}=" "$SANDBOX_VERSIONS_FILE"; then
        sandbox_contract_fail "versions.env is missing $stored_name" || return 1
      fi
    done
  done
  sandbox_contract_status
}

# 提取 versions.env 中所有赋值的键名，将架构后缀去掉以便与 Dockerfile ARG 比较。
sandbox_versions_resolved_keys() {
  local suffixes="" arch
  for arch in $(sandbox_arch_list); do
    suffixes="$suffixes $(sandbox_upper "$arch")"
  done
  awk -v suffixes="$suffixes" '
    /^[A-Za-z_][A-Za-z0-9_]*=/ {
      key = $0
      sub(/=.*/, "", key)
      n = split(suffixes, list, " ")
      for (i = 1; i <= n; i++) {
        if (list[i] != "" && key ~ ("_" list[i] "$")) {
          sub("_" list[i] "$", "", key)
          break
        }
      }
      if (!seen[key]++) print key
    }
  ' "$SANDBOX_VERSIONS_FILE"
}

# ── Dockerfile 和 verifier 来源合同 ──────────────────────────

# 校验 Dockerfile、verifier、profile 中的关键字面量——确保固定来源的引用方式正确。
sandbox_verify_pinned_sources() {
  local dockerfile="$SANDBOX_DOCKERFILE"
  local verifier="$SANDBOX_ROOT_DIR/image/verify-contract.sh"
  local profile="$SANDBOX_ROOT_DIR/image/config/profile.sh"

  sandbox_require_literal "$dockerfile" 'COPY versions.env /etc/oma-sandbox-versions.env'
  sandbox_require_literal "$dockerfile" 'ubuntu@${UBUNTU_DIGEST} AS base'
  sandbox_require_literal "$dockerfile" 'ADD --checksum=${CA_BUNDLE_SHA256}'
  sandbox_require_literal "$dockerfile" '${CA_BUNDLE_URL} /etc/ssl/certs/ca-certificates.crt'
  sandbox_require_literal "$dockerfile" 'https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT_TIMESTAMP}/'
  sandbox_require_literal "$dockerfile" '"URIs: ${APT_RUNTIME_MIRROR}"'
  sandbox_require_literal "$dockerfile" '"https://getcomposer.org/download/${COMPOSER_VERSION}/composer.phar"'
  sandbox_reject_literal "$dockerfile" 'https://getcomposer.org/installer'
  sandbox_require_literal "$dockerfile" '"https://github.com/adoptium/temurin25-binaries/releases/download/${JAVA_RELEASE_TAG}/${JAVA_ARCHIVE}"'
  sandbox_require_literal "$dockerfile" '"https://cache.ruby-lang.org/pub/ruby/${RUBY_SERIES}/ruby-${RUBY_VERSION}.tar.xz"'
  sandbox_require_literal "$dockerfile" 'gcc-${GCC_MAJOR}=${GCC_APT_VERSION}'
  sandbox_require_literal "$dockerfile" 'g++-${GCC_MAJOR}=${GCC_APT_VERSION}'
  sandbox_require_literal "$dockerfile" '"${YARN_TARBALL_URL}" -o /tmp/yarn.tgz'
  sandbox_require_literal "$dockerfile" '"${PNPM_TARBALL_URL}" -o /tmp/pnpm.tgz'
  sandbox_require_literal "$dockerfile" 'echo "${YARN_SHA256}  /tmp/yarn.tgz" | sha256sum --check --strict'
  sandbox_require_literal "$dockerfile" 'echo "${PNPM_SHA256}  /tmp/pnpm.tgz" | sha256sum --check --strict'
  sandbox_require_literal "$dockerfile" 'image/config/launcher-settings.json /root/.claude/launcher-settings.json'
  sandbox_require_literal "$dockerfile" '/opt/python/current/bin'
  sandbox_require_literal "$dockerfile" '/opt/ruby/current/bin'
  sandbox_require_literal "$profile" '/opt/python/current/bin'
  sandbox_require_literal "$profile" '/opt/ruby/current/bin'
  sandbox_require_literal "$verifier" 'source "$VERSIONS_FILE"'
  sandbox_require_literal "$verifier" '"${ENVD_VERSION}"'
  sandbox_require_literal "$verifier" '"${ENVD_SHA256}  /usr/bin/envd"'
  sandbox_require_literal "$verifier" '"${CLAUDE_VERSION} (Claude Code)"'
  sandbox_require_literal "$verifier" '"${CLAUDE_PATCHED_SHA256}  /opt/claude-code/bin/claude"'
  sandbox_require_literal "$verifier" '"environment-runner ${ENVIRONMENT_MANAGER_VERSION}"'
  sandbox_require_literal "$verifier" '"URIs: ${APT_RUNTIME_MIRROR}"'

  # profile.sh 不得使用带版本号的运行时路径——必须通过 /opt/<runtime>/current 链接。
  if grep -Eq '/opt/(python|node|go|java|php|rust|ruby|maven|gradle)/[0-9]' "$profile"; then
    sandbox_contract_fail 'profile.sh contains a versioned runtime path'
  fi
  sandbox_contract_status
}

# Dockerfile 只能通过构建参数表达架构，不允许出现裸的架构字面量。
# 裸字面量是第二种架构静默退化为下载第一种架构二进制的根源。
sandbox_verify_dockerfile_is_architecture_neutral() {
  local dockerfile="$SANDBOX_DOCKERFILE" offenders

  offenders="$(grep -nE '(^|[^A-Za-z_])(x86_64|aarch64|x64|amd64|arm64|armv7|i686)([^A-Za-z0-9_-]|$)' "$dockerfile" || true)"
  [[ -z "$offenders" ]] \
    || sandbox_contract_fail "Dockerfile hardcodes an architecture instead of using a build argument:
$offenders"
}

# 镜像内 verifier 必须从构建 wrapper 使用的同一张 SANDBOX_ARCH_VARS 表解析架构值，
# 并且必须断言它运行在正确的机器上——否则错误架构的镜像可以通过自己的合同验证。
sandbox_verify_verifier_is_architecture_aware() {
  local verifier="$SANDBOX_ROOT_DIR/image/verify-contract.sh"

  sandbox_require_literal "$verifier" 'for name in $SANDBOX_ARCH_VARS; do'
  sandbox_require_literal "$verifier" 'for platform in $SANDBOX_PLATFORMS; do'
  sandbox_require_literal "$verifier" '[[ "$(uname -m)" == "$UNAME_MACHINE" ]]'
  sandbox_reject_literal "$verifier" 'platform is not linux/amd64'
  sandbox_contract_status
}

# ── 固定值关系校验 ──────────────────────────────────────────────

# 校验共享值（所有架构都相同的）之间的语义关系。
sandbox_verify_shared_relationships() {
  [[ "$UBUNTU_RELEASE_TAG" =~ ^noble-[0-9]{8}(\.[0-9]+)?$ ]] \
    || sandbox_contract_fail 'UBUNTU_RELEASE_TAG must name a dated Ubuntu noble build'
  [[ "$CA_BUNDLE_URL" =~ ^https://curl\.se/ca/cacert-[0-9]{4}-[0-9]{2}-[0-9]{2}\.pem$ ]] \
    || sandbox_contract_fail 'CA_BUNDLE_URL must use a versioned curl CA extract'
  [[ "$CA_BUNDLE_SHA256" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || sandbox_contract_fail 'CA_BUNDLE_SHA256 is not an immutable SHA-256 identity'
  [[ "$UBUNTU_SNAPSHOT_TIMESTAMP" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] \
    || sandbox_contract_fail 'UBUNTU_SNAPSHOT_TIMESTAMP must use YYYYMMDDTHHMMSSZ'
  [[ "$RUNTIME_REVISION" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
    || sandbox_contract_fail 'RUNTIME_REVISION must use YYYY-MM-DD'
  [[ "$JAVA_RELEASE_TAG" == "jdk-${JAVA_VERSION/+/%2B}" ]] \
    || sandbox_contract_fail 'JAVA_RELEASE_TAG does not match JAVA_VERSION'
  [[ "$RUBY_SERIES" == "${RUBY_VERSION%.*}" ]] \
    || sandbox_contract_fail 'RUBY_SERIES does not match RUBY_VERSION'
  [[ "$YARN_TARBALL_URL" == "https://registry.npmmirror.com/yarn/-/yarn-${YARN_VERSION}.tgz" ]] \
    || sandbox_contract_fail 'YARN_TARBALL_URL does not match YARN_VERSION'
  [[ "$PNPM_TARBALL_URL" == "https://registry.npmmirror.com/pnpm/-/pnpm-${PNPM_VERSION}.tgz" ]] \
    || sandbox_contract_fail 'PNPM_TARBALL_URL does not match PNPM_VERSION'
  [[ "$GCC_MAJOR" == "${GCC_VERSION%%.*}" && "$GCC_APT_VERSION" == "${GCC_VERSION}-"* ]] \
    || sandbox_contract_fail 'GCC package coordinates do not match GCC_VERSION'
  [[ "$ENVD_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || sandbox_contract_fail 'ENVD_VERSION is invalid'
  [[ "$CODEX_UNIVERSAL_RECIPE_REVISION" =~ ^[0-9a-f]{40}$ ]] \
    || sandbox_contract_fail 'CODEX_UNIVERSAL_RECIPE_REVISION is not a Git commit'
  [[ "$ENVIRONMENT_MANAGER_REVISION" =~ ^[0-9a-f]{40}$ ]] \
    || sandbox_contract_fail 'ENVIRONMENT_MANAGER_REVISION is not a Git commit'
  [[ "$ENVIRONMENT_MANAGER_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || sandbox_contract_fail 'ENVIRONMENT_MANAGER_VERSION is not a semantic version'
  [[ "$MAVEN_SHA512" =~ ^[0-9a-f]{128}$ ]] \
    || sandbox_contract_fail 'MAVEN_SHA512 is not a SHA-512'
  [[ "$OMA_ARTIFACT_REGISTRY" == */* \
      && "$OMA_ARTIFACT_REGISTRY" != */ \
      && "$OMA_ARTIFACT_REGISTRY" != *[[:space:]@]* \
      && "$OMA_ARTIFACT_REGISTRY" != *://* ]] \
    || sandbox_contract_fail 'OMA_ARTIFACT_REGISTRY is not a registry namespace'
  [[ "$TARGET_IMAGE_SIZE_BYTES" =~ ^[1-9][0-9]*$ ]] \
    || sandbox_contract_fail 'TARGET_IMAGE_SIZE_BYTES is not a positive integer'

  local checksum_name
  for checksum_name in PYTHON_SHA256 PHP_SHA256 RUBY_SHA256 YARN_SHA256 PNPM_SHA256 \
    GRADLE_SHA256 COMPOSER_SHA256; do
    sandbox_verify_sha256_value "$checksum_name" || return 1
  done
  sandbox_contract_status
}

# 校验当前选定架构的值。每个下载坐标都会交叉检查它声明服务的架构，
# 防止复制粘贴遗留的其他架构资源名通过检查。
sandbox_verify_arch_relationships() {
  local arch="$SANDBOX_ARCH"
  local java_major="${JAVA_VERSION%%.*}"
  local expected_java_archive="OpenJDK${java_major}U-jdk_${JAVA_ARCH}_linux_hotspot_${JAVA_VERSION/+/_}.tar.gz"
  local expected_apt_runtime_mirror

  sandbox_verify_oci_image_digest UBUNTU_DIGEST || return 1
  [[ "$JAVA_ARCHIVE" == "$expected_java_archive" ]] \
    || sandbox_contract_fail "$arch: JAVA_ARCHIVE does not match JAVA_VERSION and JAVA_ARCH"
  [[ "$BUN_ASSET" == bun-linux-* ]] \
    || sandbox_contract_fail "$arch: BUN_ASSET is not a Linux Bun release asset"
  [[ "$RUST_TARGET" == *-unknown-linux-gnu ]] \
    || sandbox_contract_fail "$arch: RUST_TARGET is not a Linux GNU target"
  [[ "$UV_TARGET" == *-unknown-linux-gnu ]] \
    || sandbox_contract_fail "$arch: UV_TARGET is not a Linux GNU target"
  [[ "$CLAUDE_NPM_PACKAGE" == "claude-code-linux-"* ]] \
    || sandbox_contract_fail "$arch: CLAUDE_NPM_PACKAGE is not a Linux Claude Code package"
  [[ "$ENVD_ARCH" == "$arch" ]] \
    || sandbox_contract_fail "$arch: ENVD_ARCH is $ENVD_ARCH"
  [[ "$GO_ARCH" == "$arch" ]] \
    || sandbox_contract_fail "$arch: GO_ARCH is $GO_ARCH"

  # 按架构选择对应的 APT 运行时镜像和预期 token 集合。
  case "$arch" in
    amd64)
      expected_apt_runtime_mirror='https://mirrors.aliyun.com/ubuntu/'
      sandbox_verify_arch_tokens x86_64 x64 amd64 || return 1
      ;;
    arm64)
      expected_apt_runtime_mirror='https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/'
      sandbox_verify_arch_tokens aarch64 arm64 || return 1
      ;;
    *)
      sandbox_contract_fail "no architecture token expectations defined for $arch" || return 1
      ;;
  esac
  [[ "$APT_RUNTIME_MIRROR" == "$expected_apt_runtime_mirror" ]] \
    || sandbox_contract_fail "$arch: APT_RUNTIME_MIRROR must be $expected_apt_runtime_mirror"

  # 校验架构相关的校验和格式。
  local checksum_name
  for checksum_name in ENVD_SHA256 NODE_SHA256 GO_SHA256 JAVA_SHA256 BUN_SHA256 \
    RUST_SHA256 UV_SHA256 DOCKER_CLI_SHA256 CLAUDE_SHA256 CLAUDE_PATCHED_SHA256; do
    sandbox_verify_sha256_value "$checksum_name" || return 1
  done

  sandbox_verify_external_artifacts
  sandbox_contract_status
}

# 校验每个架构相关的下载坐标是否使用了该架构对应的 token。
# 例如 arm64 的值必须包含 "aarch64" 或 "arm64" 之一。
sandbox_verify_arch_tokens() {
  local name value token matched
  local -a tokens=("$@")

  for name in UNAME_MACHINE NODE_ARCH GO_ARCH JAVA_ARCH BUN_ASSET RUST_TARGET \
    UV_TARGET DOCKER_CLI_ARCH CLAUDE_NPM_PACKAGE ENVD_ARCH; do
    value="${!name}"
    matched=no
    for token in "${tokens[@]}"; do
      case "$value" in
        "$token"|*[-_]"$token"|"$token"[-_]*|*[-_]"$token"[-_]*) matched=yes; break ;;
      esac
    done
    [[ "$matched" == yes ]] \
      || sandbox_contract_fail "$SANDBOX_ARCH: $name=$value uses none of the expected tokens: ${tokens[*]}" \
      || return 1
  done
  sandbox_contract_status
}

# ── 外部制品 ──────────────────────────────────────────────────
#
# Environment Manager 和 rclone-filestore 由各自仓库独立发布。
# versions.env 用 "unpublished" 标记尚未发布某架构的外部制品。

SANDBOX_EXTERNAL_ARTIFACT_NAMES=(
  ENVIRONMENT_MANAGER_IMAGE_DIGEST ENVIRONMENT_MANAGER_SHA256
  RCLONE_FILESTORE_IMAGE_DIGEST RCLONE_FILESTORE_SHA256
)

# 列出当前架构中标记为 unpublished 的外部制品名。
sandbox_unpublished_external_artifacts() {
  local name
  for name in "${SANDBOX_EXTERNAL_ARTIFACT_NAMES[@]}"; do
    [[ "${!name}" == "unpublished" ]] && printf '%s_%s\n' "$name" "$(sandbox_upper "$SANDBOX_ARCH")"
  done
  return 0
}

# 当前架构是否因缺少外部制品而被阻塞。
# 被阻塞时仓库自身仍然是一致的（verify-source 报告但不判定失败），
# 但 build 会拒绝：因为没有可以 COPY 的来源。
sandbox_arch_is_blocked() {
  [[ -n "$(sandbox_unpublished_external_artifacts)" ]]
}

# 描述阻塞原因和解除方法。
sandbox_describe_arch_block() {
  printf 'linux/%s is blocked: the externally owned environment-manager-rs and
rclone-filestore projects publish no linux/%s artifact yet. Once they do,
replace these versions.env markers with the immutable platform manifest digest
and the binary SHA-256:\n' "$SANDBOX_ARCH" "$SANDBOX_ARCH"
  sandbox_unpublished_external_artifacts | sed 's/^/    /'
}

# 校验外部制品值——如果架构未阻塞则检查 digest 和 SHA-256 格式。
sandbox_verify_external_artifacts() {
  sandbox_arch_is_blocked && return 0

  sandbox_verify_oci_image_digest ENVIRONMENT_MANAGER_IMAGE_DIGEST
  sandbox_verify_oci_image_digest RCLONE_FILESTORE_IMAGE_DIGEST
  sandbox_verify_sha256_value ENVIRONMENT_MANAGER_SHA256
  sandbox_verify_sha256_value RCLONE_FILESTORE_SHA256
  sandbox_contract_status
}

# ── 运行时合同字面量 ──────────────────────────────────────────

# 校验 Dockerfile、verifier、workflow 和配置文件中的运行时合同关键字面量。
sandbox_verify_runtime_contract_literals() {
  local dockerfile="$SANDBOX_DOCKERFILE"
  local verifier="$SANDBOX_ROOT_DIR/image/verify-contract.sh"
  local workflow="$SANDBOX_ROOT_DIR/.github/workflows/sandbox-image.yml"
  local bundler_config="$SANDBOX_ROOT_DIR/image/config/bundler-config"
  local gemrc="$SANDBOX_ROOT_DIR/image/config/gemrc"

  # Dockerfile 中的镜像结构和账号布局。
  sandbox_require_literal "$dockerfile" 'ARG SANDBOX_PLATFORM'
  sandbox_require_literal "$dockerfile" 'FROM --platform=${SANDBOX_PLATFORM} environment_manager AS environment_manager_source'
  sandbox_require_literal "$dockerfile" 'FROM --platform=${SANDBOX_PLATFORM} rclone_filestore AS rclone_filestore_source'
  sandbox_require_literal "$dockerfile" 'USER root'
  sandbox_require_literal "$dockerfile" 'ENV HOME=/root'
  sandbox_require_literal "$dockerfile" 'WORKDIR /home/user'
  sandbox_require_literal "$dockerfile" '/root/.claude.json'
  sandbox_require_literal "$dockerfile" 'COPY image/config/sudoers-claude /etc/sudoers.d/claude'
  sandbox_require_literal "$dockerfile" 'COPY image/patch-claude-sdk-url.py /tmp/patch-claude-sdk-url.py'
  sandbox_require_literal "$dockerfile" 'useradd --uid 1001 --gid claude --create-home --shell /bin/bash claude'
  sandbox_require_literal "$dockerfile" 'usermod --append --groups claude user'

  # 共享工具路径和环境变量。
  sandbox_require_literal "$dockerfile" 'PIP_CACHE_DIR=/home/claude/.cache/pip'
  sandbox_require_literal "$dockerfile" 'NODE_PATH=/home/claude/.npm-global/lib/node_modules'
  sandbox_require_literal "$dockerfile" 'PATH=/home/claude/.npm-global/bin:/home/claude/.local/bin:'
  sandbox_require_literal "$dockerfile" 'GEMRC=/home/user/.gemrc'
  sandbox_require_literal "$dockerfile" 'BUN_CONFIG_REGISTRY=https://registry.npmmirror.com'
  sandbox_require_literal "$dockerfile" 'BUNDLE_USER_CONFIG=/home/user/.bundle/config'
  sandbox_require_literal "$dockerfile" 'COPY image/config/bundler-config /home/user/.bundle/config'
  sandbox_require_literal "$dockerfile" 'MAVEN_ARGS="-s /home/user/.m2/settings.xml"'
  sandbox_require_literal "$dockerfile" 'GRADLE_USER_HOME=/home/user/.gradle'
  sandbox_require_literal "$dockerfile" 'COMPOSER_HOME=/home/user/.config/composer'

  # 外部制品和 envd 复制路径。
  sandbox_require_literal "$dockerfile" 'COPY --from=environment_manager_source /usr/local/bin/environment-manager /opt/env-runner/environment-manager'
  sandbox_require_literal "$dockerfile" 'COPY --from=rclone_filestore_source /opt/rclone/rclone-filestore /opt/rclone/rclone-filestore'
  sandbox_require_literal "$dockerfile" 'COPY image/envd-bin/envd-v${ENVD_VERSION}-linux-${ENVD_ARCH} /usr/bin/envd'
  sandbox_require_literal "$dockerfile" 'COPY image/verify-contract.sh /usr/local/bin/verify-sandbox-contract'
  sandbox_require_literal "$dockerfile" 'EXPOSE 49983'
  sandbox_require_literal "$dockerfile" 'ENTRYPOINT ["/usr/bin/envd", "-isnotfc", "-port", "49983"]'
  sandbox_require_literal "$dockerfile" '${RCLONE_FILESTORE_SHA256}  /opt/rclone/rclone-filestore'
  sandbox_require_literal "$dockerfile" 'ln -s /opt/env-runner/environment-manager /usr/local/bin/environment-manager'
  sandbox_require_literal "$dockerfile" 'ln -s /opt/claude-code/bin/claude /usr/local/bin/claude'
  sandbox_require_literal "$dockerfile" 'ln -sf "/usr/bin/gcc-${GCC_MAJOR}" /usr/local/bin/cc'
  sandbox_require_literal "$dockerfile" 'ln -sf "/usr/bin/g++-${GCC_MAJOR}" /usr/local/bin/c++'
  sandbox_require_literal "$dockerfile" 'ENVIRONMENT_MANAGER_SHA256'
  sandbox_require_literal "$dockerfile" 'ENVIRONMENT_MANAGER_VERSION'

  # BuildKit 缓存和 Rust 组件。
  sandbox_require_literal "$dockerfile" 'APT::Keep-Downloaded-Packages "true"'
  sandbox_require_literal "$dockerfile" '--components="rustc,rust-std-${RUST_TARGET},cargo,rustfmt-preview,clippy-preview,rust-analyzer-preview,llvm-tools-preview"'
  sandbox_require_literal "$dockerfile" 'COPY image/config/profile.sh /etc/profile.d/oma-sandbox.sh'

  # 镜像内 verifier 必须包含的关键断言。
  sandbox_require_literal "$verifier" 'assert_absent codex'
  sandbox_require_literal "$verifier" 'assert_absent dockerd'
  sandbox_require_literal "$verifier" 'assert_writable_dir /root/.claude'
  sandbox_require_literal "$verifier" 'getent passwd claude'
  sandbox_require_literal "$verifier" 'assert_writable_dir "$shared_dir"'
  sandbox_require_literal "$verifier" "assert_output_equals '/opt/oma-office/environment-manager' readlink /usr/local/bin/environment-manager"
  sandbox_require_literal "$verifier" 'provision-packages --protocol v1 --stdin'
  sandbox_require_literal "$verifier" 'assert_environment_manager_provisions_packages'
  sandbox_require_literal "$verifier" 'assert_environment_manager_stops_after_first_failure'
  sandbox_require_literal "$verifier" 'assert_cargo_builds'
  sandbox_require_literal "$verifier" 'assert_yarn_uses_npmmirror'
  sandbox_require_literal "$verifier" 'assert_bun_uses_npmmirror'
  sandbox_require_literal "$verifier" 'assert_bundler_uses_mirror'
  sandbox_require_literal "$verifier" 'assert_environment_manager_help_flags task-run'
  sandbox_require_literal "$verifier" 'assert_environment_manager_help_flags provision-packages'

  # 包管理器镜像配置。
  sandbox_require_literal "$bundler_config" 'BUNDLE_MIRROR__HTTPS://RUBYGEMS__ORG/: "https://mirrors.cloud.tencent.com/rubygems/"'
  sandbox_reject_literal "$bundler_config" 'FALLBACK_TIMEOUT'
  sandbox_require_literal "$gemrc" 'https://mirrors.cloud.tencent.com/rubygems/'

  # CI workflow 只执行静态合同检查，不访问 Registry，也不构建或发布镜像。
  sandbox_require_literal_count "$workflow" 'persist-credentials: false' 1
  sandbox_require_literal "$workflow" '- ".dockerignore"'
  sandbox_require_literal "$workflow" './tests/run-tests.sh'
  sandbox_require_literal "$workflow" './scripts/sandbox-image.sh verify-source --all'
  sandbox_require_literal "$workflow" './scripts/sandbox-image.sh --arch "$arch" check'
  sandbox_reject_literal "$workflow" './scripts/sandbox-image.sh build'
  sandbox_reject_literal "$workflow" './scripts/sandbox-image.sh test-image'
  sandbox_reject_literal "$workflow" 'docker/login-action@'
  sandbox_reject_literal "$workflow" 'docker push'
  sandbox_reject_literal "$workflow" 'publish-manifest'
  sandbox_reject_literal "$workflow" 'packages: write'
  sandbox_reject_literal "$workflow" 'actions/upload-artifact@'
  sandbox_reject_literal "$workflow" 'actions/download-artifact@'

  # 禁止使用可变的在线安装方式。
  sandbox_reject_literal "$dockerfile" 'python -m pip install --no-cache-dir --upgrade pip'
  sandbox_reject_literal "$dockerfile" 'gem install --no-document bundler'
  sandbox_reject_literal "$dockerfile" 'npm install --global --ignore-scripts "yarn@'
  sandbox_reject_literal "$dockerfile" 'npm install --global --ignore-scripts "pnpm@'
  sandbox_reject_literal "$dockerfile" 'PIP_CONFIG_FILE=/root/'
  sandbox_reject_literal "$dockerfile" 'trusted-host'
  sandbox_reject_literal "$dockerfile" 'GOSUMDB=off'

  sandbox_require_literal "$dockerfile" 'COPY image/toolkit/office /opt/oma-office'
  sandbox_require_literal "$dockerfile" 'COPY image/toolkit/runtime /opt/oma-tools'
  sandbox_require_literal "$dockerfile" 'ln -sfn /opt/oma-office/environment-manager /usr/local/bin/environment-manager'
  sandbox_require_literal "$SANDBOX_ROOT_DIR/scripts/lib/image-test.sh" '/usr/local/bin/verify-toolkit'

  sandbox_verify_curl_retry_policy "$dockerfile"
  sandbox_verify_curl_retry_policy "$workflow"
  sandbox_verify_workflow_action_pins "$workflow"

  # Dockerfile 最终状态必须是 root 用户、/home/user 工作目录。
  local final_user final_workdir
  final_user="$(awk 'toupper($1) == "USER" { value = $2 } END { print value }' "$dockerfile")"
  final_workdir="$(awk 'toupper($1) == "WORKDIR" { value = $2 } END { print value }' "$dockerfile")"
  [[ "$final_user" == "root" ]] \
    || sandbox_contract_fail "final Dockerfile USER is $final_user, expected root"
  [[ "$final_workdir" == "/home/user" ]] \
    || sandbox_contract_fail "final Dockerfile WORKDIR is $final_workdir, expected /home/user"
  sandbox_contract_status
}

# ── 入口函数 ──────────────────────────────────────────────────

# 校验所有必需文件是否存在。
sandbox_verify_required_files() {
  local path
  for path in \
    "$SANDBOX_DOCKERFILE" \
    "$SANDBOX_VERSIONS_FILE" \
    "$SANDBOX_ROOT_DIR/image/verify-contract.sh" \
    "$SANDBOX_ROOT_DIR/image/config/bundler-config" \
    "$SANDBOX_ROOT_DIR/image/config/gemrc" \
    "$SANDBOX_ROOT_DIR/image/config/profile.sh" \
    "$SANDBOX_ROOT_DIR/.github/workflows/sandbox-image.yml"; do
    [[ -f "$path" ]] || sandbox_contract_fail "missing $path"
  done
  sandbox_contract_status
}

# 校验指定架构的 envd 二进制——它随仓库提交，所以在构建前就可以检查字节。
sandbox_verify_envd_binary() {
  local envd_binary="$SANDBOX_ROOT_DIR/image/envd-bin/envd-v${ENVD_VERSION}-linux-${ENVD_ARCH}"

  [[ -x "$envd_binary" ]] \
    || sandbox_contract_fail "missing executable $envd_binary" \
    || return 1
  [[ "$(sandbox_sha256_file "$envd_binary")" == "$ENVD_SHA256" ]] \
    || sandbox_contract_fail "$SANDBOX_ARCH envd SHA-256 does not match versions.env"
  sandbox_contract_status
}

# 校验对所有架构都成立的共享事实。
# 必需文件缺失是硬性阻断（后续检查需要读取这些文件），其余采用累积模式。
sandbox_verify_shared_source() {
  sandbox_verify_required_files || return 1
  sandbox_verify_build_arg_wiring
  sandbox_verify_pinned_sources
  sandbox_verify_dockerfile_is_architecture_neutral
  sandbox_verify_verifier_is_architecture_aware
  sandbox_verify_runtime_contract_literals
  sandbox_verify_shared_relationships
  sandbox_contract_status
}

# 校验单个架构的事实。
# 带 --require-buildable 时，外部制品未发布的架构视为失败（而非仅报告为 blocked）。
sandbox_verify_arch_source() {
  local arch="$1" require_buildable="${2:-}"

  sandbox_select_arch "$arch" || return 1
  sandbox_build_args
  sandbox_verify_arch_relationships
  sandbox_verify_envd_binary

  if sandbox_arch_is_blocked; then
    if [[ "$require_buildable" == "--require-buildable" ]]; then
      sandbox_contract_fail "$(sandbox_describe_arch_block)"
    else
      sandbox_warn "$(sandbox_describe_arch_block)"
      sandbox_contract_status || return 1
      printf 'sandbox image source contract passed for linux/%s (blocked on external artifacts)\n' "$arch"
      return 0
    fi
  fi

  sandbox_contract_status || return 1
  printf 'sandbox image source contract passed for linux/%s\n' "$arch"
}

# 先校验共享合同，再遍历构建矩阵中的每个架构。
# 可以只请求一个架构；额外参数透传给 verify_arch_source（例如 --require-buildable）。
sandbox_verify_source() {
  local requested_arch="${1:-}" arch

  sandbox_verify_shared_source || return 1

  if [[ -n "$requested_arch" ]]; then
    shift
    sandbox_verify_arch_source "$requested_arch" "$@" || return 1
    return 0
  fi

  for arch in $(sandbox_arch_list); do
    sandbox_verify_arch_source "$arch" || true
  done
  sandbox_contract_status || return 1
  printf 'sandbox image source contract passed for the whole build matrix\n'
}

# 列出当前可以实际构建的架构（排除外部制品未发布的架构）。
# CI 从这个输出驱动构建矩阵，而不在 YAML 中重复声明架构列表。
sandbox_buildable_arch_list() {
  local arch
  for arch in $(sandbox_arch_list); do
    sandbox_select_arch "$arch" >/dev/null || continue
    sandbox_arch_is_blocked || printf '%s\n' "$arch"
  done
}
