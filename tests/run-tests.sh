#!/usr/bin/env bash
#
# 主机侧测试。不需要 Docker daemon 和网络。
# 在每个 PR 上运行，为多架构配置提供真实覆盖——不仅仅测试 runner 恰好使用的那个架构。

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$TESTS_DIR/.." && pwd)"
CLI="$ROOT_DIR/scripts/sandbox-image.sh"

tests_run=0
tests_failed=0

# ── 测试工具函数 ──────────────────────────────────────────────

check() {
  local description="$1"
  shift

  tests_run=$((tests_run + 1))
  if "$@" >/dev/null 2>&1; then
    printf 'ok   %s\n' "$description"
  else
    printf 'FAIL %s\n' "$description"
    tests_failed=$((tests_failed + 1))
  fi
}

fail_test() {
  local description="$1" detail="${2:-}"

  tests_run=$((tests_run + 1))
  tests_failed=$((tests_failed + 1))
  printf 'FAIL %s\n' "$description"
  [[ -n "$detail" ]] && printf '     %s\n' "$detail"
}

pass_test() {
  tests_run=$((tests_run + 1))
  printf 'ok   %s\n' "$1"
}

assert_equals() {
  local description="$1" expected="$2" actual="$3"

  if [[ "$expected" == "$actual" ]]; then
    pass_test "$description"
  else
    fail_test "$description" "expected [$expected], got [$actual]"
  fi
}

assert_not_empty() {
  local description="$1" value="$2"

  if [[ -n "$value" ]]; then
    pass_test "$description"
  else
    fail_test "$description" "value is empty"
  fi
}

# ── Manifest 发布测试用 mock docker ──────────────────────────

write_manifest_mock_docker() {
  local destination="$1"

  cat > "$destination" <<'MOCK_DOCKER'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >> "$MOCK_DOCKER_LOG"

source_digest_for_arch() {
  case "$1" in
    amd64) printf 'sha256:%064d' 0 | tr '0' 'a' ;;
    arm64) printf 'sha256:%064d' 0 | tr '0' 'b' ;;
    *) exit 2 ;;
  esac
}

child_digest_for_arch() {
  if [[ "${MOCK_SOURCE_WRAPPER:-no}" != yes ]]; then
    source_digest_for_arch "$1"
    return
  fi
  case "$1" in
    amd64) printf 'sha256:%064d' 0 | tr '0' 'c' ;;
    arm64) printf 'sha256:%064d' 0 | tr '0' 'd' ;;
    *) exit 2 ;;
  esac
}

emit_index() {
  local arch digest separator=""
  printf '{"schemaVersion":2,"manifests":['
  for arch in $MOCK_EXPECTED_ARCHES; do
    digest="$(child_digest_for_arch "$arch")"
    printf '%s{"digest":"%s","platform":{"os":"linux","architecture":"%s"}}' \
      "$separator" "$digest" "$arch"
    separator=,
  done
  printf ']}\n'
}

[[ "${1:-} ${2:-} ${3:-}" == 'buildx imagetools inspect' \
    || "${1:-} ${2:-} ${3:-}" == 'buildx imagetools create' ]] || exit 98

if [[ "${3:-}" == inspect && "${4:-}" == --raw ]]; then
  if [[ "${5:-}" == *:index ]]; then
    emit_index
  elif [[ "${MOCK_SOURCE_WRAPPER:-no}" == yes ]]; then
    case "${5:-}" in
      *@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) arch=amd64 ;;
      *@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) arch=arm64 ;;
      *) exit 95 ;;
    esac
    digest="$(child_digest_for_arch "$arch")"
    printf '{"schemaVersion":2,"manifests":[{"digest":"%s","platform":{"os":"linux","architecture":"%s"}},{"digest":"sha256:%064d","platform":{"os":"unknown","architecture":"unknown"}}]}\n' \
      "$digest" "$arch" 0
  else
    printf '{"schemaVersion":2,"config":{"digest":"sha256:%064d"},"layers":[]}\n' 0
  fi
elif [[ "${3:-}" == inspect && "${4:-}" == --format ]]; then
  case "${6:-}" in
    *@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|\
    *@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc) arch=amd64 ;;
    *@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb|\
    *@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd) arch=arm64 ;;
    *) exit 97 ;;
  esac
  if [[ "${MOCK_WRONG_PLATFORM:-no}" == yes ]]; then
    [[ "$arch" == amd64 ]] && arch=arm64 || arch=amd64
  fi
  printf '{"os":"linux","architecture":"%s"}\n' "$arch"
elif [[ "${3:-}" == create && "${4:-}" == --tag ]]; then
  exit 0
else
  exit 96
fi
MOCK_DOCKER
  chmod +x "$destination"
}

# 为 manifest 测试组装 <arch>=<image@digest> 参数。
manifest_specs() {
  local arch digest
  for arch in $(sandbox_buildable_arch_list); do
    case "$arch" in
      amd64) digest='sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;
      arm64) digest='sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' ;;
      *) return 1 ;;
    esac
    printf '%s=%s\n' "$arch" "registry.example/sandbox@$digest"
  done
}

# ── 阻塞架构与安全拒绝测试 ──────────────────────────────────

# 验证被阻塞的架构在调用 Docker 之前就被拒绝。
assert_blocked_build_stops_before_docker() (
  local arch="$1" work
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  mkdir -p "$work/bin"
  cat > "$work/bin/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
touch "$MOCK_DOCKER_LOG"
exit 99
MOCK_DOCKER
  chmod +x "$work/bin/docker"

  if PATH="$work/bin:$PATH" MOCK_DOCKER_LOG="$work/docker-called" \
      "$CLI" --arch "$arch" build >"$work/output" 2>&1; then
    return 1
  fi
  [[ ! -e "$work/docker-called" ]]
  grep -Fq "linux/$arch is blocked" "$work/output"
)

# 验证直接 push 在调用 Docker 之前就被拒绝。
assert_direct_push_stops_before_docker() (
  local work
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  mkdir -p "$work/bin"
  cat > "$work/bin/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
touch "$MOCK_DOCKER_LOG"
exit 99
MOCK_DOCKER
  chmod +x "$work/bin/docker"

  if PATH="$work/bin:$PATH" MOCK_DOCKER_LOG="$work/docker-called" \
      SANDBOX_IMAGE_OUTPUT=push "$CLI" --arch amd64 build >"$work/output" 2>&1; then
    return 1
  fi
  [[ ! -e "$work/docker-called" ]]
  grep -Fq 'direct build-and-push is disabled' "$work/output"
)

# 验证可变引用在调用 Docker 之前就被拒绝。
assert_manifest_input_rejected_before_docker() (
  local work arch
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  mkdir -p "$work/bin"
  write_manifest_mock_docker "$work/bin/docker"
  arch="$(sandbox_buildable_arch_list | head -n 1)"

  if PATH="$work/bin:$PATH" MOCK_DOCKER_LOG="$work/docker.log" \
      "$CLI" publish-manifest registry.example/sandbox:index \
      "$arch=registry.example/sandbox:mutable" >/dev/null 2>&1; then
    return 1
  fi
  [[ ! -e "$work/docker.log" ]]
)

# 验证平台错误的来源在写入目标标签之前就被拒绝。
assert_manifest_platform_rejected_before_create() (
  local work expected_arches
  local -a specs=()
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  mkdir -p "$work/bin"
  write_manifest_mock_docker "$work/bin/docker"
  expected_arches="$(sandbox_buildable_arch_list | tr '\n' ' ')"
  while IFS= read -r spec; do specs+=("$spec"); done < <(manifest_specs)

  if PATH="$work/bin:$PATH" MOCK_DOCKER_LOG="$work/docker.log" \
      MOCK_EXPECTED_ARCHES="$expected_arches" MOCK_WRONG_PLATFORM=yes \
      "$CLI" publish-manifest registry.example/sandbox:index "${specs[@]}" >/dev/null 2>&1; then
    return 1
  fi
  ! grep -Fq 'buildx imagetools create' "$work/docker.log"
)

# 验证发布使用的是已验证的不可变 digest 引用。
assert_manifest_uses_verified_digests() (
  local work expected_arches
  local -a specs=()
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  mkdir -p "$work/bin"
  write_manifest_mock_docker "$work/bin/docker"
  expected_arches="$(sandbox_buildable_arch_list | tr '\n' ' ')"
  while IFS= read -r spec; do specs+=("$spec"); done < <(manifest_specs)

  PATH="$work/bin:$PATH" MOCK_DOCKER_LOG="$work/docker.log" \
    MOCK_EXPECTED_ARCHES="$expected_arches" \
    "$CLI" publish-manifest registry.example/sandbox:index "${specs[@]}" >/dev/null
  grep -Fq 'buildx imagetools create --tag registry.example/sandbox:index' "$work/docker.log"
)

# 验证单平台 wrapper index 被展平为其已验证的子 manifest。
assert_manifest_flattens_wrapper_indices() (
  local work expected_arches create_line
  local -a specs=()
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  mkdir -p "$work/bin"
  write_manifest_mock_docker "$work/bin/docker"
  expected_arches="$(sandbox_buildable_arch_list | tr '\n' ' ')"
  while IFS= read -r spec; do specs+=("$spec"); done < <(manifest_specs)

  PATH="$work/bin:$PATH" MOCK_DOCKER_LOG="$work/docker.log" \
    MOCK_EXPECTED_ARCHES="$expected_arches" MOCK_SOURCE_WRAPPER=yes \
    "$CLI" publish-manifest registry.example/sandbox:index "${specs[@]}" >/dev/null
  create_line="$(grep -F 'buildx imagetools create --tag' "$work/docker.log")"
  # 不应使用外层 wrapper index 的 digest，而应使用展平后的子 manifest digest。
  [[ "$create_line" != *'@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'* ]]
  [[ "$create_line" == *'@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'* ]]
)

# ── 加载被测模块 ──────────────────────────────────────────────

# shellcheck source=../scripts/lib/common.sh
source "$ROOT_DIR/scripts/lib/common.sh"
# shellcheck source=../scripts/lib/config.sh
source "$ROOT_DIR/scripts/lib/config.sh"
# shellcheck source=../scripts/lib/source-contract.sh
source "$ROOT_DIR/scripts/lib/source-contract.sh"
sandbox_load_versions

ARCHES="$(sandbox_arch_list | tr '\n' ' ')"

# ── 构建矩阵 ──────────────────────────────────────────────────

printf '# 构建矩阵\n'
assert_equals '构建矩阵声明了两种架构' 'amd64 arm64 ' "$ARCHES"
assert_equals 'SANDBOX_PLATFORMS 的所有条目都以 linux/ 开头' '' "$(tr ' ' '\n' <<<"$SANDBOX_PLATFORMS" | grep -v '^linux/' | grep -v '^$' || true)"

# ── 按架构配置完整性 ──────────────────────────────────────────

printf '\n# 按架构配置完整性\n'
for arch in $ARCHES; do
  suffix="$(sandbox_upper "$arch")"
  missing=""
  for name in $SANDBOX_ARCH_VARS; do
    stored="${name}_${suffix}"
    [[ -n "${!stored:-}" ]] || missing="$missing $stored"
  done
  assert_equals "linux/$arch 的所有 SANDBOX_ARCH_VARS 条目都有值" '' "$missing"
done

# ── 架构解析 ──────────────────────────────────────────────────

printf '\n# 架构解析\n'
for arch in $ARCHES; do
  ( sandbox_select_arch "$arch" >/dev/null 2>&1 )
  check "sandbox_select_arch 接受 $arch" sandbox_select_arch "$arch"
done
check 'sandbox_select_arch 拒绝未知架构' \
  bash -c "source '$ROOT_DIR/scripts/lib/common.sh'; source '$ROOT_DIR/scripts/lib/config.sh'; sandbox_load_versions; ! sandbox_select_arch riscv64"

sandbox_select_arch amd64
assert_equals 'amd64 解析为 x86_64 Rust target' 'x86_64-unknown-linux-gnu' "$RUST_TARGET"
assert_equals 'amd64 解析为 x64 Node.js 归档名' 'x64' "$NODE_ARCH"
assert_equals 'amd64 解析为 x64 Claude Code 包' 'claude-code-linux-x64' "$CLAUDE_NPM_PACKAGE"
assert_equals 'amd64 解析为 x86_64 机器名' 'x86_64' "$UNAME_MACHINE"
assert_equals 'amd64 推导出 linux/amd64 平台' 'linux/amd64' "$SANDBOX_PLATFORM"
assert_equals 'amd64 使用 Ubuntu 标准源镜像' \
  'https://mirrors.aliyun.com/ubuntu/' "$APT_RUNTIME_MIRROR"
AMD64_UBUNTU_DIGEST="$UBUNTU_DIGEST"
AMD64_ENVD_SHA256="$ENVD_SHA256"

sandbox_select_arch arm64
assert_equals 'arm64 解析为 aarch64 Rust target' 'aarch64-unknown-linux-gnu' "$RUST_TARGET"
assert_equals 'arm64 解析为 arm64 Node.js 归档名' 'arm64' "$NODE_ARCH"
assert_equals 'arm64 解析为 arm64 Claude Code 包' 'claude-code-linux-arm64' "$CLAUDE_NPM_PACKAGE"
assert_equals 'arm64 解析为 aarch64 机器名' 'aarch64' "$UNAME_MACHINE"
assert_equals 'arm64 推导出 linux/arm64 平台' 'linux/arm64' "$SANDBOX_PLATFORM"
assert_equals 'arm64 使用 Ubuntu ports 源镜像' \
  'https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/' "$APT_RUNTIME_MIRROR"

# ── 架构间值隔离 ──────────────────────────────────────────────

printf '\n# 两种架构不共享架构相关值\n'
if [[ "$UBUNTU_DIGEST" == "$AMD64_UBUNTU_DIGEST" ]]; then
  fail_test '两种架构固定不同的 Ubuntu manifest'
else
  pass_test '两种架构固定不同的 Ubuntu manifest'
fi
if [[ "$ENVD_SHA256" == "$AMD64_ENVD_SHA256" ]]; then
  fail_test '两种架构固定不同的 envd 二进制'
else
  pass_test '两种架构固定不同的 envd 二进制'
fi

printf '\n# 按架构的校验和互不相同\n'
for name in NODE_SHA256 GO_SHA256 JAVA_SHA256 BUN_SHA256 RUST_SHA256 UV_SHA256 \
  DOCKER_CLI_SHA256 CLAUDE_SHA256 CLAUDE_PATCHED_SHA256 ENVD_SHA256; do
  amd64_value="$(eval "printf '%s' \"\${${name}_AMD64}\"")"
  arm64_value="$(eval "printf '%s' \"\${${name}_ARM64}\"")"
  if [[ "$amd64_value" == "$arm64_value" ]]; then
    fail_test "$name 在两种架构间不同" "两者都是 $amd64_value"
  else
    pass_test "$name 在两种架构间不同"
  fi
done

# ── BuildKit 参数组装 ──────────────────────────────────────────

printf '\n# BuildKit 参数组装\n'
for arch in $ARCHES; do
  sandbox_select_arch "$arch"
  if sandbox_build_args 2>/dev/null; then
    pass_test "linux/$arch 的每个 Dockerfile ARG 都解析到了值"
  else
    fail_test "linux/$arch 的每个 Dockerfile ARG 都解析到了值"
  fi
  arg_count="${#SANDBOX_BUILD_ARGS[@]}"
  name_count="$(sandbox_build_arg_names | wc -l | tr -d ' ')"
  assert_equals "linux/$arch 每个 Dockerfile ARG 传递一个 --build-arg" \
    "$((name_count * 2))" "$arg_count"
done

sandbox_select_arch amd64
sandbox_build_args
assert_not_empty 'amd64 构建参数携带 x86_64 Rust target' \
  "$(printf '%s\n' "${SANDBOX_BUILD_ARGS[@]}" | grep -Fx 'RUST_TARGET=x86_64-unknown-linux-gnu' || true)"
sandbox_select_arch arm64
sandbox_build_args
assert_not_empty 'arm64 构建参数携带 aarch64 Rust target' \
  "$(printf '%s\n' "${SANDBOX_BUILD_ARGS[@]}" | grep -Fx 'RUST_TARGET=aarch64-unknown-linux-gnu' || true)"

# ── Dockerfile 架构中性 ──────────────────────────────────────

printf '\n# Dockerfile 只通过构建参数表达架构\n'
assert_equals 'Dockerfile 不含架构字面量' '' \
  "$(grep -nE '(^|[^A-Za-z_])(x86_64|aarch64|x64|amd64|arm64|armv7|i686)([^A-Za-z0-9_-]|$)' "$ROOT_DIR/Dockerfile" || true)"
assert_equals 'DEBIAN_FRONTEND 外的 ARG 均无内联默认值' '' \
  "$(grep -E '^ARG [A-Z_]+=' "$ROOT_DIR/Dockerfile" | grep -v '^ARG DEBIAN_FRONTEND=' || true)"

# ── envd 二进制 ──────────────────────────────────────────────

printf '\n# 每个架构都附带了 envd 二进制\n'
for arch in $ARCHES; do
  sandbox_select_arch "$arch"
  binary="$ROOT_DIR/image/envd-bin/envd-v${ENVD_VERSION}-linux-${ENVD_ARCH}"
  if [[ -x "$binary" ]]; then
    assert_equals "linux/$arch envd 二进制匹配固定的 SHA-256" \
      "$ENVD_SHA256" "$(sandbox_sha256_file "$binary")"
  else
    fail_test "linux/$arch envd 二进制存在且可执行" "缺少 $binary"
  fi
done

# ── 镜像内 verifier 架构表 ──────────────────────────────────

printf '\n# 镜像内 verifier 解析同一张架构表\n'
VERIFIER="$ROOT_DIR/image/verify-contract.sh"
check 'verifier 读取 SANDBOX_ARCH_VARS' grep -Fq 'for name in $SANDBOX_ARCH_VARS; do' "$VERIFIER"
check 'verifier 读取 SANDBOX_PLATFORMS' grep -Fq 'for platform in $SANDBOX_PLATFORMS; do' "$VERIFIER"
check 'verifier 断言运行中的机器架构' grep -Fq '[[ "$(uname -m)" == "$UNAME_MACHINE" ]]' "$VERIFIER"
check 'verifier 不再硬编码 amd64' bash -c "! grep -Fq 'platform is not linux/amd64' '$VERIFIER'"

# 模拟 verifier 的架构解析，确保每种架构的 uname -m 能解析到正确的架构。
printf '\n# verifier 架构解析模拟\n'
for arch in $ARCHES; do
  suffix="$(sandbox_upper "$arch")"
  machine="$(eval "printf '%s' \"\${UNAME_MACHINE_${suffix}}\"")"
  resolved=""
  for platform in $SANDBOX_PLATFORMS; do
    candidate="${platform#linux/}"
    candidate_suffix="$(sandbox_upper "$candidate")"
    candidate_machine="$(eval "printf '%s' \"\${UNAME_MACHINE_${candidate_suffix}}\"")"
    [[ "$candidate_machine" == "$machine" ]] && resolved="$candidate" && break
  done
  assert_equals "$machine 机器解析到 linux/$arch" "$arch" "$resolved"
done

# ── CLI 行为 ──────────────────────────────────────────────────

printf '\n# CLI 架构选择\n'
assert_equals 'CLI 默认使用 amd64（向后兼容）' 'arch=amd64' \
  "$(env -u SANDBOX_ARCH "$CLI" metadata | grep '^arch=')"
assert_equals 'CLI 列出声明的架构' 'amd64 arm64' \
  "$("$CLI" architectures | tr '\n' ' ' | sed 's/ $//')"
assert_equals 'metadata 报告请求的架构' 'arch=arm64' \
  "$("$CLI" --arch arm64 metadata | grep '^arch=')"
assert_equals 'metadata 报告请求的平台' 'platform=linux/arm64' \
  "$("$CLI" --arch arm64 metadata | grep '^platform=')"
assert_equals 'metadata 解析按架构的运行时坐标' \
  'envd_sha256=f23090807b72f0610a01a280da546281be59e9a893cc7f513e1ef96fd76dc8b5' \
  "$("$CLI" --arch arm64 metadata | grep '^envd_sha256=')"
check 'CLI 拒绝矩阵外的架构' \
  bash -c "! '$CLI' --arch riscv64 metadata"

# ── 来源合同 ──────────────────────────────────────────────────

printf '\n# 来源合同\n'
check '来源合同通过全矩阵检查' "$CLI" verify-source --all
for arch in $ARCHES; do
  check "来源合同通过 linux/$arch 检查" "$CLI" --arch "$arch" verify-source
done

# ── 被阻塞架构 ──────────────────────────────────────────────

printf '\n# 被阻塞架构\n'
for arch in $ARCHES; do
  sandbox_select_arch "$arch"
  if sandbox_arch_is_blocked; then
    assert_equals "linux/$arch 被排除在 buildable 列表之外" '' \
      "$("$CLI" buildable-architectures | grep -Fx "$arch" || true)"
    check "linux/$arch 构建在调用 Docker 之前拒绝" \
      assert_blocked_build_stops_before_docker "$arch"
  else
    assert_equals "linux/$arch 列在 buildable 中" "$arch" \
      "$("$CLI" buildable-architectures | grep -Fx "$arch" || true)"
  fi
done

# ── 不可变 manifest 发布 ──────────────────────────────────────

printf '\n# 不可变 manifest 发布\n'
check '底层构建命令拒绝未验证的直接 push' \
  assert_direct_push_stops_before_docker
check '可变 manifest 来源在调用 Docker 前被拒绝' \
  assert_manifest_input_rejected_before_docker
if command -v jq >/dev/null 2>&1; then
  check '平台错误的来源在写入目标标签前被拒绝' \
    assert_manifest_platform_rejected_before_create
  check '索引从显式的已验证 digest 引用组装' \
    assert_manifest_uses_verified_digests
  check '单平台 wrapper index 被展平为已验证的子 manifest' \
    assert_manifest_flattens_wrapper_indices
else
  printf 'skip manifest 检查测试（jq 未安装）\n'
fi

# ── 反例测试 ──────────────────────────────────────────────────
# 确保合同检查真的能拒绝错误值——每个用例注入它要捕获的错误并验证检查失败。

printf '\n# 反例测试\n'

check '复制粘贴 amd64 Rust target 到 arm64 块会被拒绝' \
  bash -c "
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    sandbox_select_arch arm64
    RUST_TARGET=x86_64-unknown-linux-gnu
    ! sandbox_verify_arch_tokens aarch64 arm64
  "

check '复制粘贴 amd64 Claude Code 包到 arm64 块会被拒绝' \
  bash -c "
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    sandbox_select_arch arm64
    CLAUDE_NPM_PACKAGE=claude-code-linux-x64
    ! sandbox_verify_arch_tokens aarch64 arm64
  "

check 'arm64 Bun asset 放在 amd64 块中会被拒绝' \
  bash -c "
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    sandbox_select_arch amd64
    BUN_ASSET=bun-linux-aarch64
    ! sandbox_verify_arch_tokens x86_64 x64 amd64
  "

check 'JAVA_ARCHIVE 与 JAVA_ARCH 不匹配会被拒绝' \
  bash -c "
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    sandbox_select_arch arm64
    JAVA_ARCHIVE=OpenJDK25U-jdk_x64_linux_hotspot_25.0.3_9.tar.gz
    ! sandbox_verify_arch_relationships
  "

check 'amd64 APT 运行时镜像用于 arm64 会被拒绝' \
  bash -c "
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    sandbox_select_arch arm64
    APT_RUNTIME_MIRROR=https://mirrors.aliyun.com/ubuntu/
    ! sandbox_verify_arch_relationships
  "

check '重新引入架构字面量到 Dockerfile 中会被拒绝' \
  bash -c "
    set -e
    work=\"\$(mktemp -d)\"
    trap 'rm -rf \"\$work\"' EXIT
    sed 's|linux-\${NODE_ARCH}|linux-x64|' '$ROOT_DIR/Dockerfile' > \"\$work/Dockerfile\"
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    SANDBOX_DOCKERFILE=\"\$work/Dockerfile\"
    ! sandbox_verify_dockerfile_is_architecture_neutral
  "

check 'ARG 带内联默认值会被拒绝' \
  bash -c "
    set -e
    work=\"\$(mktemp -d)\"
    trap 'rm -rf \"\$work\"' EXIT
    sed 's|^ARG NODE_VERSION\$|ARG NODE_VERSION=24.18.0|' '$ROOT_DIR/Dockerfile' > \"\$work/Dockerfile\"
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    sandbox_select_arch amd64
    SANDBOX_DOCKERFILE=\"\$work/Dockerfile\"
    ! sandbox_verify_build_arg_wiring
  "

check 'versions.env 中无人消费的键会被拒绝' \
  bash -c "
    set -e
    work=\"\$(mktemp -d)\"
    trap 'rm -rf \"\$work\"' EXIT
    cp '$ROOT_DIR/versions.env' \"\$work/versions.env\"
    echo 'ORPHANED_VALUE=1' >> \"\$work/versions.env\"
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    sandbox_select_arch amd64
    SANDBOX_VERSIONS_FILE=\"\$work/versions.env\"
    ! sandbox_verify_build_arg_wiring
  "

check '安全关键 ARG 不允许被删除' \
  bash -c "
    set -e
    work=\"\$(mktemp -d)\"
    trap 'rm -rf \"\$work\"' EXIT
    grep -v '^ARG ENVD_SHA256\$' '$ROOT_DIR/Dockerfile' > \"\$work/Dockerfile\"
    source '$ROOT_DIR/scripts/lib/common.sh'
    source '$ROOT_DIR/scripts/lib/config.sh'
    source '$ROOT_DIR/scripts/lib/source-contract.sh'
    sandbox_load_versions
    sandbox_select_arch amd64
    SANDBOX_DOCKERFILE=\"\$work/Dockerfile\"
    ! sandbox_verify_build_arg_wiring
  "

# ── Shell 语法 ────────────────────────────────────────────────

check '工具载荷、依赖与 Skill 打包合同' python3 "$ROOT_DIR/tests/test_toolkit.py"

printf '\n# Shell 语法\n'
SHELL_SCRIPTS=(
  "$ROOT_DIR/scripts/sandbox-image.sh"
  "$ROOT_DIR/scripts/lib/common.sh"
  "$ROOT_DIR/scripts/lib/config.sh"
  "$ROOT_DIR/scripts/lib/source-contract.sh"
  "$ROOT_DIR/scripts/lib/build.sh"
  "$ROOT_DIR/scripts/lib/image-test.sh"
  "$ROOT_DIR/image/verify-contract.sh"
  "$ROOT_DIR/tests/run-tests.sh"
  "$ROOT_DIR/image/toolkit/verify.sh"
  "$ROOT_DIR/image/toolkit/office/environment-manager"
)
for script in "${SHELL_SCRIPTS[@]}"; do
  check "bash -n $(basename "$script")" bash -n "$script"
done

if command -v shellcheck >/dev/null 2>&1; then
  printf '\n# shellcheck\n'
  for script in "${SHELL_SCRIPTS[@]}"; do
    check "shellcheck $(basename "$script")" \
      shellcheck --severity=warning --external-sources --source-path="$ROOT_DIR/scripts/lib" "$script"
  done
else
  printf '\n# shellcheck（未安装，跳过）\n'
fi

printf '\n%s 个测试，%s 个失败\n' "$tests_run" "$tests_failed"
[[ "$tests_failed" -eq 0 ]]
