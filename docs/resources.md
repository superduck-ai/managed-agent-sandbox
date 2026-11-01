# 成熟工具、Chromium 与 Whisper Small 预装

在 main 基础上增加 toolkit stage，预装办公、图片/OCR、媒体、图表、浏览器与本地转写依赖。Skill 流程与轻量脚本单独上传，不写入镜像。

系统包包括 LibreOffice Writer/Calc/Impress、Poppler 与 poppler-data、qpdf、Pandoc、Noto CJK/DejaVu 字体、Tesseract 中英文数据、FFmpeg、SoX、ImageMagick、eSpeak NG、Graphviz 和 PlantUML。poppler-data 用于中文 CID PDF 的 CMap 解析，不能只安装 poppler-utils。

gh 2.103.3、D2 0.9.0、Typst 0.15.0 的双架构官方制品 URL 与 SHA-256 在 versions.env；Mermaid CLI 11.12.0 使用 npm 锁文件。Python 包含 python-docx、openpyxl、python-pptx、pypdf、pdfplumber、ReportLab、Pillow、NumPy、Pandas、Matplotlib、requests，版本与哈希见 versions.env 和 requirements.lock。

## 固定内容

- Playwright 1.56.0，匹配 Chromium headless shell revision 1194；amd64 和 arm64 的归档 URL、SHA-256 写在 versions.env。
- faster-whisper 1.2.0 、文件解码使用的 PyAV 16.0.1 与运行导入所需的 requests 2.32.5，Whisper Small revision 536b0662742c02347bc0e980a01041f333bce120；模型、配置、tokenizer、vocabulary、README 的 SHA-256 写在 versions.env。
- Python 直接与传递依赖在 image/resources/requirements.lock 中固定版本和哈希；根 Dockerfile 使用 --require-hashes 和 --only-binary 安装。
- 浏览器、模型分别位于 /opt/oma-tools-browsers 和 /opt/oma-models/whisper-small，对应 PLAYWRIGHT_BROWSERS_PATH 与 OMA_LOCAL_ASR_MODEL。
- Docker ENV 与 /etc/profile.d/oma-toolkit.sh 同时提供资源路径；envd/Manager 清理环境后启动的登录 Shell 也必须能读取。
- 镜像保留基础运行时、Manager 和 ENTRYPOINT。此功能不启动额外 MCP，也不下载其他模型。

兼容性约束：faster-whisper 1.2.0 的实际导入需要 requests；PyAV 19 的 open 接口不接受该版本使用的 metadata_errors 参数，因此显式固定 PyAV 16.0.1。验证必须经过真实 WAV 解码，不能仅用 NumPy 内存音频替代文件转写。

## 安装与校验

沿用 README 中的 sandbox-image-build / sandbox-image-test 或 sandbox-image-verify。根 Dockerfile 默认最终阶段为 toolkit。浏览器 Linux 依赖由匹配版本的 Playwright 安装，libgomp1 为 CPU 模型运行提供 OpenMP 支撑。该阶段临时使用 main 固定的 Ubuntu snapshot，安装后恢复运行时镜像源；APT 和资源缓存由 BuildKit 保存，不进入最终层。

构建期下载按 SHA-256 校验；下载文件不匹配时失败，不覆盖已有文件。模型和浏览器使用 BuildKit 缓存，缓存命中仍先校验，不将未经验证的本地缓存当作有效制品。模型 README 和 Chromium 归档内附带文件随资源保留。更新资源时须同步驱动版本、归档和哈希，不单独替换浏览器。

uv 可用于重新解析依赖：从 versions.env 中的全部 Python 直接依赖版本生成临时输入，再用 uv pip compile --python-version 3.13 --universal --generate-hashes 生成锁文件。锁文件允许记录传递依赖；主机测试和镜像内验证检查直接依赖与 versions.env 一致。

## 真实运行验证

脚本为 /opt/oma-resources/verify.py，在禁网容器中测试：

1. 清空环境后分别以 root/user/claude 运行非登录和登录 Shell；实际通过默认路径启动 Chromium，并从固定路径离线加载 Whisper；登录 Shell 另核对两个环境变量。
2. 驱动版本与 Chromium revision 一致。
3. 无头浏览器启动，填写中文表单、点击按钮，确认 DOM 值，并生成有效 PNG。
4. 复验所有模型文件 SHA-256。
5. 从绝对路径以 local_files_only=True 加载 CPU int8 模型，并通过 WAV 文件解码完成一秒静音的推理迭代。

主构建脚本的 test-image 在原有基础运行时合同之后自动执行该检查。也可以手动执行：

```sh
docker run --rm --network none -e HF_HUB_OFFLINE=1 \
  --entrypoint timeout YOUR_IMAGE 180s python3 /opt/oma-resources/verify.py
```

该检查证明模型离线加载及推理通路，不是中文/英文识别准确率或字幕质量验收。没有新增统一工具 CLI 或办公 MCP。speech Skill 随包转写脚本直接调用 faster-whisper；完整 Skill 工作流另通过 OMA 仓库的打包验收和 chat.office 检查。镜像内资源检查不能替代全部 Skill 验收。

每个发布架构应分别完成完整构建与运行验证。宿主为 arm64 时的本地验证不能替代 amd64 验收。证据和局部验证镜像保存在仓库之外，不应将隔离资源层探测当成根 Dockerfile 全量构建通过。

## Agent 运行时路径

默认的 ~/.cache/ms-playwright 或 Hugging Face 缓存为空，不代表预装资源缺失。浏览器位于 /opt/oma-tools-browsers，模型位于 /opt/oma-models/whisper-small。Playwright 读取 PLAYWRIGHT_BROWSERS_PATH；faster-whisper 应将 OMA_LOCAL_ASR_MODEL 的绝对路径传给 WhisperModel，并设置 local_files_only=True。仅传 "small" 仍可能触发 Hugging Face 下载。不要用会话内重新下载掩盖登录 Shell 的路径缺失。

基础 Bash 工具可能清理 Docker ENV 且不加载登录脚本，因此为 root/user/claude 的标准 Playwright 缓存目录登记 Chromium revision 的符号链接。浏览器文件仍只有 /opt 下的一份，不覆盖不同的已有缓存。Whisper 使用预装目录的绝对路径与 local_files_only=True，保持固定模型版本，不依赖 HF 默认缓存或按名称在线解析。
