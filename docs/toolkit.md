# 工具镜像与云端发布

本仓库的根 Dockerfile 默认输出带工具能力的 managed-agent-sandbox。基础 runtime stage 继续保留，最后的 toolkit stage 安装依赖并复制独立实现的工具；不依赖 OMA 仓库工作目录、Manus 二进制或私有 Manus 服务。

## 镜像内容

| 类别 | 组件与用途 |
| --- | --- |
| 办公 | python-docx、openpyxl、python-pptx、LibreOffice Writer/Calc/Impress；可编辑文件、重算、PDF 预览 |
| PDF/OCR | pypdf、pdfplumber、ReportLab、Poppler、qpdf、Pandoc、Tesseract 中英文、Noto CJK |
| 媒体 | FFmpeg/ffprobe、SoX、ImageMagick、PyAV；剪辑、抽帧、字幕、拼接、混音 |
| 语音 | faster-whisper、镜像内 Whisper Tiny 权重、eSpeak NG；本地转写、时间戳、SRT 和基础配音 |
| 浏览器 | Playwright 与固定安装路径的 Chromium headless shell；导航、表单、截图、上传下载 |
| 图表 | Graphviz、Mermaid、D2、PlantUML、Typst |
| 开发 | gopls、terraform-ls、通用 rclone；原 rclone-filestore 二进制保留 |
| 服务 | Slides MCP 7 个工具、Workbench MCP 19 个工具、oma-tools CLI |
| 配套 | /opt/oma-office/skills/oma-office 中的自有 Skill、元数据检查、Office 转换和回归测试 |

原始 Environment Manager 位于 /opt/env-runner/environment-manager，原供应链 SHA-256 不变。公开入口 /usr/local/bin/environment-manager 指向 /opt/oma-office/environment-manager：task-run 前启动 8765/8766 服务并检查 readiness，结束、失败或终止后回收；其他子命令 exec 原二进制，不改变 provision-packages 协议。envd 仍是镜像 ENTRYPOINT。

两个 MCP 只监听 Sandbox 的回环地址，不对外开放服务端口。当前接入针对 Claude Sandbox runtime；宿主 Brain 访问这些回环地址的路由未在本实现中提供。聊天模型可使用 OMA 已配置的兼容模型。

Word render.py 针对 Session 中 MD/TXT/CSV/TSV 来源检查身份和报告周期，无来源字段拒绝导出。没有文本来源时不启用自动检查；直接调用 LibreOffice、自行保存 DOCX 和正文事实仍需另行核验。

## 依赖与发布边界

Python 顶层依赖固定版本；pip/npm 传递依赖没有完整锁文件，APT 仍使用基础镜像配置的运行时镜像源。assets.json 为 amd64、arm64 固定外部工具 SHA-256，为 Whisper 固定 revision 与文件校验和。大二进制与权重在构建时下载，不进入 Git。构建需要相关公开镜像源、GitHub 和 Hugging Face 可访问，不能称为离线构建。保留制品附带许可，模型与依赖遵循各自许可；不分发 Manus 或 Anthropic 办公 Skill。

远程 ASR/TTS、图片生成编辑与视频采样理解只有适配代码；服务与凭据仍需配置，聊天模型凭据不会自动传给预先启动的 MCP 服务。视频生成、音乐、音效、3D、生产部署等未实现。Tiny 中文转写质量有限；工具运行成功不等于业务内容或版式质量通过。

## 构建、验证与推送

沿用根目录既有发布命令。在可运行目标架构的机器上执行：

```sh
SANDBOX_IMAGE_TAG=ghcr.io/superduck-ai/managed-agent-sandbox:YOUR_COMMIT-amd64 \
  just arch=amd64 sandbox-image-verify
```

这会执行正式 Dockerfile 构建、基础运行时合同、工具测试和 envd health。arm64 使用对应的架构与标签。test-image 保留体积报告，不通过提高目标值掩盖工具依赖增加的体积。工具测试会生成临时产物并清理，不需要模型服务密钥。

通过后推送同一个已验证镜像，再按根 README 的不可变 digest 流程发布多架构索引。CI 仍只运行主机与来源/Dockerfile 检查，不自动发布镜像或模板。

## 云端 E2B 与 OMA

1. 将已验证镜像发布到云端模板构建器可拉取的 Registry。
2. 按实际云端 E2B 兼容服务的受支持流程，从镜像构建新模板，保留原 OMA runtime、envd 和文件挂载能力，记录成功的 template ID。普通 E2B Python 镜像不能代替 OMA 基础镜像。OMA 当前内置预构建适配器使用 CubeSandbox 协议，不应视为所有 E2B 平台的通用导入接口。
3. 手工模板路径：在测试部署完整配置内把 e2b.template 改为新 ID，保留现有 api_url、sandbox_url、凭据和其他配置，按部署方式重载。该值是部署默认值，会影响之后创建的普通 Environment。已成功预构建的 Environment 继续固定其模板。也可使用已有 OMA 预构建流水线完成逐 Environment 模板绑定。
4. 新建体验 Environment；旧 Environment 和现有 Sandbox 不会自动升级。避免直接修改数据库绑定。
5. 在本仓库执行 python3 image/toolkit/office/package.py /path/to/output，上传生成的 oma-office.skill 并绑定体验 Agent。完整 Skill 随镜像提供，不自动注册到 OMA；/root/.claude/skills 的会话挂载继续由 OMA 管理。
6. 合并 image/toolkit/runtime/agent-config.json 中的 system 要求、MCP 服务和逐工具权限，保留原 Agent 的其他提示、模型、Bash 和文件工具。新建 Claude Sandbox Session。
7. 真实模型完成工具状态、媒体剪辑/抽帧、中英文转写、三页 PPTX/PDF/备注、浏览器表单、Word、Excel 和图表任务，等待 Files 可下载并重新打开。再由另一位普通成员独立完成上传、执行和下载。

升级镜像标签不等于更新云端模板。发布记录须同时包含源码提交、目标架构、镜像 digest、template ID、Skill SHA-256 和真实 Session 验收结果。回退恢复原模板默认值及 Agent/Skill 版本，用新 Environment/Session 验证，不删除已有数据。

## 本分支验证说明

来源合同、主机测试与 Dockerfile 检查覆盖两种声明架构。工具回归可在已有依赖镜像上复制本分支载荷验证；这只能认证工具与监督入口，不能替代正式 Dockerfile 的全量依赖构建、各架构完整运行时合同或云端 E2B 模板验收。最终结果以交付记录为准。
