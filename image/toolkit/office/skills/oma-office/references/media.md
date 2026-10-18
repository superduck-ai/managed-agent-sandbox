# 本地媒体与语音

使用 workbench MCP。先 `media_probe(input_path)` 查看时长、流、编码与尺寸，再按需求调用 `media_transform(edit)`。输入必须是 `/mnt/user-data` 下的本地文件，输出放 `/mnt/user-data/outputs`，已有输出会拒绝覆盖。远程 URL 先通过用户授权的下载路径取得本地文件。

`edit` 至少包含 action、input_path、output_path。支持：

| action | 参数与输出 |
| --- | --- |
| clip | start、duration；MP4/MOV/MKV/WebM |
| transcode | width、crf；CRF 越高质量越低，先 probe 再决定压缩参数 |
| audio | 提取单声道 16k 音频，输出 WAV/MP3/M4A/FLAC |
| frames | fps、max_frames、width；输出包含 PNG 与 frames.json 的 ZIP |
| gif | fps、duration、width；最多 30 秒 |
| subtitle | subtitle_path 指向本地 SRT/ASS/VTT；烧录中文字幕后交付视频 |
| concat | additional_paths，输入全部带音频或全部无音频；统一到 640×360、25fps，保留存在的音频 |
| mix | additional_paths，混合多路音频 |

单个输入文件最大 500 MiB，时长最多 30 分钟，处理阶段受超时约束。长素材先分段；抽帧为采样，不等于看完全部运动细节。

`speech_transcribe(input_path, output_path, engine="local", language="zh", word_timestamps=true, subtitle_path="...srt", text_path="...txt")` 使用镜像内固定的多语言 Whisper 模型和 CPU，一次生成 JSON、SRT、TXT。三种文件来自同一次实际识别结果，优先使用工具直接生成的 TXT，避免手工复制模型识别文本。对口音、噪声、专业词、多人重叠和静音幻觉需人工检查；没有识别结果时如实报告，不能编造转写。需要更高质量时按 providers.md 配置远程转写或由管理员更换本地模型路径。

`speech_synthesize(text, output_path, engine="local", voice="en-us")` 使用 eSpeak 输出 WAV；中文可用 cmn。它是机械合成音，不冒充自然人录音；自然配音使用显式配置的远程 provider。

本地 CLI 与 MCP 共享实现：`oma-tools probe INPUT`、`oma-tools transform --file EDIT.json`、`oma-tools transcribe INPUT OUTPUT.json --language zh --words --srt OUTPUT.srt`、`oma-tools speech TEXT OUTPUT.wav --voice cmn`。优先把复杂 JSON 写成文件，避免 shell 引号错误。

完成标准：probe 验证编码、尺寸、时长和音轨，实际播放或查看代表性帧，检查字幕与转写，再提供真实输出路径的下载链接。工具成功和 Files catalog 写回是不同阶段。
