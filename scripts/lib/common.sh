#!/usr/bin/env bash
# 所有 sandbox-image 模块共享的基础原语。

# 致命错误：打印消息并返回非零。
sandbox_die() {
  printf '%s\n' "$*" >&2
  return 1
}

# ── 合同断言 ──────────────────────────────────────────────────
#
# 合同检查采用"累积失败"模式而非遇错即停：一次运行会报告所有不一致。
# 调用方用 sandbox_contract_status 判断最终结果。
# 全局计数器也能在 set -e 被函数结果测试抑制时继续正确工作。
SANDBOX_CONTRACT_FAILURES=0

sandbox_contract_fail() {
  printf 'source contract failed: %s\n' "$*" >&2
  SANDBOX_CONTRACT_FAILURES=$((SANDBOX_CONTRACT_FAILURES + 1))
  return 1
}

# 如果存在累积失败则返回非零。
sandbox_contract_status() {
  [[ "$SANDBOX_CONTRACT_FAILURES" -eq 0 ]]
}

sandbox_warn() {
  printf '%s\n' "$*" >&2
}

# 计算文件的 SHA-256 哈希值（兼容 Linux sha256sum 和 macOS shasum）。
sandbox_sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# 将架构名转为大写，用于 <NAME>_<ARCH> 查找。
# macOS 自带的 Bash 3.2 不支持 ${var^^}，所以用 tr 保持可移植性。
sandbox_upper() {
  printf '%s' "$1" | tr '[:lower:]' '[:upper:]'
}

# ── 文件内容断言 ──────────────────────────────────────────────

# 要求文件中必须包含指定字面量。
sandbox_require_literal() {
  local file="$1" literal="$2"

  grep -Fq -- "$literal" "$file" \
    || sandbox_contract_fail "$file does not contain $literal"
}

# 要求文件中指定字面量恰好出现 N 次。
sandbox_require_literal_count() {
  local file="$1" literal="$2" expected_count="$3" actual_count

  actual_count="$(grep -Fc -- "$literal" "$file" || true)"
  [[ "$actual_count" == "$expected_count" ]] \
    || sandbox_contract_fail "$file contains $actual_count copies of $literal, expected $expected_count"
}

# 要求文件中不得包含指定字面量。
sandbox_reject_literal() {
  local file="$1" literal="$2"

  if grep -Fq -- "$literal" "$file"; then
    sandbox_contract_fail "$file contains prohibited text $literal"
  fi
}
