#!/usr/bin/env bash
#
# Sandbox 基础镜像 CLI 入口。
#
# 每个子命令作用于 versions.env 声明的构建矩阵中的一个架构。
# 架构选择优先级：--arch > SANDBOX_ARCH 环境变量 > 默认 amd64（兼容原始单架构入口）。

set -euo pipefail

SANDBOX_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)"
# shellcheck source=lib/common.sh
source "$SANDBOX_LIB_DIR/common.sh"
# shellcheck source=lib/config.sh
source "$SANDBOX_LIB_DIR/config.sh"
# shellcheck source=lib/source-contract.sh
source "$SANDBOX_LIB_DIR/source-contract.sh"
# shellcheck source=lib/build.sh
source "$SANDBOX_LIB_DIR/build.sh"
# shellcheck source=lib/image-test.sh
source "$SANDBOX_LIB_DIR/image-test.sh"

usage() {
  cat >&2 <<'USAGE'
用法: sandbox-image.sh [--arch <arch>] <command> [argument ...]

命令:
  architectures                      列出构建矩阵中声明的所有架构
  buildable-architectures            列出当前可构建的架构
  metadata                           打印指定架构的合同元数据
  verify-source [--all]              检查固定来源；--all 覆盖所有架构
  check                              运行 BuildKit Dockerfile 检查
  build                              构建单个架构并加载到本地 daemon
  test-image <image>                 执行平台、大小与运行时合同验证
  publish-manifest <tag> <arch>=<image@digest> ...
                                     从不可变来源组装并验证 OCI 多架构索引

环境变量:
  SANDBOX_ARCH                   --arch 缺省时使用的架构（默认: amd64）
  SANDBOX_IMAGE_TAG              构建或测试使用的镜像标签
  SANDBOX_IMAGE_OUTPUT           设置时必须为 load；拒绝直接推送
  SANDBOX_IMAGE_METADATA_FILE    buildx --metadata-file 的输出路径
  SANDBOX_BUILDX_CACHE_FROM      buildx --cache-from 配置
  SANDBOX_BUILDX_CACHE_TO        buildx --cache-to 配置
USAGE
}

sandbox_default_arch() {
  printf '%s\n' amd64
}

main() {
  local arch=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --arch)
        [[ $# -ge 2 ]] || { usage; return 2; }
        arch="$2"
        shift 2
        ;;
      --arch=*)
        arch="${1#--arch=}"
        shift
        ;;
      *)
        break
        ;;
    esac
  done

  sandbox_load_versions

  local command="${1:-}"
  shift || true

  if [[ "$command" == "architectures" ]]; then
    sandbox_arch_list
    return 0
  fi

  if [[ "$command" == "buildable-architectures" ]]; then
    sandbox_buildable_arch_list
    return 0
  fi

  # verify-source --all 自己遍历矩阵，不需要预选架构。
  if [[ "$command" == "verify-source" && "${1:-}" == "--all" ]]; then
    [[ -z "$arch" ]] || { printf -- '--arch and --all are mutually exclusive\n' >&2; return 2; }
    sandbox_verify_source
    return 0
  fi

  [[ -n "$arch" ]] || arch="${SANDBOX_ARCH:-$(sandbox_default_arch)}"
  sandbox_select_arch "$arch"

  case "$command" in
    metadata) sandbox_metadata ;;
    verify-source) sandbox_verify_source "$arch" ;;
    check) sandbox_verify_source "$arch" && sandbox_check_dockerfile ;;
    build) sandbox_build_image ;;
    test-image) sandbox_test_image "${1:-$(sandbox_image_tag)}" ;;
    publish-manifest) sandbox_publish_manifest "$@" ;;
    *) usage; return 2 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
