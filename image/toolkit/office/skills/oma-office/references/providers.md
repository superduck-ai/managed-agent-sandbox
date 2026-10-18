# 外部模型与生成服务

先调用 tools_status。provider 状态 configured_unverified 只说明配置和环境变量存在，需真实调用与产物检查后才算可用。缺少配置时报告对应 provider；不要读取、回显或要求用户在聊天里粘贴密钥。

管理员通过 OMA_TOOLS_CONFIG 指定私有 JSON 配置，按 asr、vision、image、speech 分别设置 base_url、model、api_key_env，并将对应秘密注入 workbench 服务进程的环境。默认没有外部服务，也不会自动把 Agent 的聊天模型 Vault 凭据转给该服务；仅给 Claude 子进程设置环境变量不等于 supervisor 提前启动的 MCP 进程也已取得变量。真实部署需单独验证密钥注入。只有管理员明确配置的服务会收到媒体数据。

| 工具 | 所需合同 |
| --- | --- |
| speech_transcribe engine=remote | audio/transcriptions，verbose_json 和 segment/word 时间戳；模型不支持这些字段时明确失败 |
| speech_synthesize engine=remote | audio/speech；使用该服务支持的 voice ID，注明音频是合成的 |
| image_generate_or_edit | images/generations 或 images/edits，返回 data[0].b64_json；输入图片进入该远程服务 |
| video_analyze | chat/completions 图片消息；带时间戳的稀疏抽帧，可选附本地语音转写，输出 JSON |

视频理解基于最多 16 张采样帧，不代表模型逐帧看完视频；说明采样范围与不确定性。用户要求的动作细节、声音、时序或画外内容不在证据中时，不补造结论。长视频可分段分析并保留时间索引。

生成图片可保存到 Slides 项目 assets 中，编辑 XML 引用，再 present、查看预览、导出。它没有伪装成 Manus 的 generate_image_slide。

视频生成、音乐、音效、3D、邮件短信电话与平台 HTTP cron 没有通用可移植合同，当前没有可用实现。只有取得供应商接口、鉴权和实际验收后再声明支持。OpenAI 原 Sora/Videos API 已下线，不使用历史文档中的已停用接口作为默认实现。
