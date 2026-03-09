# VoiceLinkVR-server
([简体中文](README-zh.md)|english)

This is a Docker server program that supports faster-whisper and WhisperLive-TRT backends

It can be used together with other clients of VoiceLinkVR, such as [VRCLS](https://github.com/VoiceLinkVR/VRCLS), or independently as a server

Integrated a very simple user control interface. The project relies on [faster-whisper-server (now called speaches)](https://github.com/speaches-ai/speaches/tree/v0.6.0-rc.1) and supports WhisperLive-TRT REST integration.

## Deployment

This repository uses a single `compose.yaml` entrypoint.

All image names, ports, cache paths, and runtime options are configured through `.env`, and optional services are selected through `COMPOSE_PROFILES`.

### Quick start

1. Copy `.env.example` to `.env`
2. Update the published image names you want to use
3. Set `COMPOSE_PROFILES` to match the services you want to start
4. Keep `WHISPER_BACKEND` aligned with the enabled backend profile

```powershell
cp .env.example .env
docker compose pull
docker compose up -d
```

### Common profile combinations

- `COMPOSE_PROFILES=redis,faster-whisper`
- `COMPOSE_PROFILES=redis,whisper-trt,gemma`
- `COMPOSE_PROFILES=redis,whisper-trt,gemma,sensevoice`

### Important environment variables

- `VOICELINK_IMAGE`, `FASTER_WHISPER_IMAGE`, `WHISPER_TRT_IMAGE`, `GEMMA_TRANSLATE_IMAGE`, `SENSEVOICE_IMAGE`
- `WHISPER_BACKEND=faster-whisper` or `WHISPER_BACKEND=whisper-trt`
- `TRANSLATE_SERVICE_URL=http://gemma-translate:8080`
- `GEMMA_TRANSLATE_ENABLED=True` when enabling the Gemma service path
- `GEMMA_TRANSLATE_API_MODE=openai` because the current Gemma deployment uses `llama-server` `POST /v1/chat/completions` only
- `sourceLanguage=auto` is no longer supported for Gemma/text translation requests
- `WHISPER_TRT_REST_HOST_PORT=18100` by default to avoid conflicting with Faster-Whisper on `8000`

After everything is ready to run, please visit: `http://{Server IP}:8980/ui/login`

The username and password entered during the first login will be used as the default administrator's account and password. Please keep them safe.

If you forget your administrator account and password, please modify the database file yourself. In Docker, this file can be found in `/usr/src/app/data/db/users.db`

## Service Interface Document

### Management Interface
- Management interface entrance: /ui/login
- Management interface homepage: /ui/manage_users
- Exit the management interface: /ui/logout
- Delete user in the management interface: /ui/deleteUser

### Control API
Except for the administrator registration interface, which does not require a token when there are no users after startup, tokens need to be added for all other interfaces

#### Register Administrator Interface

This interface will automatically designate the first user as the administrator when there is no user information, and the token will be verified at other times

Method: POST
URL: /manageapi/registerAdmin
Parameters:
```json
{
    "username":"",
    "password":""
}
```
Response:
```json
{"message": "AdminUser created successfully"}
```

#### Change User Password Interface
Method: POST
URL: /manageapi/changePassword
Parameters:
```json
{
    "username":"",
    "password":""
}
```
Response:
```json
{"message": "user:{username}, Password changed successfully"}
```

#### Register User Interface (Simple)
Method: POST
URL: /manageapi/register
Parameters:
```json
{
    "username":"",
    "password":""
}
```
Response:
```json
{"message": "User created successfully"}
```

#### Add User Interface (Full)
Method: POST
URL: /manageapi/addUser
Parameters:
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
Field Description:
- `username`: Username (required, 3-50 characters)
- `password`: Password (required, minimum 6 characters)
- `is_admin`: Is administrator (optional, default false)
- `is_active`: Is active (optional, default true)
- `limit_rule`: Rate limit rule (optional)
- `expiration_date`: Expiration date (optional, ISO format)

Response:
```json
{"message": "User created successfully", "user": {"username": ""}}
```

#### Update User Interface
Method: POST
URL: /manageapi/updateUser
Parameters:
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
Field Description:
- `username`: Username (required, used to locate the user to update)
- `password`: New password (optional, not modified if not provided)
- `is_admin`: Is administrator (optional, not modified if not provided)
- `is_active`: Is active (optional, not modified if not provided)
- `limit_rule`: Rate limit rule (optional, not modified if not provided)
- `expiration_date`: Expiration date (optional, not modified if not provided)

Response:
```json
{"message": "user:{username}, updated successfully"}
```

#### Delete User Interface
Method: POST
URL: /manageapi/deleteUser
Parameters:
```json
{
    "username":""
}
```
Response:
```json
{"message": "User deleted successfully"}
```

### Call API

#### Login to Obtain Token
Method: POST
URL: /api/login
```json
{
    "username":"",
    "password":""
}
```
Response:
```json
// Success
{"message": "Login successful", "access_token": "", "token_type": "bearer"}
// Failed
{"detail": "Invalid credentials"}
```

#### Get Speech Recognition Results (Chinese)
Method: POST
URL: /api/whisper/transcriptions
Parameters:
```
// form-data format
'file': {binary file}
```

Response:
```json
{"text": ""}
```

#### Text Translation Interface
Removed in FastAPI mode. Use `/api/func/webtranslate` for text translation.

Request body fields: `text`, `sourceLanguage`, `targetLanguage`, `targetLanguage2`, `targetLanguage3`.
`sourceLanguage=auto` is no longer supported.

#### Voice Translation Interface (English)
Method: POST
URL: /api/func/translateToEnglish
Parameters:
```
// form-data format
'file': {binary file}
```
Response:
```json
{"text": "","translatedText":""}
```

#### Voice Translation Interface (Other Languages)
Method: POST
URL: /api/func/translateToOtherLanguage
Parameters:
```
// form-data format
files["file"]: {binary file}
data={"targetLanguage":""}
// Please refer to this project's supported language list in `src/core/services.py`
// `targetLanguage=auto` is not supported
```
Response:
```json
{"text": "","translatedText":""}
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| WHISPER_HOST | Whisper service address | localhost |
| WHISPER_PORT | Whisper service port | 8000 |
| WHISPER_BACKEND | Whisper backend selector (`faster-whisper` / `whisper-trt`) | faster-whisper |
| WHISPER_TRT_HOST | WhisperLive-TRT REST host | localhost |
| WHISPER_TRT_PORT | WhisperLive-TRT REST port | 8000 |
| SENSEVOICE_HOST | SenseVoice service address | localhost |
| SENSEVOICE_PORT | SenseVoice service port | 8800 |
| LIMIT_ENABLE | Enable rate limiting | False |
| LIMITER_REDIS_URL | Redis connection URL | - |
| TRANSLATOR_SERVICES_LIST | Translation services list | bing,modernMt,cloudTranslation |
| UPDATE_PUBLIC_BASE_URL | Public base URL for update files (optional) | - |
| UPDATE_STATIC_ROOT | Static root folder for update assets | src/data/update/files |
| UPDATE_MANIFEST_PATH | App update manifest path | src/data/update/update_manifest.json |
| MODEL_MANIFEST_PATH | Model catalog manifest path | src/data/update/models_manifest.json |
| TRANSLATION_PROFILE_MANIFEST_PATH | Translation profile manifest path | src/data/update/translation_profile_manifest.json |
| TRANSLATOR_RUNTIME_MANIFEST_PATH | Translator runtime manifest path | src/data/update/translator_runtime_manifest.json |
| TRANSLATION_CAPABILITIES_CACHE_SECONDS | Translator capability cache TTL (seconds) | 1800 |
| TRANSLATION_CAPABILITY_TIMEOUT | Capability fetch timeout per engine (seconds) | 4.0 |
| ENABLE_WEB_TRANSLATORS | Enable online translation services | True |
| JWT_SECRET_KEY | JWT secret key (change in production) | voicelinkvr-secret-key |
| SQL_PATH | Database connection string | sqlite:///data/db/users.db |

## Complete Environment Variable Reference

The table above is a quick summary. The following section is the complete reference for the current FastAPI server, `run.py`, `.env.example`, and `compose.yaml`.

### Resolution order

- Application runtime variables are loaded by `src/core/config.py` from process environment and `.env`
- Explicit environment variables override values in `.env`
- Values in `.env` override code defaults in `src/core/config.py`
- Docker Compose also reads `.env` for variable interpolation in `compose.yaml`

### FastAPI application variables

#### Speech recognition and translation

| Variable | Description | Default |
|----------|-------------|---------|
| `WHISPER_HOST` | Faster-Whisper/OpenAI-compatible Whisper service host | `127.0.0.1` |
| `WHISPER_PORT` | Faster-Whisper/OpenAI-compatible Whisper service port | `8000` |
| `WHISPER_BACKEND` | Speech backend selector, supports `faster-whisper` and `whisper-trt` | `faster-whisper` |
| `WHISPER_TRT_HOST` | WhisperLive-TRT REST service host | `127.0.0.1` |
| `WHISPER_TRT_PORT` | WhisperLive-TRT REST service port | `8000` |
| `SENSEVOICE_HOST` | SenseVoice service host | `127.0.0.1` |
| `SENSEVOICE_PORT` | SenseVoice service port | `8800` |
| `WHISPER_APIKEY` | API key sent to compatible Whisper service when required | `something` |
| `WHISPER_MODEL` | Model name reported to the Whisper-compatible backend | `Systran/faster-whisper-large-v3` |
| `FILTER_WEB_URL` | Remote filter configuration source | `https://raw.githubusercontent.com/VoiceLinkVR/VoiceLinkServer/refs/heads/main/src/filter.json` |
| `ENABLE_WEB_TRANSLATORS` | Enable online translators from the `translators` library | `False` |
| `TRANSLATOR_SERVICE` | Preferred single translator service | `alibaba` |
| `TRANSLATOR_SERVICES_LIST` | Translator priority list used for fallback | `bing,iciba,alibaba,MyMemory,google` |
| `TRANSLATION_TIMEOUT` | Timeout for each translation request in seconds | `1.5` |

#### Gemma translation integration

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMMA_TRANSLATE_ENABLED` | Enable TranslateGemma/OpenAI-compatible translation path | `False` |
| `GEMMA_TRANSLATE_URL` | TranslateGemma base host and port | `127.0.0.1:8080` |
| `GEMMA_TRANSLATE_API_MODE` | API mode for Gemma service, currently `openai` | `openai` |
| `GEMMA_TRANSLATE_MODEL` | Model name sent to the Gemma service | `translategemma-12b-it-MP-GGUF` |
| `GEMMA_TRANSLATE_TIMEOUT` | Gemma request timeout in seconds | `10.0` |
| `GEMMA_TRANSLATE_MAX_TOKENS` | Max generated tokens for Gemma translation calls | `500` |
| `GEMMA_TRANSLATE_FALLBACK` | Fall back to other translators when Gemma fails | `True` |

#### Authentication and session

| Variable | Description | Default |
|----------|-------------|---------|
| `JWT_SECRET_KEY` | JWT signing secret; change in production | `wVLAF_13N6XL_QmP.DjkKsV` |
| `SESSION_SECRET_KEY` | Session middleware secret | `wVddLAF_13dsdddN6XL_QmP.DjkKsV` |
| `JWT_ACCESS_TOKEN_EXPIRES_MINUTES` | Access token lifetime in minutes | `10080` |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |

#### Database, rate limiting, and compatibility

| Variable | Description | Default |
|----------|-------------|---------|
| `SQL_PATH` | SQLAlchemy connection string | `sqlite:///{project_root}/data/db/users.db` |
| `SQL_POOL_SIZE` | SQLAlchemy pool size | `50` |
| `SQL_MAX_OVERFLOW` | SQLAlchemy max overflow connections | `150` |
| `SQL_POOL_RECYCLE` | SQLAlchemy recycle interval in seconds | `3600` |
| `LIMIT_ENABLE` | Enable request rate limiting | `True` |
| `LEGACY_CLIENT_COMPAT` | Enable old client compatibility behavior | `False` |
| `LIMIT_PUBLIC_TEST_USER` | Public test account exempt/special handling | empty |
| `LIMITER_REDIS_URL` | Redis URL for rate limiting storage | `redis://localhost:6379/0` |

#### Updates, TTS, logging, and text processing

| Variable | Description | Default |
|----------|-------------|---------|
| `TTS_URL` | External TTS service URL | empty |
| `TTS_TOKEN` | External TTS service token | empty |
| `LATEST_VERSION` | Version string returned to clients for update checks | empty |
| `PACKAGE_BASE_URL` | Base download URL for update packages | empty |
| `PACKAGE_TYPE` | Package suffix such as `.exe` | empty |
| `UPDATE_PUBLIC_BASE_URL` | Public base URL used when generating update links | empty |
| `UPDATE_STATIC_ROOT` | Local directory holding update files | `{project_root}/data/update/files` |
| `UPDATE_MANIFEST_PATH` | App update manifest path | `{project_root}/data/update/update_manifest.json` |
| `MODEL_MANIFEST_PATH` | Model manifest path | `{project_root}/data/update/models_manifest.json` |
| `TRANSLATION_PROFILE_MANIFEST_PATH` | Translation profile manifest path | `{project_root}/data/update/translation_profile_manifest.json` |
| `TRANSLATOR_RUNTIME_MANIFEST_PATH` | Translator runtime manifest path | `{project_root}/data/update/translator_runtime_manifest.json` |
| `TRANSLATION_CAPABILITIES_CACHE_SECONDS` | Capability cache TTL in seconds | `1800` |
| `TRANSLATION_CAPABILITY_TIMEOUT` | Capability probe timeout per engine in seconds | `4.0` |
| `LOG_LEVEL` | Application log level | `INFO` |
| `ENABLE_TEXT_COMPRESSION` | Enable repeated-character text compression | `True` |
| `TEXT_COMPRESSION_MIN_REPEAT` | Minimum repeat count before compression | `5` |

#### Afdian membership/payment

| Variable | Description | Default |
|----------|-------------|---------|
| `AFDIAN_USER_ID` | Afdian user ID | `db43f694299511ef821252540025c377` |
| `AFDIAN_TOKEN` | Afdian API token | empty |
| `AFDIAN_ORDER_CREATE_BASE_URL` | Afdian order creation page | `https://afdian.com/order/create` |
| `AFDIAN_QUERY_ORDER_URL` | Afdian order query API | `https://afdian.com/api/open/query-order` |
| `AFDIAN_CUSTOM_ID_KEY` | Secret used to sign/encrypt custom IDs | `change-me-to-32-byte-secret` |
| `AFDIAN_CUSTOM_PRICE` | Base custom price | `5.0` |
| `AFDIAN_RENEW_DAYS` | Days granted for one renewal unit | `30` |
| `AFDIAN_BOOST_DAYS` | Days used by boost plan calculations | `30` |
| `AFDIAN_BOOST_PRICE_STEP` | Price step for boost upgrades | `5.0` |
| `AFDIAN_BOOST_DAILY_INCREMENT` | Daily quota increment for each boost step | `1500` |
| `AFDIAN_DEFAULT_LIMIT_RULE` | Default quota/rate rule for paid users | `1500/day` |
| `AFDIAN_BOOST_LIMIT_RULE` | Optional explicit quota rule for boost users | empty |
| `AFDIAN_QUERY_SCAN_PAGES` | Number of order pages to scan per sync | `3` |
| `AFDIAN_CUSTOM_ID_TTL_SECONDS` | Custom ID validity period in seconds | `604800` |

#### Uvicorn startup variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UVICORN_WORKERS` | Worker process count used by `run.py` | `4` |
| `UVICORN_RELOAD` | Enable hot reload in `run.py`; `true/1/t` means enabled | `False` |

### Docker Compose variables

These variables are read by `compose.yaml` and usually come from `.env`.

#### Compose control and naming

| Variable | Description | Default |
|----------|-------------|---------|
| `COMPOSE_PROFILES` | Optional service profiles to start | `redis,faster-whisper` |
| `COMPOSE_PROJECT_NAME` | Compose project name | `voicelinkvr` |
| `REDIS_CONTAINER_NAME` | Redis container name override | `redis` |
| `FASTER_WHISPER_CONTAINER_NAME` | Faster-Whisper container name override | `faster-whisper` |
| `WHISPER_TRT_CONTAINER_NAME` | WhisperLive-TRT container name override | `whisper-trt` |
| `SENSEVOICE_CONTAINER_NAME` | SenseVoice container name override | `sensevoice` |
| `GEMMA_TRANSLATE_CONTAINER_NAME` | Gemma container name override | `gemma-translate` |
| `VOICELINK_CONTAINER_NAME` | VoiceLink app container name override | `voicelink` |

#### Image sources

| Variable | Description | Default |
|----------|-------------|---------|
| `VOICELINK_IMAGE` | VoiceLink server image | `boyqiu0010/voice-link-vr-server:latest` |
| `REDIS_IMAGE` | Redis image | `redis:latest` |
| `FASTER_WHISPER_IMAGE` | Faster-Whisper image | `fedirz/faster-whisper-server:latest-cuda` |
| `WHISPER_TRT_IMAGE` | WhisperLive-TRT image | `ghcr.io/your-org/whisperlive-trt-gemma:latest` |
| `GEMMA_TRANSLATE_IMAGE` | TranslateGemma image | `ghcr.io/your-org/translategemma-llama-server:latest` |
| `SENSEVOICE_IMAGE` | SenseVoice image | `ghcr.io/your-org/sensevoice-oneapi:latest` |

#### Published ports

| Variable | Description | Default |
|----------|-------------|---------|
| `VOICELINK_HOST_PORT` | Published port for the VoiceLink server | `8980` |
| `REDIS_HOST_PORT` | Published port for Redis | `6379` |
| `FASTER_WHISPER_HOST_PORT` | Published port for Faster-Whisper | `8000` |
| `WHISPER_TRT_WS_HOST_PORT` | Published WebSocket port for WhisperLive-TRT | `9090` |
| `WHISPER_TRT_REST_HOST_PORT` | Published REST port for WhisperLive-TRT | `18100` |
| `GEMMA_TRANSLATE_BIND_IP` | Bind address used when exposing Gemma | `127.0.0.1` |
| `GEMMA_TRANSLATE_HOST_PORT` | Published port for Gemma | `8080` |
| `SENSEVOICE_HOST_PORT` | Published port for SenseVoice | `8800` |

#### Volumes and local paths

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_DATA_PATH` | Host path mounted to Redis `/data` | `./redisFiles/data` |
| `REDIS_CONFIG_PATH` | Host path mounted as Redis config | `./redisFiles/redis.conf` |
| `VOICELINK_DATA_VOLUME` | Named volume for `/usr/src/app/data` | `voice-link-vr-server-local` |
| `WL_CACHE_DIR` | Host cache root for WhisperLive-TRT assets/engine/weights | `./.cache/whisper-trt` |
| `GEMMA_MODEL_ROOT` | Host model directory mounted to `/models` | `D:/models` |

#### Faster-Whisper container variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FASTER_WHISPER_TTL` | Model/process TTL inside Faster-Whisper container | `-1` |
| `FASTER_WHISPER_NUM_WORKERS` | Worker count inside Faster-Whisper container | `10` |
| `FASTER_WHISPER_CPU_THREADS` | CPU thread count inside Faster-Whisper container | `40` |

#### WhisperLive-TRT container variables

| Variable | Description | Default |
|----------|-------------|---------|
| `WHISPER_TRT_ENABLE_REST` | Enable REST API in WhisperLive-TRT | `true` |
| `WHISPER_TRT_REST_PORT` | Internal REST port in WhisperLive-TRT container | `8000` |
| `WHISPER_TRT_MULTILINGUAL` | Enable multilingual mode | `true` |
| `TRANSLATE_SERVICE_URL` | Translation service URL used by WhisperLive-TRT | `http://gemma-translate:8080` |
| `AUTO_BUILD_TRT_MODEL` | Auto-build TensorRT model on startup | `true` |
| `TRT_BUILD_MODEL_NAME` | Whisper model name used when building TRT engine | `large-v3` |
| `TRT_WEIGHT_ONLY_PRECISION` | Weight precision used during TRT build | `float16` |
| `TRT_PY_SESSION` | Enable Python TRT session mode | `false` |
| `TRT_BUILD_INFERENCE_PRECISION` | Inference precision used at build time | `float16` |
| `TRT_BUILD_MAX_BATCH_SIZE` | Max batch size for build | `1` |
| `TRT_BUILD_MAX_BEAM_WIDTH` | Max beam width for build | `1` |
| `TRT_BUILD_ENCODER_MAX_INPUT_LEN` | Max encoder input length during build | `3000` |
| `TRT_BUILD_ENCODER_MAX_SEQ_LEN` | Max encoder sequence length during build | `3000` |
| `TRT_BUILD_DECODER_MAX_SEQ_LEN` | Max decoder sequence length during build | `225` |
| `TRT_BUILD_DECODER_MAX_INPUT_LEN` | Max decoder input length during build | `32` |
| `TRT_BUILD_DECODER_MAX_ENCODER_INPUT_LEN` | Max decoder encoder input length during build | `3000` |
| `TRT_BUILD_DECODER_MAX_NUM_TOKENS` | Optional decoder token cap during build | empty |
| `TRT_RUNTIME_MAX_BATCH_SIZE` | Max batch size at runtime | `1` |
| `TRT_RUNTIME_MAX_INPUT_LEN` | Max runtime input length | `3000` |
| `TRT_RUNTIME_MAX_OUTPUT_LEN` | Max runtime output length | `64` |
| `TRT_RUNTIME_MAX_BEAM_WIDTH` | Max runtime beam width | `1` |
| `TRT_KV_CACHE_FREE_GPU_MEMORY_FRACTION` | Fraction of free GPU memory reserved for KV cache | `0.55` |
| `TRT_CROSS_KV_CACHE_FRACTION` | Fraction reserved for cross KV cache | `0.30` |
| `CONTEXT_FMHA_MODE` | Context FMHA plugin mode | `auto` |
| `BERT_ATTENTION_PLUGIN_MODE` | BERT attention plugin mode | `auto` |
| `GPT_ATTENTION_PLUGIN_MODE` | GPT attention plugin mode | `auto` |
| `CORS_ORIGINS` | Allowed CORS origins for WhisperLive-TRT REST | `http://127.0.0.1:18100,http://localhost:18100` |

#### TranslateGemma container variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMMA_MODEL_NAME` | Model loaded by the Gemma container | `translategemma-12b-it-MP-GGUF` |
| `LLAMA_N_GPU_LAYERS` | Number of model layers kept on GPU | `-1` |
| `LLAMA_N_PARALLEL` | Parallel request count for llama-server | `2` |
| `LLAMA_N_CTX` | Context length for llama-server | `3072` |
| `LLAMA_FLASH_ATTN` | Enable flash attention | `on` |
| `LLAMA_EXTRA_ARGS` | Extra startup args passed to llama-server | empty |

#### SenseVoice container variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SENSEVOICE_DEVICE_TYPE` | Device passed to SenseVoice | `cuda:0` |
| `SENSEVOICE_USE_ITN` | Enable inverse text normalization | `True` |
| `SENSEVOICE_NCPU` | CPU count used by SenseVoice | `8` |
| `SENSEVOICE_VAD_ENABLE` | Enable VAD in SenseVoice | `False` |

### Compatibility and legacy variables

The repository still contains older Flask/Werkzeug files (`src/server.py`, `src/serverstart.py`) that reference some historical variables. They are not part of the current FastAPI startup path (`run.py` -> `src/main.py`), but you may still encounter them in old deployments.

| Variable | Status | Notes |
|----------|--------|-------|
| `LIBRETRANSLATE_HOST` | legacy | Used by the old Flask server code |
| `LIBRETRANSLATE_PORT` | legacy | Used by the old Flask server code |
| `LIBRETRANSLATE_APIKEY` | legacy | Used by the old Flask server code |
| `FLASK_SECRET_KEY` | legacy | Replaced by `SESSION_SECRET_KEY` in FastAPI |
| `JWT_ACCESS_TOKEN_EXPIRES` | legacy | Replaced by `JWT_ACCESS_TOKEN_EXPIRES_MINUTES` |
| `SQLALCHEMY_POOL_SIZE` | legacy/compose | Historical name, current FastAPI setting is `SQL_POOL_SIZE` |
| `SQLALCHEMY_MAX_OVERFLOW` | legacy | Historical name, current FastAPI setting is `SQL_MAX_OVERFLOW` |
| `THREADS_NUM` | legacy | Used by old Waitress startup script |
