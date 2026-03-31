# VoiceLinkVR-server
(简体中文|[english](README.md))

这是一个支持 faster-whisper 与 WhisperLive-TRT 后端切换的 docker 服务端程序

既可以配合VoiceLinkVR的其他客户端如VRCHAT-OSC客户端[VRCLS](https://github.com/VoiceLinkVR/VRCLS)一起使用，也可以独立作为服务端使用

集成了一个非常简单的用户控制界面，项目依赖于[faster-whisper-server（现在叫speeches）](https://github.com/speaches-ai/speaches/tree/v0.6.0-rc.1)，并支持 WhisperLive-TRT REST 集成

## 部署方式

当前仓库统一使用单一 `compose.yaml` 作为部署入口。

镜像地址、端口、缓存目录、运行参数都通过 `.env` 配置；需要启用哪些可选服务，则通过 `COMPOSE_PROFILES` 控制。

### 快速开始

1. 复制 `.env.example` 为 `.env`
2. 按需修改你发布后的镜像地址
3. 设置 `COMPOSE_PROFILES`，决定本次要启动哪些服务
4. 让 `WHISPER_BACKEND` 与启用的后端 profile 保持一致

```powershell
cp .env.example .env
docker compose pull
docker compose up -d
```

### 常见 profile 组合

- `COMPOSE_PROFILES=redis,faster-whisper`
- `COMPOSE_PROFILES=redis,whisper-trt,gemma`
- `COMPOSE_PROFILES=redis,whisper-trt,gemma,sensevoice`

### 关键环境变量

- `VOICELINK_IMAGE`、`FASTER_WHISPER_IMAGE`、`WHISPER_TRT_IMAGE`、`GEMMA_TRANSLATE_IMAGE`、`SENSEVOICE_IMAGE`
- `WHISPER_BACKEND=faster-whisper` 或 `WHISPER_BACKEND=whisper-trt`
- `TRANSLATE_SERVICE_URL=http://gemma-translate:8080`
- 启用 Gemma 路径时，将 `GEMMA_TRANSLATE_ENABLED=True`
- `GEMMA_TRANSLATE_API_MODE=completion`（必选），当前 Gemma 部署按 `llama-server --no-jinja` 走 `POST /completion`
- Gemma/文本翻译请求不再支持 `sourceLanguage=auto`
- 默认把 `WHISPER_TRT_REST_HOST_PORT` 设为 `18100`，避免和 Faster-Whisper 的 `8000` 端口冲突

等待一切运行就绪后请访问：`http://{服务器ip}:8980/ui/login`

首次登陆时输入的用户名和密码将作为默认管理员的账户和密码，请妥善保管。

如果忘记管理员账户和密码请自行修改数据库文件，在 docker 中该文件位于`/usr/src/app/data/db/users.db`

## 服务接口文档

### 管理界面
- 管理界面入口：/ui/login
- 管理界面主页：/ui/manage_users
- 管理界面退出 /ui/logout
- 管理界面删除用户 /ui/deleteUser

### 控制api
除管理员注册接口在启动后无用户时不需要token外，都需要增加token

#### 注册管理员接口

该接口在无用户信息时会自动将第一个用户作为管理员，其他时间将验证token

方法：POST
url: /manageapi/registerAdmin
传入参数：
```json
{
    "username":"",
    "password":""
}
```
响应格式：
```json
{"message": "AdminUser created successfully"}
```

#### 更换用户密码接口
方法：POST
url:/manageapi/changePassword
传入参数：
```json
{
    "username":"",
    "password":""
}
```
响应格式：
```json
{"message": "user:{username}, Password changed successfully"}
```

#### 注册用户接口（简单版）
方法：POST
url:/manageapi/register
传入参数：
```json
{
    "username":"",
    "password":""
}
```
响应格式：
```json
{"message": "User created successfully"}
```

#### 添加用户接口（完整版）
方法：POST
url:/manageapi/addUser
传入参数：
```json
{
    "username": "",
    "password": "",
    "is_admin": false,
    "is_active": true,
    "limit_rule": "10000/day;1000/hour",
    "expiration_date": "2025-12-31T23:59:59"
}
```
字段说明：
- `username`: 用户名（必填，3-50字符）
- `password`: 密码（必填，最少6字符）
- `is_admin`: 是否为管理员（可选，默认false）
- `is_active`: 是否激活（可选，默认true）
- `limit_rule`: 限速规则（可选）
- `expiration_date`: 过期时间（可选，ISO格式）

响应格式：
```json
{"message": "User created successfully", "user": {"username": ""}}
```

#### 更新用户接口
方法：POST
url:/manageapi/updateUser
传入参数：
```json
{
    "username": "",
    "password": "",
    "is_admin": false,
    "is_active": true,
    "limit_rule": "10000/day;1000/hour",
    "expiration_date": "2025-12-31T23:59:59"
}
```
字段说明：
- `username`: 用户名（必填，用于定位要更新的用户）
- `password`: 新密码（可选，不传则不修改）
- `is_admin`: 是否为管理员（可选，不传则不修改）
- `is_active`: 是否激活（可选，不传则不修改）
- `limit_rule`: 限速规则（可选，不传则不修改）
- `expiration_date`: 过期时间（可选，不传则不修改）

响应格式：
```json
{"message": "user:{username}, updated successfully"}
```

#### 删除用户接口
方法：POST
url:/manageapi/deleteUser
传入参数：
```json
{
    "username":""
}
```
响应格式：
```json
{"message": "User deleted successfully"}
```

### 调用api

#### 登陆获取token
方法：POST
url:/api/login
```json
{
    "username":"",
    "password":""
}
```
响应格式：
```json
//成功
{"message": "Login successful", "access_token": "", "token_type": "bearer"}
//失败
{"detail": "Invalid credentials"}
```

#### 获取语音识别结果(中文)
方法：POST
url:/api/whisper/transcriptions
传入参数：
```
//form-data格式
'file':{文件二进制}
```

响应格式：
```json
{"text": ""}
```

#### 文字翻译接口
FastAPI 模式已移除 `/api/libreTranslate`，请使用 `/api/func/webtranslate`

请求体字段：`text`、`sourceLanguage`、`targetLanguage`、`targetLanguage2`、`targetLanguage3`。
`sourceLanguage=auto` 已不再支持。

#### 语音翻译接口(英语)
方法：POST
url:/api/func/translateToEnglish
传入参数：
```
//form-data格式
'file':{文件二进制}
```
响应格式：
```json
{"text": "","translatedText":""}
```

#### 语音翻译接口(其他语言)
方法：POST
url:/api/func/translateToOtherLanguage
传入参数：
```
//form-data格式
files["file"]:{文件二进制}
data={"targetLanguage":""}
//支持的目标语言格式请查看 `src/core/services.py` 中的支持语言列表
// 不支持 `targetLanguage=auto`
```
响应格式：
```json
{"text": "","translatedText":""}
```

## 环境变量说明

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| WHISPER_HOST | Whisper服务地址 | localhost |
| WHISPER_PORT | Whisper服务端口 | 8000 |
| WHISPER_BACKEND | Whisper后端选择（`faster-whisper` / `whisper-trt`） | faster-whisper |
| WHISPER_TRT_HOST | WhisperLive-TRT REST地址 | localhost |
| WHISPER_TRT_PORT | WhisperLive-TRT REST端口 | 8000 |
| SENSEVOICE_HOST | SenseVoice服务地址 | localhost |
| SENSEVOICE_PORT | SenseVoice服务端口 | 8800 |
| LIMIT_ENABLE | 是否启用限速 | False |
| LIMITER_REDIS_URL | Redis连接URL | - |
| TRANSLATOR_SERVICES_LIST | 翻译服务列表 | bing,modernMt,cloudTranslation |
| UPDATE_PUBLIC_BASE_URL | 更新资源对外访问基础URL（可选） | - |
| UPDATE_STATIC_ROOT | 热更新静态资源根目录 | src/data/update/files |
| UPDATE_MANIFEST_PATH | 应用更新manifest路径 | src/data/update/update_manifest.json |
| MODEL_MANIFEST_PATH | 模型目录manifest路径 | src/data/update/models_manifest.json |
| TRANSLATION_PROFILE_MANIFEST_PATH | 翻译能力profile manifest路径 | src/data/update/translation_profile_manifest.json |
| TRANSLATOR_RUNTIME_MANIFEST_PATH | 翻译运行时manifest路径 | src/data/update/translator_runtime_manifest.json |
| TRANSLATION_CAPABILITIES_CACHE_SECONDS | 翻译能力缓存秒数 | 1800 |
| TRANSLATION_CAPABILITY_TIMEOUT | 单引擎语言探测超时（秒） | 4.0 |
| ENABLE_WEB_TRANSLATORS | 启用在线翻译服务 | True |
| JWT_SECRET_KEY | JWT密钥（生产环境请修改） | voicelinkvr-secret-key |
| SQL_PATH | 数据库连接字符串 | sqlite:///data/db/users.db |

## 完整环境变量清单

上面的表格只是简表。下面这一节把当前 FastAPI 服务、`run.py`、`.env.example` 和 `compose.yaml` 里涉及到的环境变量都整理出来了。

### 生效顺序

- 应用运行时参数由 `src/core/config.py` 从系统环境变量和 `.env` 读取
- 显式传入的系统环境变量优先级高于 `.env`
- `.env` 高于代码里的默认值
- Docker Compose 也会读取 `.env`，用于 `compose.yaml` 里的变量替换

### FastAPI 应用环境变量

#### 语音识别与翻译

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WHISPER_HOST` | Faster-Whisper / OpenAI 兼容 Whisper 服务地址 | `127.0.0.1` |
| `WHISPER_PORT` | Faster-Whisper / OpenAI 兼容 Whisper 服务端口 | `8000` |
| `WHISPER_BACKEND` | 语音识别后端选择，支持 `faster-whisper` 和 `whisper-trt` | `faster-whisper` |
| `WHISPER_TRT_HOST` | WhisperLive-TRT REST 服务地址 | `127.0.0.1` |
| `WHISPER_TRT_PORT` | WhisperLive-TRT REST 服务端口 | `8000` |
| `SENSEVOICE_HOST` | SenseVoice 服务地址 | `127.0.0.1` |
| `SENSEVOICE_PORT` | SenseVoice 服务端口 | `8800` |
| `WHISPER_APIKEY` | 访问兼容 Whisper 服务时附带的 API Key | `something` |
| `WHISPER_MODEL` | 发送给 Whisper 兼容后端的模型名 | `Systran/faster-whisper-large-v3` |
| `FILTER_WEB_URL` | 远程过滤词配置地址 | `https://raw.githubusercontent.com/VoiceLinkVR/VoiceLinkServer/refs/heads/main/src/filter.json` |
| `ENABLE_WEB_TRANSLATORS` | 是否启用 `translators` 提供的在线翻译 | `False` |
| `TRANSLATOR_SERVICE` | 默认优先使用的单个翻译服务 | `alibaba` |
| `TRANSLATOR_SERVICES_LIST` | 翻译服务优先级列表 | `bing,iciba,alibaba,MyMemory,google` |
| `TRANSLATION_TIMEOUT` | 单次翻译请求超时时间，单位秒 | `1.5` |

#### Gemma 翻译集成

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `GEMMA_TRANSLATE_ENABLED` | 是否启用 TranslateGemma / OpenAI 兼容翻译链路 | `False` |
| `GEMMA_TRANSLATE_URL` | TranslateGemma 服务地址和端口 | `127.0.0.1:8080` |
| `GEMMA_TRANSLATE_API_MODE` | Gemma 接口模式（强制）：`completion` | `completion` |
| `GEMMA_TRANSLATE_MODEL` | 发送给 Gemma 服务的模型名 | `translategemma-12b-it-MP-GGUF` |
| `GEMMA_TRANSLATE_TIMEOUT` | Gemma 请求超时时间，单位秒 | `10.0` |
| `GEMMA_TRANSLATE_MAX_TOKENS` | Gemma 翻译时允许生成的最大 token 数 | `500` |
| `GEMMA_TRANSLATE_FALLBACK` | Gemma 失败后是否回退到其它翻译服务 | `True` |

#### 鉴权与会话

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `JWT_SECRET_KEY` | JWT 签名密钥，生产环境务必修改 | `wVLAF_13N6XL_QmP.DjkKsV` |
| `SESSION_SECRET_KEY` | Session 中间件密钥 | `wVddLAF_13dsdddN6XL_QmP.DjkKsV` |
| `JWT_ACCESS_TOKEN_EXPIRES_MINUTES` | Access Token 过期时间，单位分钟 | `10080` |
| `JWT_ALGORITHM` | JWT 签名算法 | `HS256` |

#### 数据库、限流与兼容模式

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SQL_PATH` | SQLAlchemy 数据库连接串 | `sqlite:///{project_root}/data/db/users.db` |
| `SQL_POOL_SIZE` | SQLAlchemy 连接池大小 | `50` |
| `SQL_MAX_OVERFLOW` | SQLAlchemy 连接池最大溢出连接数 | `150` |
| `SQL_POOL_RECYCLE` | SQLAlchemy 连接回收时间，单位秒 | `3600` |
| `LIMIT_ENABLE` | 是否启用请求限流 | `True` |
| `LEGACY_CLIENT_COMPAT` | 是否开启旧版客户端兼容模式 | `False` |
| `LIMIT_PUBLIC_TEST_USER` | 公共测试账号名，留空表示不配置 | 空 |
| `LIMITER_REDIS_URL` | 限流存储使用的 Redis 地址 | `redis://localhost:6379/0` |

#### 更新、TTS、日志与文本压缩

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TTS_URL` | 外部 TTS 服务地址 | 空 |
| `TTS_TOKEN` | 外部 TTS 服务令牌 | 空 |
| `LATEST_VERSION` | 提供给客户端的最新版本号 | 空 |
| `PACKAGE_BASE_URL` | 更新包下载基础地址 | 空 |
| `PACKAGE_TYPE` | 安装包后缀，例如 `.exe` | 空 |
| `UPDATE_PUBLIC_BASE_URL` | 生成更新下载链接时使用的公开基础地址 | 空 |
| `UPDATE_STATIC_ROOT` | 更新文件本地目录 | `{project_root}/data/update/files` |
| `UPDATE_MANIFEST_PATH` | 应用更新清单路径 | `{project_root}/data/update/update_manifest.json` |
| `MODEL_MANIFEST_PATH` | 模型清单路径 | `{project_root}/data/update/models_manifest.json` |
| `TRANSLATION_PROFILE_MANIFEST_PATH` | 翻译配置清单路径 | `{project_root}/data/update/translation_profile_manifest.json` |
| `TRANSLATOR_RUNTIME_MANIFEST_PATH` | 翻译运行时清单路径 | `{project_root}/data/update/translator_runtime_manifest.json` |
| `TRANSLATION_CAPABILITIES_CACHE_SECONDS` | 翻译能力缓存时间，单位秒 | `1800` |
| `TRANSLATION_CAPABILITY_TIMEOUT` | 单个翻译引擎能力探测超时，单位秒 | `4.0` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `ENABLE_TEXT_COMPRESSION` | 是否启用重复字符压缩 | `True` |
| `TEXT_COMPRESSION_MIN_REPEAT` | 触发文本压缩的最小重复次数 | `5` |

#### 爱发电相关

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AFDIAN_USER_ID` | 爱发电用户 ID | `db43f694299511ef821252540025c377` |
| `AFDIAN_TOKEN` | 爱发电 API Token | 空 |
| `AFDIAN_ORDER_CREATE_BASE_URL` | 爱发电付款页基础地址 | `https://ifdian.net/p/9c65d9cc617011ed81c352540025c377` |
| `AFDIAN_QUERY_ORDER_URL` | 爱发电订单查询接口 | `https://afdian.com/api/open/query-order` |
| `AFDIAN_CUSTOM_ID_KEY` | 自定义订单 ID 加密/签名密钥 | `change-me-to-32-byte-secret` |
| `AFDIAN_CUSTOM_PRICE` | 自定义套餐基础价格 | `5.0` |
| `AFDIAN_RENEW_DAYS` | 每个续费单位增加的天数 | `30` |
| `AFDIAN_BOOST_DAYS` | Boost 套餐计算使用的天数 | `30` |
| `AFDIAN_BOOST_PRICE_STEP` | Boost 升档价格步进 | `5.0` |
| `AFDIAN_BOOST_DAILY_INCREMENT` | 每次 Boost 增加的日额度 | `1500` |
| `AFDIAN_DEFAULT_LIMIT_RULE` | 付费用户默认额度规则 | `1500/day` |
| `AFDIAN_BOOST_LIMIT_RULE` | Boost 用户显式额度规则，留空表示自动推导 | 空 |
| `AFDIAN_QUERY_SCAN_PAGES` | 每次同步扫描的订单页数 | `3` |
| `AFDIAN_CUSTOM_ID_TTL_SECONDS` | 自定义订单 ID 有效期，单位秒 | `604800` |
| `AFDIAN_WEBHOOK_FORWARD_ENABLED` | 是否将爱发电 Webhook 透明转发给机器人插件 | `False` |
| `AFDIAN_WEBHOOK_FORWARD_URL` | 机器人插件 Webhook 地址 | 空 |
| `AFDIAN_WEBHOOK_FORWARD_TIMEOUT` | 转发超时时间，单位秒 | `10.0` |
| `AFDIAN_WEBHOOK_FORWARD_AUTHORIZATION` | 转发时附带的 Authorization 请求头 | 空 |

#### 爱发电 Webhook 与 Nginx 反代

现在推荐的链路是：

1. 爱发电 Webhook 只回调到服务端 `/api/user-center/afdian/webhook`
2. 服务端收到后会立即返回 `{"ec":200,"em":""}` 给爱发电
3. 随后服务端在后台异步执行本地激活，并把原始 Webhook JSON 透明转发给机器人插件

对应配置示例：

```env
AFDIAN_WEBHOOK_FORWARD_ENABLED=True
AFDIAN_WEBHOOK_FORWARD_URL=http://127.0.0.1:6500/
AFDIAN_WEBHOOK_FORWARD_TIMEOUT=10
AFDIAN_WEBHOOK_FORWARD_AUTHORIZATION=
```

Nginx 反代示例：

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.example;

    # VoiceLink 服务端
    location = /api/user-center/afdian/webhook {
        proxy_pass http://127.0.0.1:8980/api/user-center/afdian/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

部署时请对应设置：

- 爱发电后台 Webhook URL：`https://your-domain.example/api/user-center/afdian/webhook`
- 服务端转发目标 `AFDIAN_WEBHOOK_FORWARD_URL`：机器人插件的实际可达地址，例如 `http://127.0.0.1:6500/` 或内网地址

如果机器人插件和服务端在同一台机器上，不需要再给机器人插件额外配置公网 Nginx 入口。

#### Uvicorn 启动参数

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `UVICORN_WORKERS` | `run.py` 使用的 worker 数量 | `4` |
| `UVICORN_RELOAD` | 是否开启热重载，支持 `true/1/t` | `False` |

### Docker Compose 环境变量

这一组变量由 `compose.yaml` 使用，通常来源于根目录下的 `.env`。

#### Compose 控制与命名

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `COMPOSE_PROFILES` | 本次要启动的可选服务 profile | `redis,faster-whisper` |
| `COMPOSE_PROJECT_NAME` | Compose 项目名 | `voicelinkvr` |
| `REDIS_CONTAINER_NAME` | Redis 容器名覆盖值 | `redis` |
| `FASTER_WHISPER_CONTAINER_NAME` | Faster-Whisper 容器名覆盖值 | `faster-whisper` |
| `WHISPER_TRT_CONTAINER_NAME` | WhisperLive-TRT 容器名覆盖值 | `whisper-trt` |
| `SENSEVOICE_CONTAINER_NAME` | SenseVoice 容器名覆盖值 | `sensevoice` |
| `GEMMA_TRANSLATE_CONTAINER_NAME` | Gemma 容器名覆盖值 | `gemma-translate` |
| `VOICELINK_CONTAINER_NAME` | VoiceLink 主服务容器名覆盖值 | `voicelink` |

#### 镜像地址

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `VOICELINK_IMAGE` | VoiceLink 服务镜像 | `boyqiu0010/voice-link-vr-server:latest` |
| `REDIS_IMAGE` | Redis 镜像 | `redis:latest` |
| `FASTER_WHISPER_IMAGE` | Faster-Whisper 镜像 | `fedirz/faster-whisper-server:latest-cuda` |
| `WHISPER_TRT_IMAGE` | WhisperLive-TRT 镜像 | `ghcr.io/your-org/whisperlive-trt-gemma:latest` |
| `GEMMA_TRANSLATE_IMAGE` | TranslateGemma 镜像 | `ghcr.io/your-org/translategemma-llama-server:latest` |
| `SENSEVOICE_IMAGE` | SenseVoice 镜像 | `ghcr.io/your-org/sensevoice-oneapi:latest` |

#### 对外端口

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `VOICELINK_HOST_PORT` | VoiceLink 服务映射到宿主机的端口 | `8980` |
| `REDIS_HOST_PORT` | Redis 映射到宿主机的端口 | `6379` |
| `FASTER_WHISPER_HOST_PORT` | Faster-Whisper 映射到宿主机的端口 | `8000` |
| `WHISPER_TRT_WS_HOST_PORT` | WhisperLive-TRT WebSocket 映射端口 | `9090` |
| `WHISPER_TRT_REST_HOST_PORT` | WhisperLive-TRT REST 映射端口 | `18100` |
| `GEMMA_TRANSLATE_BIND_IP` | Gemma 对外绑定地址 | `127.0.0.1` |
| `GEMMA_TRANSLATE_HOST_PORT` | Gemma 映射到宿主机的端口 | `8080` |
| `SENSEVOICE_HOST_PORT` | SenseVoice 映射到宿主机的端口 | `8800` |

#### 数据卷与宿主机路径

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `REDIS_DATA_PATH` | 挂载到 Redis `/data` 的宿主机目录 | `./redisFiles/data` |
| `REDIS_CONFIG_PATH` | 挂载到 Redis 的配置文件路径 | `./redisFiles/redis.conf` |
| `VOICELINK_DATA_VOLUME` | 挂载到 `/usr/src/app/data` 的命名卷 | `voice-link-vr-server-local` |
| `WL_CACHE_DIR` | WhisperLive-TRT 缓存目录根路径 | `./.cache/whisper-trt` |
| `GEMMA_MODEL_ROOT` | 挂载到 `/models` 的宿主机模型目录 | `D:/models` |

#### Faster-Whisper 容器参数

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FASTER_WHISPER_TTL` | Faster-Whisper 容器内部模型/进程 TTL | `-1` |
| `FASTER_WHISPER_NUM_WORKERS` | Faster-Whisper 容器内部 worker 数 | `10` |
| `FASTER_WHISPER_CPU_THREADS` | Faster-Whisper 容器内部 CPU 线程数 | `40` |

#### WhisperLive-TRT 容器参数

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WHISPER_TRT_ENABLE_REST` | 是否启用 WhisperLive-TRT REST 接口 | `true` |
| `WHISPER_TRT_REST_PORT` | WhisperLive-TRT 容器内部 REST 端口 | `8000` |
| `WHISPER_TRT_MULTILINGUAL` | 是否启用多语言模式 | `true` |
| `TRANSLATE_SERVICE_URL` | WhisperLive-TRT 使用的翻译服务地址 | `http://gemma-translate:8080` |
| `AUTO_BUILD_TRT_MODEL` | 启动时是否自动构建 TensorRT 模型 | `true` |
| `TRT_BUILD_MODEL_NAME` | 构建 TRT 引擎时使用的 Whisper 模型名 | `large-v3` |
| `TRT_WEIGHT_ONLY_PRECISION` | TRT 构建使用的权重精度 | `float16` |
| `TRT_PY_SESSION` | 是否启用 Python TRT session 模式 | `false` |
| `TRT_BUILD_INFERENCE_PRECISION` | 构建时的推理精度 | `float16` |
| `TRT_BUILD_MAX_BATCH_SIZE` | 构建时最大 batch size | `1` |
| `TRT_BUILD_MAX_BEAM_WIDTH` | 构建时最大 beam width | `1` |
| `TRT_BUILD_ENCODER_MAX_INPUT_LEN` | 构建时编码器最大输入长度 | `3000` |
| `TRT_BUILD_ENCODER_MAX_SEQ_LEN` | 构建时编码器最大序列长度 | `3000` |
| `TRT_BUILD_DECODER_MAX_SEQ_LEN` | 构建时解码器最大序列长度 | `225` |
| `TRT_BUILD_DECODER_MAX_INPUT_LEN` | 构建时解码器最大输入长度 | `32` |
| `TRT_BUILD_DECODER_MAX_ENCODER_INPUT_LEN` | 构建时解码器可接受的最大编码器输入长度 | `3000` |
| `TRT_BUILD_DECODER_MAX_NUM_TOKENS` | 构建时解码器最大 token 数，可留空 | 空 |
| `TRT_RUNTIME_MAX_BATCH_SIZE` | 运行时最大 batch size | `1` |
| `TRT_RUNTIME_MAX_INPUT_LEN` | 运行时最大输入长度 | `3000` |
| `TRT_RUNTIME_MAX_OUTPUT_LEN` | 运行时最大输出长度 | `64` |
| `TRT_RUNTIME_MAX_BEAM_WIDTH` | 运行时最大 beam width | `1` |
| `TRT_KV_CACHE_FREE_GPU_MEMORY_FRACTION` | KV Cache 可使用的空闲显存比例 | `0.55` |
| `TRT_CROSS_KV_CACHE_FRACTION` | Cross KV Cache 占比 | `0.30` |
| `CONTEXT_FMHA_MODE` | Context FMHA 插件模式 | `auto` |
| `BERT_ATTENTION_PLUGIN_MODE` | BERT Attention 插件模式 | `auto` |
| `GPT_ATTENTION_PLUGIN_MODE` | GPT Attention 插件模式 | `auto` |
| `CORS_ORIGINS` | WhisperLive-TRT REST 允许的 CORS 来源 | `http://127.0.0.1:18100,http://localhost:18100` |

#### TranslateGemma 容器参数

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `GEMMA_MODEL_NAME` | Gemma 容器加载的模型名 | `translategemma-12b-it-MP-GGUF` |
| `LLAMA_N_GPU_LAYERS` | 放到 GPU 上的模型层数 | `-1` |
| `LLAMA_N_PARALLEL` | llama-server 并行请求数 | `2` |
| `LLAMA_N_CTX` | llama-server 上下文长度 | `3072` |
| `LLAMA_FLASH_ATTN` | 是否开启 flash attention | `on` |
| `LLAMA_EXTRA_ARGS` | 传给 llama-server 的额外启动参数 | 空 |

#### SenseVoice 容器参数

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SENSEVOICE_DEVICE_TYPE` | SenseVoice 使用的设备类型 | `cuda:0` |
| `SENSEVOICE_USE_ITN` | 是否启用逆文本归一化 | `True` |
| `SENSEVOICE_NCPU` | SenseVoice 使用的 CPU 数量 | `8` |
| `SENSEVOICE_VAD_ENABLE` | 是否启用 VAD | `False` |

### 兼容/历史遗留变量

仓库里还保留了旧版 Flask/Werkzeug 代码（`src/server.py`、`src/serverstart.py`）。这些变量不是当前 FastAPI 启动链路（`run.py` -> `src/main.py`）的必需项，但在老部署里仍可能看到。

| 变量 | 状态 | 说明 |
|------|------|------|
| `LIBRETRANSLATE_HOST` | 历史遗留 | 旧版 Flask 服务使用 |
| `LIBRETRANSLATE_PORT` | 历史遗留 | 旧版 Flask 服务使用 |
| `LIBRETRANSLATE_APIKEY` | 历史遗留 | 旧版 Flask 服务使用 |
| `FLASK_SECRET_KEY` | 历史遗留 | FastAPI 中已由 `SESSION_SECRET_KEY` 取代 |
| `JWT_ACCESS_TOKEN_EXPIRES` | 历史遗留 | FastAPI 中已由 `JWT_ACCESS_TOKEN_EXPIRES_MINUTES` 取代 |
| `SQLALCHEMY_POOL_SIZE` | 历史/Compose 兼容 | 旧命名，当前 FastAPI 配置项为 `SQL_POOL_SIZE` |
| `SQLALCHEMY_MAX_OVERFLOW` | 历史遗留 | 旧命名，当前 FastAPI 配置项为 `SQL_MAX_OVERFLOW` |
| `THREADS_NUM` | 历史遗留 | 旧版 Waitress 启动脚本使用 |
