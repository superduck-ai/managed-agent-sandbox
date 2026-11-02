# Sandbox 工具扩展：范围、安装清单与验收

状态：默认工具、资源和 Skill 迁移已实现；arm64 镜像与本地工作流已进入真实验收，远程图片服务和原生 amd64 运行另验。安装合同见 resources.md，实际结果以验收报告为准。
日期：2026-11-01。
源码起点：origin/main，5595c91e4e8ad2a84d9cdf8b857cbd7e74f8d385。
工作分支：codex/sandbox-toolkit。

## 目标和完成条件

从最新 main 重新实现通用工具扩展，覆盖现有 26 个独立 Skill 的本地能力。镜像提供成熟 CLI、Python 库、字体、浏览器和本地模型；Skill 提供操作流程、轻量脚本、模板、检查及交付要求。

完成条件是：目标架构的完整镜像构建成功，新增包版本与制品可追溯，基础运行时合同保持通过，所有本地工具通过真实任务测试，OMA 的 Skill 挂载、工具执行和成品下载链路通过。远程服务分支单独配置和验收，不能算入本地成功。

不以 codex/office-media-toolkit 为代码起点，不复制该分支实现，不使用其镜像作为新结果的验收证据。既有 Skill 只作为能力需求来源。

## 构建决策

- 从 main 的根 Dockerfile 与现有构建入口继续开发，在基础运行时后增加独立 toolkit stage；正式工具版本与下载校验信息按仓库规则纳入 versions.env。
- 不把 3992 作为隐含父镜像。本次基于 main 源码构建，其 Manager、rclone-filestore 和其他基础版本以 main 为准；main 与 3992 的基础组件差异属于独立事项，不顺手升级或覆盖。
- 沿用现有 scripts/sandbox-image.sh、justfile、来源检查及架构矩阵，增加工具层检查，避免再维护一套并行构建系统。
- 第一发布目标为 linux/amd64，另保留 linux/arm64 来源定义与适配；只有在各架构分别完成真实构建和运行验证后才发布多架构镜像。
- 保留 envd、Environment Manager、Claude 路径和 rclone-filestore，保持原 ENTRYPOINT、用户模型与挂载合同。工具安装不得替换基础组件。
- 不新增办公 MCP 服务、常驻浏览器服务或统一工具网关。不把整个旧工具包重新搬回来。
- 不给 OMA 生产代码增加安装逻辑；必要修改在镜像、Skill 包与验收代码。基础 3001/mcp 是 Brain 的执行通道，不因移除办公 MCP 而删除。

## 已有运行时：复用，不重复安装

main 已定义 Python 3.13.14、Node 24.18.0、Go 1.26.5、Java 25、Bun、Git、curl、Bash、uv、Rust、PHP、Ruby、GCC 和相关构建工具。构建时以 main 的实际合同检查为准。

使用 main 已有的 Python 虚拟环境，Skill、pip 和 Python CLI 必须使用同一个解释器。Node、Java 和 Go 复用既有路径。

## 默认预装范围

以下项目纳入首版工具镜像；工具制品版本与哈希写入 versions.env，Python/npm 依赖写入各自锁文件；实际结果按架构分别验证。此清单不是可直接执行的依赖锁文件。

### 系统软件包

| 软件包 | 用途 | 对应能力 |
| --- | --- | --- |
| libreoffice-writer | DOCX 编辑后转换与预览 | Word |
| libreoffice-calc | XLSX 公式重算、打印和转换 | Excel、图表、财务模型 |
| libreoffice-impress | PPTX 转 PDF 和渲染 | PowerPoint |
| poppler-data | 中文 CID PDF CMap 数据 | PDF 中文提取 |
| poppler-utils | pdfinfo、pdftotext、pdftoppm 等 | PDF 检查、提取与页面预览 |
| qpdf | PDF 结构、页面与加密信息处理 | PDF |
| pandoc | Markdown 等内容格式转换 | 文档、PDF |
| fontconfig | 字体发现与缓存 | 所有渲染任务 |
| fonts-noto-cjk | 简繁中文等 CJK 字体 | 中文文档、图表、浏览器 |
| fonts-dejavu-core | 常用西文字体 | 文档、数学符号和图表 |
| tesseract-ocr | OCR 引擎 | PDF、特殊图片 |
| tesseract-ocr-chi-sim | 简体中文 OCR 数据 | OCR |
| tesseract-ocr-eng | 英文 OCR 数据 | OCR |
| ffmpeg | 包含 ffprobe；编码、剪辑、抽帧、音轨与字幕 | 视频、音频、HTML 视频 |
| sox | 音频处理 | 语音、媒体 |
| imagemagick | 图片与媒体格式辅助处理 | 图片、媒体 |
| espeak-ng | 无远程服务时的基础机械配音 | speech |
| libgomp1 | 本地 CPU 推理的 OpenMP 运行库 | faster-whisper 等 |
| graphviz | dot 渲染与 PlantUML 布局 | diagrams |
| plantuml | UML 与时序图；复用 Java | diagrams |

Playwright 的操作系统动态库另按匹配版本的浏览器依赖安装；不能仅靠以上 APT 列表推断浏览器可运行。

APT 包必须在仓库固定源/snapshot 中确认可解析。构建记录保存最终版本；不能在未检查 snapshot 的情况下声称系统包已固定或可安装。PlantUML 版本需覆盖测试用例，若系统版本不足，再选单个固定校验的上游 jar，不并存两套默认入口。

### Python 直接依赖

| pip 包 | 用途 | 首版要求 |
| --- | --- | --- |
| python-docx | Word 创建、读取与修改 | 必装 |
| openpyxl | Excel 公式、表格与原生图表 | 必装 |
| python-pptx | 可编辑 PPT、图表与备注 | 必装 |
| pypdf | PDF 页面读写、合并 | 必装 |
| pdfplumber | PDF 文字与表格提取 | 必装 |
| reportlab | 创建 PDF | 必装 |
| Pillow | 缩放、裁切、透明度和长图分块 | 必装 |
| numpy | 数值处理与媒体推理依赖 | 必装，显式声明 |
| pandas | 清洗、分组、合并和分析 | 必装 |
| matplotlib | 静态 PNG/SVG/PDF 图表 | 必装 |
| requests | API、SEO 与一般 HTTP 请求 | 必装 |
| playwright | 浏览器与 HTML 逐帧渲染 | 必装 |
| faster-whisper | 本地语音转写与时间戳 | 必装 |

不为旧包装器预装 mcp、httpx、defusedxml 等直接依赖。若新的 Skill 脚本或选定库实际需要，应按真实调用加入；文件解码已确认需要兼容的 av，首批实现显式固定为 16.0.1；其他传递依赖必须进入锁定结果，不应因为没有列为直接依赖而手工排除。

版本选择必须支持 Python 3.13，并检查 amd64/arm64 的 wheel 或明确构建路径。实现阶段锁定直接和传递依赖，保存来源和校验信息。浏览器驱动与浏览器制品须成对锁定。当前不虚构 Pandas/Matplotlib 或其他包的已验证版本。

### 独立 CLI 和资源

| 项目 | 安装要求 | 验收重点 |
| --- | --- | --- |
| GitHub CLI gh | 固定版本的官方制品或受控软件源 | 路径、版本和公开 API 读取；账号凭据独立注入 |
| Mermaid CLI mmdc | 固定 npm 版本及依赖锁定 | 复用预装 Chromium，显式配置 executablePath，不会话内下载浏览器 |
| D2 | 按架构固定制品与 SHA-256 | SVG 和 PNG 均能生成 |
| Typst | 按架构固定制品与 SHA-256 | 中文/数学 PDF，本地模板不依赖在线包下载 |
| Chromium headless shell | 与 Playwright 匹配；含 Linux 动态库 | 实际启动、表单、截图、下载和关闭 |
| Whisper Small 模型 | 固定模型 revision 和文件 SHA-256 | 包括模型、配置、tokenizer 和 vocabulary；离线加载和中英文转写 |

浏览器路径统一为 /opt/oma-tools-browsers，模型路径统一为 /opt/oma-models/whisper-small。实际执行用户必须可读可执行；临时缓存与输出目录可写。相关环境变量与 Skill 的资源检测路径一起固定。

## 可选项：暂不加入默认镜像

| 可选包或能力 | 加入条件 |
| --- | --- |
| seaborn | 需要大量统计绘图，Matplotlib 原生路径不够便捷时 |
| scipy、scikit-learn | 有明确统计检验、优化或机器学习任务 |
| opencv-python | 有 Pillow/FFmpeg 无法覆盖的计算机视觉处理任务 |
| beautifulsoup4 | 网页结构提取脚本实际需要 |
| PyMuPDF、pdf2image、pytesseract、XlsxWriter | 已有 PDF/图片/Excel 库或 CLI 无法满足具体任务时，避免为同一入口预装多套包装库 |
| tesseract-ocr-chi-tra 等 | 有繁体或其他语言 OCR 输入 |
| rclone 通用 CLI | 有外部同步目标；不要将其与既有 rclone-filestore 混为一个二进制 |
| gopls、terraform-ls | 开发 Agent 明确需要语言服务 |
| gws / Tailscale | 已定义连接器、账户、网络与运行权限，单独验收 |
| GPU/CUDA、本地图片生成大模型 | 有明确硬件与模型部署要求，不纳入通用 CPU 工具镜像 |

## 自有 CLI 与现有 Skill 的迁移

默认优先让 Skill 使用成熟 CLI 与 Python 库。新镜像不因为旧 Skill 引用了命令就复制旧分支的 oma-tools/oma-slides 包装器。

现有 26 个 Skill 的兼容性要求必须逐项处理：

- speech 已改为随 Skill 分发的轻量转写/字幕脚本，直接调用 faster-whisper 和 FFmpeg，并保持参数与产物可检查。
- image-generation 已提供随 Skill 分发的显式 HTTP 调用脚本和服务合同实现。未配置 provider 之前标记待配置，不能只删除依赖检查后声称可用。
- pptx 中 oma-slides 属于可选项目式路径。首版普通 PPTX 由 python-pptx 完成；模板、逐页检查、备注写入和最终附件规则由 Skill 及随包脚本组织。项目式版本管理/present/export 命令不算首版已恢复能力。
- video-editing、diagrams 的 oma-tools 便捷入口改为 FFmpeg、Graphviz、mmdc、D2、PlantUML 的真实命令。
- Skill 的 runtime.json、说明、脚本和验收必须同步更新。现有依赖声明无修改前，不能声称原来的 26 个 ZIP 可直接无缝运行。
- 若确认某项重复代码确实需要跨 Skill 共享，再增加小型 CLI；先定义实际输入、输出和失败行为，不预先建设工具服务平台。

这些修改发生在 Skill 源码与分发包，不等于修改 OMA 的生产执行引擎。Skill 仍通过 OMA 上传和版本绑定。

## 26 个 Skill 的覆盖与额外条件

| Skill | 本地路径 | 首版之外的条件或迁移 |
| --- | --- | --- |
| docx | python-docx、Writer、Poppler、字体 | 保留来源核对与渲染检查 |
| xlsx | openpyxl、Calc、Poppler | 保留公式并真实重算 |
| pptx | python-pptx、Impress、Poppler | 备注 XML、原生图表和逐页检查；旧项目式 CLI 不在首版 |
| pdf | pypdf/pdfplumber/ReportLab、Poppler、qpdf | OCR/转换按实际输入格式选择 |
| image-processing | Pillow、ImageMagick | 真实像素结果检查 |
| video-editing | FFmpeg/ffprobe | 已迁移为原生命令；不含视频生成 |
| html-video | Playwright、Chromium、FFmpeg | 自有 renderAt 流程，不保证兼容任意 HTML 音视频工程 |
| data-analysis | Pandas、NumPy、openpyxl | 按业务口径独立核对结果 |
| data-quality | Python 标准库与 Skill 脚本 | 无额外专用大包 |
| charts | Matplotlib、openpyxl、Calc、字体 | 中文标签、图例、尺度和可编辑图表核对 |
| diagrams | Graphviz、mmdc、D2、PlantUML | 逐引擎真实渲染 |
| deep-research | curl、浏览器与来源读取 | 搜索能力、目标站点和网络另外验证 |
| finance-research | Pandas、openpyxl | 实时行情另配；用户材料可本地分析 |
| github-research | git、curl、gh | 私有和写权限单独授权；安装 gh 不自动登录 |
| webdev | 已有 Node/npm，加浏览器 | 项目依赖随项目安装；公网部署另配 |
| webdev-seo | requests、浏览器 | 目标站点网络；不保证搜索排名 |
| browser | Playwright、Chromium | 独立沙箱登录态，不继承用户桌面浏览器 |
| speech | faster-whisper、固定模型、FFmpeg、eSpeak | 随包原生转写入口；自然语音服务另配 |
| read-special-images | Pillow、Tesseract | Agent 看图通道和支持图像的模型 |
| typst-pdf | Typst、字体、Poppler | 离线本地模板测试 |
| workflow-composer | Python/CLI 与 Skill 脚本 | 不提供持久调度 |
| skill-authoring | Python/归档/文件工具 | 安装发布仍通过 OMA |
| api-integration | requests、curl | 目标 API 文档、网络和授权 |
| backup-restoration | Python 标准库与归档脚本 | 普通文件备份，不是数据库一致性快照 |
| image-generation | requests 与 provider 调用脚本 | 入口迁移、服务、模型、凭据和看图验收，待配置 |
| music-prompts | 文字流程 | 不承诺生成音乐、音效 |

## 远程服务与平台能力

以下条件不能通过安装依赖获得，也不作为镜像构建成功的证明：

- 图片生成/语义编辑、自然配音、远程转写、视频视觉理解：独立 provider、模型、网络、凭据与调用验收。
- 文生视频、音乐、音效、3D：需要另外定义适配与服务，不因存在 FFmpeg 就认为支持。
- Google Workspace、实时金融数据、私有 GitHub、业务 API、公开部署：各自的账号、连接器和网络。
- WebSearch/WebFetch：确认 OMA 的实际 Agent 工具配置和服务，不将 curl 当成搜索引擎。
- 定时触发、跨会话检索、长期后台进程：属于平台能力，不由 Skill 的本地脚本冒充。

## 验收标准

| 层次 | 必须验证的结果 |
| --- | --- |
| 来源与构建 | 版本、SHA-256、APT snapshot、架构参数一致；干净 checkout 的来源检查和 Dockerfile 检查通过 |
| 基础运行时 | 原有运行时合同、envd health、Manager、文件挂载保持可用；不能仅跑新增工具测试 |
| 命令和导入 | 三种既有执行账号按实际权限运行；使用同一 Python；工具可执行，库可导入，字体缓存可见 |
| Office | 中文 DOCX/PPTX 渲染；XLSX 保留公式且重算；PPT 备注正文实际写入导出 XML；PDF 可提取中文 |
| 图像和图表 | 缩放/透明度/长图分块；中文统计图与各关系图引擎真实渲染 |
| 浏览器 | 本地表单、截图、上传下载、异常退出和清理；不依赖公网探活代替浏览器运行 |
| 媒体 | 真实短视频剪辑、抽帧、字幕和 MP4 解码；HTML 动画输出非空逐帧视频 |
| 本地语音 | 模型离线加载；中英文音频转写与 SRT 时间轴检查；eSpeak 生成可解码音频 |
| GitHub | gh 与 git 基础操作；公开读取和私有/写操作分开报告，未配置凭据不能标记授权操作通过 |
| Skill | 更新后的每个独立 ZIP 依赖检查与随包脚本测试；禁止只改依赖检查掩盖旧命令不可用 |
| OMA 链路 | Brain 与 Claude Sandbox 新 Session 实际读取 Skill、执行工具，Files 下载字节与挂载产物一致 |
| 交付与清理 | 默认 PPT 任务只交付一个 PPTX；源码/预览留工作目录；失败和成功均无遗留运行资源 |

工具层体积单独报告；保留 main 的既有体积报告机制，不为了掩盖增加依赖直接提高目标值。Whisper 和 Chromium 对体积影响较大，构建前记录基线，构建后记录增量。模型识别质量与复杂排版质量另以任务样本评估。

## 后续实现顺序

1. 本清单定稿后，先接入 Word/Excel/PPT/PDF、字体、数据分析和 gh，执行对应真实产物测试。
2. 加入浏览器、图片/OCR、关系图和 Typst，验证无头浏览器和中文渲染。
3. 加入媒体、本地语音与固定权重，验证 CPU 执行和离线模型加载。
4. 迁移上述 Skill 的旧 CLI 引用，重新打包并检查每个包。
5. 执行 OMA 真实会话验收；最终镜像记录源码 SHA、依赖锁定结果和架构，通过后再进入推送/云端模板发布流程。

上述实现顺序保留为设计记录；默认工具安装和 Skill 迁移已完成本地实现，云端模板发布仍需单独执行。
