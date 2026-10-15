# Managed Agent Sandbox

本仓库构建并发布 **Open Managed Agents (OMA)** 使用的 Sandbox 基础镜像。

> 本仓库只负责镜像本身。它不是 OMA Server 镜像，也不管理 Environment、Session 或 Sandbox 的生命周期。

## 镜像概述

镜像配方精简自 [`codex-universal`](https://github.com/openai/codex-universal) 的固定版本实践：为每种受支持的运行时安装一个精选版本，排除 Codex CLI 与多版本管理器，并保持现有 Environment Manager 与 Claude Agent 路径。

### 用户模型

| 账号 | UID/GID | HOME | 说明 |
|---|---|---|---|
| `root`（默认） | 0/0 | `/root` | 镜像默认运行账号，`WORKDIR=/home/user` |
| `user` | 1000/1000 | `/home/user` | Managed Agent 工作目录所在账号 |
| `claude` | 1001/1001 | `/home/claude` | 兼容账号，两者均使用 Bash 并具有 passwordless sudo |

- 预置权限收紧的 `/root/.claude`（700）和合法空配置 `/root/.claude.json`（600），Claude Code 首次启动即可安全持久化 root 配置
- `/root/.claude/launcher-settings.json` 供 OMA 启动 Claude Code 时使用
- `/home/claude/.npm-global`、`/home/claude/.local/bin`、pip cache、Node 全局模块和 Claude 状态目录作为共享工具路径，`user` 和 `claude` 均可安全访问

### 预装运行时

Python · Node.js · Go · Java (Temurin) · PHP · Ruby · Bun · Rust（含 rustfmt/Clippy/rust-analyzer/LLVM tools） · GCC/G++ · CMake · Docker CLI · Claude Code · uv · Maven · Gradle · Composer · Yarn · pnpm

所有软件包管理器默认使用国内 HTTPS 镜像。pip 和 uv 使用相同的阿里云 PyPI 索引；APT 按架构读取 `versions.env`，amd64 使用阿里云，arm64 使用 TUNA 的 ubuntu-ports。npm、pnpm、Yarn 和 Bun 使用 npmmirror，Cargo 使用 RSProxy，RubyGems 和 Bundler 使用腾讯云 RubyGems。

## 仓库结构

| 路径 | 职责 |
|---|---|
| `Dockerfile` | 镜像配方，**零架构字面量**，全部通过 BuildKit 参数注入 |
| `versions.env` | 镜像合同的唯一值来源，同时声明构建矩阵 |
| `image/` | 被构建或复制进镜像的全部载荷 |
| ├─ `verify-contract.sh` | 镜像内运行时合同验证器 |
| ├─ `patch-claude-sdk-url.py` | 构建期解除 Claude Code 对 OMA `--sdk-url` 主机的限制 |
| ├─ `config/` | profile、npmrc、gemrc、bundler、maven、gradle 等配置文件 |
| └─ `envd-bin/` | 按版本和架构预置的 envd 二进制 |
| `scripts/sandbox-image.sh` | CLI 入口，只负责参数解析与分发 |
| `scripts/lib/` | 按职责拆分的模块 |
| ├─ `common.sh` | 基础断言原语（die、contract_fail、sha256 等） |
| ├─ `config.sh` | 版本加载、架构解析、BuildKit 参数组装 |
| ├─ `source-contract.sh` | 静态来源合同（构建前检查） |
| ├─ `build.sh` | 单架构构建与 OCI Manifest 发布 |
| └─ `image-test.sh` | 构建后镜像验证（平台、大小、运行时合同） |
| `tests/run-tests.sh` | 主机侧测试，不需要 Docker 与网络 |
| `.github/workflows/sandbox-image.yml` | CI：主机测试、全架构来源合同与 Dockerfile 静态检查 |
| `justfile` | 本地开发快捷命令 |

## 多架构支持

构建矩阵声明 `linux/amd64` 与 `linux/arm64`，两者对等：外部制品、供应链固定和 CI 验证矩阵完全相同。

### 配置方式

`versions.env` 使用统一的后缀命名规则管理架构相关值：

```
# 共享值（所有架构相同）
PYTHON_VERSION=3.13.14
RUST_VERSION=1.97.0

# 按架构区分的值
RUST_TARGET_AMD64=x86_64-unknown-linux-gnu
RUST_TARGET_ARM64=aarch64-unknown-linux-gnu
```

- `SANDBOX_PLATFORMS` 声明构建矩阵
- `SANDBOX_ARCH_VARS` 声明哪些变量名是架构相关的
- 构建 wrapper 与镜像内 verifier 读取同一张表，将 `<NAME>_<ARCH>` 解析为 `<NAME>`，二者不可能产生分歧
- Dockerfile 与 verifier 只看到不带后缀的普通名字，与单架构时期完全一致

### 架构中性保证

来源合同自动执行以下检查：

- **禁止裸架构字面量**：Dockerfile 中出现 `x86_64`、`aarch64`、`x64`、`amd64`、`arm64` 等任何裸字面量将被拒绝
- **交叉检查架构 token**：每个按架构区分的下载坐标必须包含该架构对应的 token（例如 arm64 的值必须含 `aarch64` 或 `arm64`），防止跨架构复制粘贴
- **构建参数完整性**：Dockerfile 的每个 `ARG` 必须由 `versions.env` 提供值，不允许内联默认值，不允许孤立键

新增架构时，若其外部制品（Environment Manager、rclone-filestore）尚未发布，来源合同会将该架构报告为 blocked 但不判定失败，`buildable-architectures` 会自动排除它，直到 `versions.env` 补齐不可变 digest 与 SHA-256。

## 供应链安全

### 单一值来源

`versions.env` 是所有版本、下载坐标、校验和的唯一来源：

- 构建 wrapper 从该文件生成 BuildKit 参数
- Dockerfile 中的 `ARG` 不提供重复默认值
- 相同文件复制到镜像内 `/etc/oma-sandbox-versions.env`，供运行时 verifier 读取
- 来源合同检查：无 `ARG` 带内联默认值、安全关键 `ARG` 始终存在、`versions.env` 中无孤立键

### 制品固定

- 所有远程归档必须匹配仓库固定的 SHA-256/SHA-512 校验和
- 构建期 APT 使用固定时间戳的 Ubuntu snapshot，避免滚动索引导致包版本不可解析
- curl CA extract 使用带日期的不可变 URL + SHA-256
- Yarn/pnpm 直接下载固定 tarball 并校验，不通过 `npm install` 接受 registry metadata
- Composer 下载带版本路径的 `composer.phar` 并校验，不执行可变的在线 installer
- `image/envd-bin/` 按版本和架构预置 envd 二进制，构建时只复制并校验 SHA-256
- Claude Code 先校验上游 tarball，再在一次性 stage 运行固定补丁；补丁后的二进制按架构校验 SHA-256，脚本、Python 和备份不进入最终镜像

### 外部 OCI 制品

Environment Manager 与 rclone-filestore 由各自仓库独立发布，不提交到本仓库：

- `OMA_ARTIFACT_REGISTRY` 配置制品所在的 Registry namespace（当前为 `ghcr.io/superduck-ai`）
- BuildKit 通过外部命名镜像上下文复制二进制，Dockerfile 再次校验字节
- 完整构建需要 GHCR 读取权限；CI 静态检查使用本地占位 context，不访问制品 Registry

## 构建与验证

需要 Docker、Buildx，以及读取 `versions.env` 中外部 OCI 制品的权限。

```console
just sandbox-image-architectures   # 查看声明和当前可构建的架构
just sandbox-image-check           # 主机测试 + 全架构来源合同 + Dockerfile 检查
just sandbox-image-test-scripts    # 仅主机侧测试
```

完整构建与验证由发布者在可运行目标架构的环境中执行，标签按架构区分：

```console
IMAGE_TAG=ghcr.io/superduck-ai/managed-agent-sandbox:<full-commit-sha>

SANDBOX_IMAGE_TAG="${IMAGE_TAG}-amd64" just arch=amd64 sandbox-image-verify   # amd64 runner
SANDBOX_IMAGE_TAG="${IMAGE_TAG}-arm64" just arch=arm64 sandbox-image-verify   # arm64 runner
```

## 发布多架构镜像

推送两个已通过运行时合同的单架构标签，再用它们的不可变 digest 组装统一标签：

```console
docker push "${IMAGE_TAG}-amd64"
docker push "${IMAGE_TAG}-arm64"

SANDBOX_IMAGE_TAG="$IMAGE_TAG" just sandbox-image-publish-manifest \
  amd64=registry.example/sandbox@sha256:... \
  arm64=registry.example/sandbox@sha256:...
```

业务方只使用不带架构后缀的统一标签；Docker 自动选择匹配的平台镜像。

## CI 流程

`.github/workflows/sandbox-image.yml` 在 PR 或手动触发时只执行静态检查：

1. 运行主机侧测试。
2. 验证 amd64、arm64 的来源与版本合同。
3. 对每个架构执行 Dockerfile `buildx --check`。

CI 不拉取外部 OCI 制品，不构建或推送镜像，也不更新 E2B Template。
完整镜像构建、运行时合同验证和 Manifest 发布均由发布者在本地执行。第三方
Actions 均固定到 commit SHA，Checkout 不持久化凭据。

## 镜像优化与历史测量

以下测量记录来自 2026-09-22 的单架构源码基线，早于当前多架构与 envd 更新；数值不代表当前镜像的体积。

镜像拉取与 CubeSandbox 模板制作需要分别看待。合并小配置文件后，最终镜像减少 12 个文件系统层；大型工具链仍分别保留层，以便复用未变更的内容。Yarn/pnpm 在 COPY 时直接设置 UID/GID 1001，后续只修正所有者不符的文件，避免递归 chown 把相同的软件包内容再次写进镜像层。以上主要减少拉取和逐层解包成本，并不减少最终文件树中的文件数量。

真正缩小最终文件树的处理在各自 builder 中、复制到 runtime 之前完成：Python 和 Ruby 的解释器及共享库使用 `strip --strip-debug` 去掉原生调试信息；Rust 的 LLVM tools 如果携带与主工具链字节相同的 LLVM 动态库，则通过相对符号链接复用主工具链的文件。比较不相同或文件不存在时保持原样。所有语言、包管理器、头文件和 Rust 工具组件继续保留；代价是 Python/Ruby 自身及随附原生扩展不再包含 C 级调试信息。运行时验收会实际加载 Python/Ruby 原生扩展，并启动 LLVM tools，覆盖这些处理可能影响的动态链接路径。

2026-09-22 使用相同 `versions.env` 分别完整构建改动前后镜像，实测如下。压缩体积取 OCI manifest 中 gzip layer 的字节数之和；未压缩体积在镜像首次挂载后读取两次 history，并按 RootFS 非空层数核验完整性。

| 指标 | 优化前 | 优化后 |
| --- | ---: | ---: |
| RootFS 层数（含标准空层） | 35 | 23 |
| gzip 层总字节数 | 1,283,002,276 | 1,169,313,343 |
| 未压缩层总字节数 | 3,457,130,496 | 3,165,511,680 |

压缩层数据减少约 108.4 MiB（8.86%），未压缩层数据减少约 278.1 MiB（8.44%）。最终文件树的主要减重来自 LLVM 去重约 190.2 MiB、Python/Ruby 调试信息剥离约 61.6 MiB；Yarn/pnpm 的约 26.2 MiB 重复层不会额外占用最终文件树。当前源码基线的 35 层与历史部署镜像的 37 层不同，不应跨版本直接比较。

按当前 CubeSandbox 默认容量公式估算，受测文件树对应的 ext4 可由 3.75 GiB 降至 3.50 GiB；实际以部署版本、模板配置和重新制作后的测量为准。文件数量基本不变，因此不能据此推导模板制作耗时按相同比例下降，也不代表已完成内网端到端性能验收。
