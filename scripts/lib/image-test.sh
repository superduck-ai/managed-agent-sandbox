#!/usr/bin/env bash
# 构建后镜像验证：平台身份、未压缩大小、镜像内运行时合同、envd 存活检查。

# OCI/Docker 中空 tar 层的 DiffID。这种层在 `docker history` 中合法地报告大小为零。
SANDBOX_EMPTY_TAR_DIFF_ID="sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef"

# 在容器内计算指定文件的 SHA-256。
sandbox_image_file_sha256() {
  local image_ref="$1" image_path="$2" output sha256

  output="$(docker run --rm --platform "$SANDBOX_PLATFORM" --entrypoint sha256sum "$image_ref" "$image_path")"
  sha256="${output%%[[:space:]]*}"
  [[ "$sha256" =~ ^[0-9a-f]{64}$ ]] || {
    printf 'docker returned an invalid SHA-256 for %s: %s\n' "$image_path" "$sha256" >&2
    return 1
  }
  printf '%s\n' "$sha256"
}

# ── 未压缩镜像大小测量 ──────────────────────────────────────────
#
# 拒绝任何 docker 无法完整或稳定报告的结果。
# 步骤：
#   1. 先跑一个空容器触发 containerd 解包（否则新加载的层可能报告零大小）
#   2. 读取两次 `docker image history`，拒绝不稳定结果
#   3. 从 RootFS.Layers 推导权威的非空层数，验证 history 的非零条目数一致
sandbox_measure_image() {
  local image_ref="$1"
  local rootfs_layer_count empty_layer_template rootfs_empty_layer_markers
  local layer_sizes confirmed_layer_sizes rootfs_empty_layer_count
  local expected_nonempty_layer_count history_nonempty_layer_count

  rootfs_layer_count="$(docker image inspect --format '{{len .RootFS.Layers}}' "$image_ref")"
  empty_layer_template="{{range .RootFS.Layers}}{{if eq . \"$SANDBOX_EMPTY_TAR_DIFF_ID\"}}1{{end}}{{end}}"
  rootfs_empty_layer_markers="$(docker image inspect --format "$empty_layer_template" "$image_ref")"

  [[ "$rootfs_layer_count" =~ ^[1-9][0-9]*$ ]] || {
    printf 'docker returned an invalid RootFS layer count: %s\n' "$rootfs_layer_count" >&2
    return 1
  }
  [[ "$rootfs_empty_layer_markers" =~ ^1*$ ]] || {
    printf 'docker returned invalid empty RootFS layer markers: %s\n' "$rootfs_empty_layer_markers" >&2
    return 1
  }

  # 跑一个空容器触发 containerd 解包层元数据。
  docker run --rm --platform "$SANDBOX_PLATFORM" --entrypoint /bin/true "$image_ref"
  # 两次读取，拒绝不稳定结果。
  layer_sizes="$(docker image history --human=false --format '{{.Size}}' "$image_ref")"
  confirmed_layer_sizes="$(docker image history --human=false --format '{{.Size}}' "$image_ref")"
  [[ "$layer_sizes" == "$confirmed_layer_sizes" ]] || {
    printf 'docker image history changed between reads; refusing an unstable size result\n' >&2
    return 1
  }
  if [[ -z "$layer_sizes" ]] || grep -Eqv '^[0-9]+$' <<<"$layer_sizes"; then
    printf 'docker returned invalid uncompressed layer sizes\n' >&2
    return 1
  fi

  # 验证 history 非零条目数与 RootFS 非空层数一致。
  history_nonempty_layer_count="$(awk '$1 > 0 { count++ } END { print count + 0 }' <<<"$layer_sizes")"
  rootfs_empty_layer_count="${#rootfs_empty_layer_markers}"
  expected_nonempty_layer_count=$((rootfs_layer_count - rootfs_empty_layer_count))
  ((history_nonempty_layer_count == expected_nonempty_layer_count)) || {
    printf 'docker returned incomplete or inconsistent image history: %s non-empty entries for %s non-empty RootFS layers\n' \
      "$history_nonempty_layer_count" "$expected_nonempty_layer_count" >&2
    return 1
  }

  SANDBOX_MEASURED_ROOTFS_LAYERS="$rootfs_layer_count"
  SANDBOX_MEASURED_EMPTY_LAYERS="$rootfs_empty_layer_count"
  SANDBOX_MEASURED_HISTORY_ENTRIES="$(awk 'END { print NR }' <<<"$layer_sizes")"
  SANDBOX_MEASURED_HISTORY_NONEMPTY="$history_nonempty_layer_count"
  SANDBOX_MEASURED_UNCOMPRESSED_BYTES="$(awk '{sum += $1} END {printf "%.0f", sum}' <<<"$layer_sizes")"
}

# 确保镜像携带的是仓库当前的合同，而非某个较旧但自洽的版本。
sandbox_verify_image_matches_repository() {
  local image_ref="$1" expected image_value

  expected="$(sandbox_sha256_file "$SANDBOX_VERSIONS_FILE")"
  image_value="$(sandbox_image_file_sha256 "$image_ref" /etc/oma-sandbox-versions.env)"
  [[ "$image_value" == "$expected" ]] || {
    printf 'image version contract checksum mismatch: expected %s, got %s\n' "$expected" "$image_value" >&2
    return 1
  }

  expected="$(sandbox_sha256_file "$SANDBOX_ROOT_DIR/image/verify-contract.sh")"
  image_value="$(sandbox_image_file_sha256 "$image_ref" /usr/local/bin/verify-sandbox-contract)"
  [[ "$image_value" == "$expected" ]] || {
    printf 'image runtime verifier checksum mismatch: expected %s, got %s\n' "$expected" "$image_value" >&2
    return 1
  }

  local resource image_path
  for resource in verify.py profile.sh; do
    image_path="/opt/oma-resources/$resource"
    [[ "$resource" != profile.sh ]] || image_path=/etc/profile.d/oma-toolkit.sh
    expected="$(sandbox_sha256_file "$SANDBOX_ROOT_DIR/image/resources/$resource")"
    image_value="$(sandbox_image_file_sha256 "$image_ref" "$image_path")"
    [[ "$image_value" == "$expected" ]] || {
      printf 'image resource checksum mismatch: %s\n' "$image_path" >&2
      return 1
    }
  done
}

# envd 存活检查：启动容器后轮询 health 端点，期望 HTTP 204。
sandbox_verify_envd_health() (
  local image_ref="$1"
  local container_id health_status attempt

  container_id="$(docker run --detach --platform "$SANDBOX_PLATFORM" "$image_ref")"
  cleanup() {
    docker rm --force "$container_id" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT

  for ((attempt = 1; attempt <= 30; attempt++)); do
    health_status="$(
      docker exec "$container_id" \
        curl --silent --output /dev/null --write-out '%{http_code}' \
        http://127.0.0.1:49983/health 2>/dev/null || true
    )"
    if [[ "$health_status" == "204" ]]; then
      return 0
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)" != "true" ]]; then
      break
    fi
    sleep 1
  done

  printf 'envd health check failed: expected HTTP 204, got %s\n' "${health_status:-no response}" >&2
  docker logs "$container_id" >&2 || true
  return 1
)

# ── 镜像完整性测试入口 ──────────────────────────────────────────
#
# 依次执行：平台身份 → 未压缩大小测量 → 合同文件一致性 → 运行时合同 → envd 存活。
sandbox_test_image() {
  local image_ref="${1:-}"
  local image_os image_arch storage_size image_id size_target_status

  [[ -n "$image_ref" ]] || {
    printf 'image reference is required\n' >&2
    return 2
  }

  # 验证镜像平台与当前选定架构一致。
  image_os="$(docker image inspect --format '{{.Os}}' "$image_ref")"
  image_arch="$(docker image inspect --format '{{.Architecture}}' "$image_ref")"
  storage_size="$(docker image inspect --format '{{.Size}}' "$image_ref")"
  image_id="$(docker image inspect --format '{{.Id}}' "$image_ref")"

  [[ "$image_os/$image_arch" == "$SANDBOX_PLATFORM" ]] || {
    printf 'image platform mismatch: expected %s, got %s/%s\n' "$SANDBOX_PLATFORM" "$image_os" "$image_arch" >&2
    return 1
  }
  [[ "$storage_size" =~ ^[0-9]+$ ]] || {
    printf 'docker returned a non-numeric storage size: %s\n' "$storage_size" >&2
    return 1
  }

  sandbox_measure_image "$image_ref" || return 1

  # 大小目标是报告指标，不是硬性限制。
  size_target_status="at_or_below_target"
  if ((SANDBOX_MEASURED_UNCOMPRESSED_BYTES > TARGET_IMAGE_SIZE_BYTES)); then
    size_target_status="above_target"
  fi

  printf '%s\n' \
    "image=$image_ref" \
    "platform=$image_os/$image_arch" \
    "storage_size_bytes=$storage_size" \
    "uncompressed_size_bytes=$SANDBOX_MEASURED_UNCOMPRESSED_BYTES" \
    "target_image_size_bytes=$TARGET_IMAGE_SIZE_BYTES" \
    "size_target_status=$size_target_status" \
    "rootfs_layers=$SANDBOX_MEASURED_ROOTFS_LAYERS" \
    "rootfs_empty_layers=$SANDBOX_MEASURED_EMPTY_LAYERS" \
    "history_entries=$SANDBOX_MEASURED_HISTORY_ENTRIES" \
    "history_nonempty_layers=$SANDBOX_MEASURED_HISTORY_NONEMPTY" \
    "image_id=$image_id"

  # 按 SHA-256 比较仓库当前文件与镜像内文件。
  sandbox_verify_image_matches_repository "$image_ref" || return 1
  # 执行镜像内运行时合同验证器。
  docker run --rm --platform "$SANDBOX_PLATFORM" \
    --entrypoint /usr/local/bin/verify-sandbox-contract \
    "$image_ref"
  docker run --rm --network none -e HF_HUB_OFFLINE=1 --platform "$SANDBOX_PLATFORM" \
    --entrypoint timeout "$image_ref" 180s python3 /opt/oma-resources/verify.py
  # envd 存活检查。
  sandbox_verify_envd_health "$image_ref"
}
