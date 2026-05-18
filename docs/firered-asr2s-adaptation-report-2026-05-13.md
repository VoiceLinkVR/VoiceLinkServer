# FireRedASR2S 适配报告（2026-05-13 至 2026-05-14）

## 目标

- 在原语音识别接口增加 ASR 引擎选择参数。
- 默认中文 ASR 引擎切换为 FireRedASR2S。
- 保留 SenseVoice，允许调用方按请求切换。
- FireRed 和 SenseVoice 定位一致：只用于中文识别主链路。
- 中文主链路不对外开放 Whisper / Whisper-TRT。
- 非中文 `sourceLanguage` 请求必须全链路使用 Whisper，包括中文过滤识别和正式源语言识别。
- `/whisper/translations` 是音频翻译入口，不属于中文主识别链路，收敛为 Whisper-only。
- 先部署 192.168.2.53 测试环境，再部署 192.168.2.81 生产环境。

## 实现

- 新增配置：
  - `ASR_DEFAULT_ENGINE=firered`
  - `FIRERED_ASR_HOST=192.168.2.81`
  - `FIRERED_ASR_PORT=8008`
  - `FIRERED_ASR_TIMEOUT=120`
- 新增请求参数：
  - `engine`
  - 兼容别名 `asrEngine`
  - 中文主链路不传时使用 `ASR_DEFAULT_ENGINE`
- 新增 ASR 引擎分发：
  - `firered` / `fireredasr2s`：调用 FireRed 原生 `/transcribe`。
  - `sensevoice`：调用 SenseVoice `/v1/audio/transcriptions`。
  - `whisper`：保留 faster-whisper/OpenAI 兼容路径，仅用于内部或非中文链路。
  - `whisper-trt`：保留 WhisperLive-TRT 路径，仅用于内部或非中文链路。
- 路由层新增三类解析：
  - 中文主链路：只允许 `firered`、`sensevoice`。
  - 非中文链路：默认、`firered`、`sensevoice` 均解析为 Whisper 系列；显式 `whisper` / `whisper-trt` 使用对应 Whisper 引擎。
  - 音频翻译入口 `/whisper/translations`：只允许 Whisper 系列；默认使用 Whisper。
- 站点配置校验同步收敛：
  - `ASR_DEFAULT_ENGINE` 只允许 FireRed/SenseVoice 相关别名。
- 增加日志：
  - 记录 requested/effective ASR engine。
  - 非中文链路回落时记录 `non-Chinese source uses full Whisper ASR chain` 或 `default Whisper ASR chain`。

## 覆盖接口

- 中文主识别链路：
  - `/v2/api/whisper/transcriptions`
  - `/v2/api/func/translateToEnglish`
  - `/v2/api/func/translateToOtherLanguage`
  - `/v2/api/func/multitranslateToOtherLanguage` 的 `sourceLanguage=zh`
  - `/v2/api/whisper/multitranscription` 的 `sourceLanguage=zh`
- 非中文识别链路：
  - `/v2/api/func/multitranslateToOtherLanguage` 的 `sourceLanguage!=zh`
  - `/v2/api/whisper/multitranscription` 的 `sourceLanguage!=zh`
- 音频翻译入口：
  - `/v2/api/whisper/translations`

## 验证

### 上游服务

- FireRedASR2S：`POST http://192.168.2.81:8008/transcribe`
  - `assets/hello_zh.wav` 返回 `{"text":"你好世界",...}`
- SenseVoice：`POST http://192.168.2.81:8800/v1/audio/transcriptions`
  - `assets/hello_zh.wav` 返回 `{"text":"你好，世界。"}`
- Whisper：`POST http://192.168.2.81:8000/v1/audio/transcriptions`
  - `assets/hello_en.wav` 返回 `{"text":"Helo, Speech."}`

### 本地和镜像检查

- 本地：`python -m compileall -q src` 通过。
- 53 镜像内：`python -m compileall -q .` 通过。
- 81 镜像内：`python -m compileall -q .` 通过。
- 路由解析用例通过：
  - 中文默认：`firered`
  - 中文显式 `sensevoice`：`sensevoice`
  - 中文显式 `whisper`：HTTP `400`
  - 非中文默认：`whisper`
  - 非中文显式 `firered`：`whisper`
  - 非中文显式 `whisper`：`whisper`
  - `/whisper/translations` 默认：`whisper`
  - `/whisper/translations` 显式 `firered`：HTTP `400`

### 53 测试环境

- 容器：`vrcls-prod-backend`
- 镜像：`voicelink-prod:firered-asr-20260514013322`
- 端口：`8980 -> 8980`
- `/v2/api/whisper/multitranscription`：
  - `sourceLanguage=zh` 默认引擎返回 `{"text":"你好世界"}`。
  - `sourceLanguage=zh&engine=sensevoice` 返回 `{"text":"你好，世界。"}`。
  - `sourceLanguage=zh&engine=whisper` 返回 HTTP `400`。
  - `sourceLanguage=en` 默认返回 `{"text":"Hello, speech."}`，日志显示中文过滤和英文识别均调用 Whisper。
  - `sourceLanguage=en&engine=firered` 返回 `{"text":"Hello, speech."}`，日志显示全链路 Whisper。
- `/v2/api/whisper/transcriptions`：
  - `engine=whisper` 返回 HTTP `400`。
- `/v2/api/func/multitranslateToOtherLanguage`：
  - `sourceLanguage=zh&targetLanguage=zh&engine=whisper` 返回 HTTP `400`。
  - `sourceLanguage=en&targetLanguage=en&engine=firered` 返回 `{"text":"Hello, speech.","translatedText":"Hello, speech.",...}`，日志显示全链路 Whisper。
- `/v2/api/whisper/translations`：
  - 默认返回 `{"text":"Hello, world."}`。
  - `engine=firered` 返回 HTTP `400`。
- 容器日志未发现 `ERROR` / `Traceback` / `Exception`。

### 81 生产环境

- 容器：`voicelink-backend`
- 镜像：`voicelink-backend:firered-asr-20260514013322`
- 端口：`18980 -> 8980`
- `/v2/api/whisper/multitranscription`：
  - `sourceLanguage=zh` 默认引擎返回 `{"text":"你好世界"}`。
  - `sourceLanguage=zh&engine=sensevoice` 返回 `{"text":"你好，世界。"}`。
  - `sourceLanguage=zh&engine=whisper` 返回 HTTP `400`。
  - `sourceLanguage=en` 默认返回 `{"text":"Hello, speech."}`。
  - `sourceLanguage=en&engine=firered` 返回 `{"text":"Hello, speech."}`。
- `/v2/api/whisper/transcriptions`：
  - `engine=whisper` 返回 HTTP `400`。
- `/v2/api/func/multitranslateToOtherLanguage`：
  - `sourceLanguage=zh&targetLanguage=zh&engine=whisper` 返回 HTTP `400`。
  - `sourceLanguage=en&targetLanguage=en&engine=firered` 返回 `{"text":"Hello, speech.","translatedText":"Hello, speech.",...}`。
- `/v2/api/whisper/translations`：
  - 默认返回 `{"text":"Hello, world."}`。
  - `engine=firered` 返回 HTTP `400`。
- 生产日志证据：
  - 中文默认链路调用 FireRed `192.168.2.81:8008/transcribe`。
  - 中文显式 SenseVoice 链路调用 `192.168.2.81:8800/v1/audio/transcriptions`。
  - 非中文默认链路记录 `non-Chinese source uses default Whisper ASR chain`。
  - 非中文显式 `engine=firered` 链路记录 `non-Chinese source uses full Whisper ASR chain: requested=firered effective=whisper`。
  - 非中文链路中文过滤和正式源语言识别均调用 `192.168.2.81:8000/v1/audio/transcriptions`。
- FireRedASR2S 容器 `fireredasr2-aed-trt` 处于 `healthy`。
- 后端容器日志未发现 `ERROR` / `Traceback` / `Exception`。

## 回滚点

- 53：
  - `vrcls-prod-backend-prev-firered-20260514013322`
  - `vrcls-prod-backend-prev-firered-20260514001610`
  - `vrcls-prod-backend-prev-firered-20260514000653`
  - `vrcls-prod-backend-prev-firered-20260513234404`
- 81：
  - `voicelink-backend-prev-firered-20260514013322`
  - `voicelink-backend-prev-firered-20260514001610`
  - `voicelink-backend-prev-firered-20260513234404`

## 注意

- 本次部署基于当前工作区构建，未回退工作区中已有未提交改动。
- 53 曾短暂部署过 `20260514000653` 镜像，该版本只做了 `language` 级兜底，会把非中文链路的中文过滤误路由到 FireRed；已由后续镜像修正。
- `20260514001610` 版本允许中文主链路显式使用 Whisper；已按最终口径由 `20260514013322` 修正为中文主链路 FireRed/SenseVoice-only。
- FireRedASR2S 使用原生 `/transcribe`，未走 OpenAI 兼容接口；后续增加中文 ASR 引擎时扩展中文主链路解析和底层分发即可。
