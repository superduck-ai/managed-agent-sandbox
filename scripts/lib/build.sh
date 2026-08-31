#!/usr/bin/env bash
# 单架构 Buildx 构建与 OCI 多架构索引发布。

SANDBOX_DEFAULT_IMAGE_TAG="ghcr.io/superduck-ai/managed-agent-sandbox:local"

sandbox_image_tag() {
  printf '%s\n' "${SANDBOX_IMAGE_TAG:-$SANDBOX_DEFAULT_IMAGE_TAG}"
}

# 运行 BuildKit Dockerfile 语法与结构检查（不实际构建）。
sandbox_check_dockerfile() (
  sandbox_build_args || return 1

  docker buildx build \
    --check \
    --platform "$SANDBOX_PLATFORM" \
    --build-context "environment_manager=$SANDBOX_ROOT_DIR" \
    --build-context "rclone_filestore=$SANDBOX_ROOT_DIR" \
    "${SANDBOX_BUILD_ARGS[@]}" \
    "$SANDBOX_ROOT_DIR"
)

# 构建单个架构的镜像。
#
# 多架构发布会在构建后组装各架构的结果，确保进入标签的每个镜像
# 都在自己的平台上通过了完整运行时合同，而非仅通过静态检查。
sandbox_build_image() (
  local image_tag image_output metadata_file cache_from cache_to

  image_tag="$(sandbox_image_tag)"
  image_output="${SANDBOX_IMAGE_OUTPUT:-load}"
  metadata_file="${SANDBOX_IMAGE_METADATA_FILE:-}"
  cache_from="${SANDBOX_BUILDX_CACHE_FROM:-}"
  cache_to="${SANDBOX_BUILDX_CACHE_TO:-}"

  # 禁止直接 push：必须先 load 到本地、运行 test-image，再手动 push。
  [[ "$image_output" == "load" ]] || {
    printf 'direct build-and-push is disabled; build with load, run test-image, then push the same local tag\n' >&2
    return 2
  }

  sandbox_verify_shared_source || return 1
  sandbox_verify_arch_source "$SANDBOX_ARCH" --require-buildable || return 1
  sandbox_build_args || return 1

  local -a build_options=(--provenance=false)
  if [[ -n "$metadata_file" ]]; then
    mkdir -p "$(dirname "$metadata_file")"
    build_options+=(--metadata-file "$metadata_file")
  fi
  [[ -n "$cache_from" ]] && build_options+=(--cache-from "$cache_from")
  [[ -n "$cache_to" ]] && build_options+=(--cache-to "$cache_to")

  docker buildx build \
    --platform "$SANDBOX_PLATFORM" \
    --build-context "environment_manager=docker-image://$ENVIRONMENT_MANAGER_IMAGE_REF" \
    --build-context "rclone_filestore=docker-image://$RCLONE_FILESTORE_IMAGE_REF" \
    "${SANDBOX_BUILD_ARGS[@]}" \
    --tag "$image_tag" \
    --load \
    "${build_options[@]}" \
    "$SANDBOX_ROOT_DIR"
)

# ── OCI 多架构索引发布 ──────────────────────────────────────────
#
# 只从不可变的、已验证的按架构制品组装统一标签。
# 写入目标标签前，每个来源都会被探测和校验。
# 要求显式的 <arch>=<image@sha256:digest> 映射，防止可变标签被用来
# "证明"索引包含了通过运行时合同验证的制品。

sandbox_publish_manifest() (
  local index_tag="${1:-}"
  shift || true
  local -a source_specs=("$@")
  local -a source_arches=()
  local -a source_refs=()
  local -a source_create_refs=()
  local -a source_child_digests=()
  local arch source_spec source_arch source_ref existing_arch expected_arch found
  local buildable_arches source_raw source_child_digest source_child_ref
  local source_image source_os source_architecture invalid_descriptor_count
  local real_platform_count actual_platforms expected_platforms final_raw
  local final_descriptor_count
  local child_digest i

  [[ -n "$index_tag" ]] || {
    printf 'usage: publish-manifest <index-tag> <arch>=<image@sha256:digest> [...]\n' >&2
    return 2
  }

  [[ "${#source_specs[@]}" -gt 0 ]] || {
    printf 'publish-manifest requires at least one explicit architecture source\n' >&2
    return 2
  }

  # ── 第一步：验证调用方提供的完整映射 ──
  # 在做任何 Registry I/O 之前完成验证。
  # 使用索引数组保持 Bash 3.2 兼容性。
  for source_spec in "${source_specs[@]}"; do
    [[ "$source_spec" == *=* ]] || {
      printf 'manifest source must be <arch>=<image@sha256:digest>: %s\n' "$source_spec" >&2
      return 2
    }
    source_arch="${source_spec%%=*}"
    source_ref="${source_spec#*=}"
    [[ -n "$source_arch" && -n "$source_ref" ]] || {
      printf 'manifest source must be <arch>=<image@sha256:digest>: %s\n' "$source_spec" >&2
      return 2
    }
    sandbox_is_known_arch "$source_arch" || {
      printf 'unknown manifest architecture: %s\n' "$source_arch" >&2
      return 2
    }
    [[ "$source_ref" =~ ^[^[:space:]@=]+@sha256:[0-9a-f]{64}$ \
        && "$source_ref" != *://* ]] || {
      printf 'manifest source for %s is not an immutable OCI digest reference: %s\n' \
        "$source_arch" "$source_ref" >&2
      return 2
    }
    if (( ${#source_arches[@]} > 0 )); then
      for existing_arch in "${source_arches[@]}"; do
        [[ "$existing_arch" != "$source_arch" ]] || {
          printf 'duplicate manifest source for architecture: %s\n' "$source_arch" >&2
          return 2
        }
      done
    fi
    source_arches+=("$source_arch")
    source_refs+=("$source_ref")
  done

  # ── 第二步：对比 buildable 集合 ──
  # 发布范围跟随当前 buildable 集合，不仅仅是声明的矩阵。
  # 这样，未发布外部制品的架构不会阻塞已有架构的发布。
  buildable_arches="$(sandbox_buildable_arch_list)"
  [[ -n "$buildable_arches" ]] || {
    printf 'the sandbox image contract currently has no buildable architectures\n' >&2
    return 1
  }
  for expected_arch in $buildable_arches; do
    found=no
    for source_arch in "${source_arches[@]}"; do
      [[ "$source_arch" == "$expected_arch" ]] && { found=yes; break; }
    done
    [[ "$found" == yes ]] || {
      printf 'missing manifest source for buildable architecture: %s\n' "$expected_arch" >&2
      return 2
    }
  done
  for source_arch in "${source_arches[@]}"; do
    found=no
    for expected_arch in $buildable_arches; do
      [[ "$source_arch" == "$expected_arch" ]] && { found=yes; break; }
    done
    [[ "$found" == yes ]] || {
      printf 'manifest source architecture is not currently buildable: %s\n' "$source_arch" >&2
      return 2
    }
  done

  # ── 第三步：逐个探测不可变来源 ──
  # 检查来源的可达性、JSON 有效性和平台正确性（通过 image config 验证，
  # 不信任 index descriptor 中的 platform 字段），确认后才允许写入 index_tag。
  for ((i = 0; i < ${#source_refs[@]}; i++)); do
    source_arch="${source_arches[$i]}"
    source_ref="${source_refs[$i]}"

    if ! source_raw="$(docker buildx imagetools inspect --raw "$source_ref")"; then
      sandbox_die "manifest source is not published: $source_ref" || return 1
    fi
    printf '%s' "$source_raw" | jq -e . >/dev/null 2>&1 || {
      sandbox_die "manifest source returned invalid JSON: $source_ref" || return 1
    }

    # 来源可能是 image index（BuildKit 附加了 provenance），也可能是单个 image manifest。
    if printf '%s' "$source_raw" | jq -e '(.manifests? | type) == "array"' >/dev/null; then
      # 来源是 image index —— 验证描述符合法性并提取目标平台的子 manifest digest。
      invalid_descriptor_count="$(
        printf '%s' "$source_raw" | jq '[.manifests[] |
          (.platform? | type) as $platform_type |
          (if $platform_type == "object" then .platform else {} end) as $platform |
          (if (.digest? | type) == "string" then .digest else "" end) as $digest |
          select(
            ($digest | test("^sha256:[0-9a-f]{64}$") | not) or
            $platform_type != "object" or
            ($platform.os? | type) != "string" or
            ($platform.architecture? | type) != "string" or
            (($platform.os == "unknown") != ($platform.architecture == "unknown"))
          )
        ] | length'
      )" || return 1
      [[ "$invalid_descriptor_count" == 0 ]] || {
        sandbox_die "manifest source has invalid or ambiguous descriptors: $source_ref" || return 1
      }

      # 确保 index 中恰好有一个"真实平台"条目（排除 unknown/unknown 类型的附加描述符）。
      real_platform_count="$(
        printf '%s' "$source_raw" | jq '[.manifests[] | select(
          .platform.os != "unknown" and .platform.architecture != "unknown"
        )] | length'
      )" || return 1
      [[ "$real_platform_count" == 1 ]] || {
        sandbox_die "manifest source for $source_arch must contain exactly one real platform: $source_ref" \
          || return 1
      }
      source_child_digest="$(
        printf '%s' "$source_raw" | jq -r --arg arch "$source_arch" '
          [.manifests[] | select(
            .platform.os == "linux" and .platform.architecture == $arch
          )] | if length == 1 then .[0].digest else empty end'
      )" || return 1
      [[ "$source_child_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
        sandbox_die "manifest source does not resolve uniquely to linux/$source_arch: $source_ref" \
          || return 1
      }
    else
      # 来源是单个 image manifest —— 验证基本结构。
      printf '%s' "$source_raw" | jq -e '
        .schemaVersion == 2 and
        (.config | type) == "object" and
        ((.config.digest? // "") | tostring | test("^sha256:[0-9a-f]{64}$")) and
        (.layers | type) == "array"' >/dev/null || {
          sandbox_die "manifest source is neither an image manifest nor an image index: $source_ref" \
            || return 1
        }
      source_child_digest="${source_ref##*@}"
    fi

    # index descriptor 的 platform 字段不可信——通过子 manifest 的 image config 验证真实平台。
    source_child_ref="${source_ref%@*}@${source_child_digest}"
    if ! source_image="$(
      docker buildx imagetools inspect --format '{{json .Image}}' "$source_child_ref"
    )"; then
      sandbox_die "cannot inspect source image config: $source_child_ref" || return 1
    fi
    printf '%s' "$source_image" | jq -e 'type == "object"' >/dev/null 2>&1 || {
      sandbox_die "source image config returned invalid JSON: $source_child_ref" || return 1
    }
    source_os="$(printf '%s' "$source_image" | jq -r '.os // .OS // empty')" || return 1
    source_architecture="$(
      printf '%s' "$source_image" | jq -r '.architecture // .Architecture // empty'
    )" || return 1
    [[ "$source_os" == linux && "$source_architecture" == "$source_arch" ]] || {
      sandbox_die "manifest source for $source_arch is actually ${source_os:-unknown}/${source_architecture:-unknown}: $source_ref" \
        || return 1
    }
    source_create_refs+=("$source_child_ref")
    source_child_digests+=("$source_child_digest")
  done

  # ── 第四步：创建 OCI Index ──
  # 使用已验证的平台子 manifest 引用组装，不使用外层 wrapper index。
  docker buildx imagetools create --tag "$index_tag" "${source_create_refs[@]}" || return 1

  # ── 第五步：验证已发布的索引 ──
  expected_platforms="$(
    for arch in "${source_arches[@]}"; do printf 'linux/%s\n' "$arch"; done | sort
  )"
  if ! final_raw="$(docker buildx imagetools inspect --raw "$index_tag")"; then
    sandbox_die "cannot inspect published index: $index_tag" || return 1
  fi
  printf '%s' "$final_raw" | jq -e '(.manifests | type) == "array"' >/dev/null 2>&1 || {
    sandbox_die "published tag is not an image index: $index_tag" || return 1
  }
  invalid_descriptor_count="$(
    printf '%s' "$final_raw" | jq '[.manifests[] |
      (.platform? | type) as $platform_type |
      (if $platform_type == "object" then .platform else {} end) as $platform |
      (if (.digest? | type) == "string" then .digest else "" end) as $digest |
      select(
        ($digest | test("^sha256:[0-9a-f]{64}$") | not) or
        $platform_type != "object" or
        ($platform.os? | type) != "string" or
        ($platform.architecture? | type) != "string" or
        (($platform.os == "unknown") != ($platform.architecture == "unknown"))
      )
    ] | length'
  )" || return 1
  [[ "$invalid_descriptor_count" == 0 ]] || {
    sandbox_die "published index has invalid or ambiguous descriptors: $index_tag" || return 1
  }
  # 子描述符数量必须恰好等于输入的架构数。
  final_descriptor_count="$(printf '%s' "$final_raw" | jq '.manifests | length')" || return 1
  [[ "$final_descriptor_count" == "${#source_arches[@]}" ]] || {
    sandbox_die "published index has $final_descriptor_count descriptors, expected ${#source_arches[@]} verified platform children" \
      || return 1
  }
  # 平台集合必须与预期完全一致。
  actual_platforms="$(
    printf '%s' "$final_raw" \
      | jq -r '.manifests[]
               | select(.platform.os != null and .platform.os != "unknown")
               | "\(.platform.os)/\(.platform.architecture)"' \
      | sort
  )" || return 1
  [[ "$actual_platforms" == "$expected_platforms" ]] || {
    printf 'published index platforms are %s, expected %s\n' \
      "$(tr '\n' ' ' <<<"$actual_platforms")" "$(tr '\n' ' ' <<<"$expected_platforms")" >&2
    return 1
  }

  # 每个子 manifest 的 digest 必须与通过运行时合同验证的制品完全一致，
  # 确保索引不会静默引用另一个构建。
  for ((i = 0; i < ${#source_arches[@]}; i++)); do
    arch="${source_arches[$i]}"
    source_child_digest="${source_child_digests[$i]}"
    child_digest="$(
      printf '%s' "$final_raw" | jq -r --arg arch "$arch" '
        [.manifests[] | select(
          .platform.architecture == $arch and .platform.os == "linux"
        )] | if length == 1 then .[0].digest else empty end'
    )" || return 1
    [[ "$child_digest" == "$source_child_digest" ]] || {
      printf 'index entry for linux/%s is %s, expected the verified %s\n' \
        "$arch" "${child_digest:-missing-or-duplicate}" "$source_child_digest" >&2
      return 1
    }
  done

  printf 'published multi-architecture index %s over %s\n' \
    "$index_tag" "$(tr '\n' ' ' <<<"$actual_platforms")"
)
