# 业务后端三模式部署方案

日期：2026-06-22

目标：同一个业务后端镜像支持三种运行模式：

- `all`：合体模式，保持当前行为，用于兼容和快速回滚。
- `light`：轻接口模式，只承载认证、账号、门户、下载、管理等低延迟接口。
- `heavy`：重接口模式，只承载 ASR、翻译、TTS、图片翻译等高耗时接口。

公网只暴露一个域名和一个入口，由反向代理按路径把请求转发到 `light` 或 `heavy`。客户端不需要知道后端拆分。

## 结论

这个方案合理，并且比只调 ASR 队列参数更彻底。

核心原因：

1. 认证、账号、版本、门户接口不再和 ASR 请求共享同一组 Uvicorn worker。
2. Whisper/FW 堵塞时，只影响 `heavy` 容器，不会拖慢 `light` 的登录、刷新 token、账号查询。
3. `heavy` 可以用短队列和快速拒绝策略，避免请求在业务后端排 90 秒。
4. 仍然保持一个镜像，部署、回滚、镜像发布流程不复杂。

必须注意：`light` 和 `heavy` 要共用同一个 DB、Redis、JWT/Session 密钥。认证不是由 `light` 远程代理给 `heavy`，而是两个容器都能本地校验同一套凭证。

## 新增运行模式

新增环境变量：

```env
VOICELINK_RUNTIME_MODE=all
```

允许值：

```text
all
light
heavy
```

建议再加两个控制项：

```env
VOICELINK_RUN_SCHEDULER=true
VOICELINK_RUN_SCHEMA_UPGRADE=true
```

推荐默认：

| 模式 | `VOICELINK_RUN_SCHEDULER` | `VOICELINK_RUN_SCHEMA_UPGRADE` |
| --- | --- | --- |
| `all` | `true` | `true` |
| `light` | `true` | `true` |
| `heavy` | `false` | `false` |

原因：

- 定时任务、数据库升级只需要一个角色执行。
- 如果 `heavy` 也执行 schema upgrade 或 scheduler，多个容器启动时会增加 DB 写入和锁竞争风险。
- 线上应以 `light` 作为控制面，`heavy` 只做计算请求。

## 模式职责

### all

合体模式，行为尽量等同当前版本。

用途：

- 本地开发。
- 53 单容器测试。
- 紧急回滚。
- 不想拆反代时继续保持现状。

允许所有接口。

### light

低延迟 Web API 模式。

职责：

- 登录、注册、刷新 token、登出。
- `/v2/auth/me`。
- 账号资料、套餐、额度查询。
- 官网/门户 API。
- 版本更新、下载信息。
- 管理后台。
- 公共配置和健康检查。

不应该承载：

- ASR。
- 音频翻译。
- 文本多目标翻译。
- 图片翻译。
- TTS。
- Voice clone。

如果重接口误打到 `light`，建议快速返回：

```http
503 Service Unavailable
Retry-After: 2
```

响应体建议：

```json
{
  "detail": {
    "code": "heavy_endpoint_not_available_on_light",
    "message": "Heavy endpoint is routed to light backend"
  }
}
```

这个返回主要用于发现反代配置错误。正常情况下公网反代不会把重接口转到 `light`。

### heavy

高耗时计算 API 模式。

职责：

- `/v1/api/whisper/*`
- `/v2/api/whisper/*`
- `/v1/api/func/translateToEnglish`
- `/v1/api/func/translateToOtherLanguage`
- `/v1/api/func/multitranslateToOtherLanguage`
- `/v1/api/func/photoTranslate`
- `/v1/api/func/tts`
- `/v1/api/func/webtranslate`
- `/v2/api/func/translateToEnglish`
- `/v2/api/func/translateToOtherLanguage`
- `/v2/api/func/multitranslateToOtherLanguage`
- `/v2/api/func/photoTranslate`
- `/v2/api/func/tts`
- `/v2/api/func/webtranslate`
- `/v2/api/tts/*`
- `/v2/api/clone/*`

`heavy` 也必须保留：

- `/healthz`
- `/readyz`
- `/`

`heavy` 不提供登录、注册、刷新 token、门户页面类接口。误打这些轻接口时建议返回：

```http
404 Not Found
```

或：

```http
503 Service Unavailable
```

推荐 `404`，避免外部误以为 heavy 也能提供完整 Web API。

## 认证方式

拆分后认证不做中心化远程调用。`heavy` 自己校验凭证。

### JWT

`light` 登录后签发 access token。`heavy` 收到请求后：

1. 从 `Authorization: Bearer <token>` 读取 token。
2. 用同一个 `JWT_SECRET_KEY` 和 `JWT_ALGORITHM` 解码。
3. 查同一个 DB 的 `V2User` / `UserSession`。
4. 继续执行原有 quota、rate limit、资源扣减逻辑。

必要共享配置：

```env
JWT_SECRET_KEY=同一个值
JWT_ALGORITHM=HS256
SQL_PATH=同一个数据库
```

### Portal Session Cookie

当前 SessionMiddleware 使用 `SESSION_SECRET_KEY` 签 cookie。拆分后：

1. `light` 登录写入 session cookie。
2. 浏览器请求 heavy 路径时携带同一个 cookie。
3. `heavy` 使用同一个 `SESSION_SECRET_KEY` 解 cookie。
4. `heavy` 查询同一个 DB 的 `UserSession`。

必要共享配置：

```env
SESSION_SECRET_KEY=同一个值
SESSION_COOKIE_SAME_SITE=lax
SESSION_COOKIE_HTTPS_ONLY=True
PORTAL_SESSION_KEY=同一个值
PORTAL_SESSION_USER_KEY=同一个值
SQL_PATH=同一个数据库
```

不需要 sticky session。

### Redis

限流、后续全局 ASR admission gate 都应该共用同一个 Redis：

```env
LIMITER_REDIS_URL=redis://redis:6379/0
```

如果 `light` 和 `heavy` 使用不同 Redis，限流和全局保护会失真。

## 反向代理路径规则

公网反代默认转发到 `light`，只有重接口显式转发到 `heavy`。

推荐原则：

1. heavy path 精确优先。
2. 其它全部落到 light。
3. `heavy` 不作为默认 upstream。
4. 上传类接口要设置足够的 body size 和超时时间。

Nginx 示例：

```nginx
upstream voicelink_light {
    server 127.0.0.1:8980;
}

upstream voicelink_heavy {
    server 127.0.0.1:8981;
}

server {
    listen 443 ssl http2;
    server_name api.example.com;

    client_max_body_size 256m;

    location ~ ^/(v1|v2)/api/whisper/ {
        proxy_pass http://voicelink_heavy;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    location ~ ^/(v1|v2)/api/func/(translateToEnglish|translateToOtherLanguage|multitranslateToOtherLanguage|photoTranslate|tts|webtranslate)$ {
        proxy_pass http://voicelink_heavy;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    location ~ ^/v2/api/(tts|clone)/ {
        proxy_pass http://voicelink_heavy;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    location / {
        proxy_pass http://voicelink_light;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }
}
```

如果有多台 heavy：

```nginx
upstream voicelink_heavy {
    least_conn;
    server 192.168.2.81:8981 max_fails=2 fail_timeout=10s;
    server 192.168.2.198:8981 max_fails=2 fail_timeout=10s;
}
```

## Compose 形态

同一个镜像启动两个业务容器：

```yaml
services:
  voicelink-light:
    image: ${VOICELINK_IMAGE}
    container_name: voicelink-light
    restart: unless-stopped
    ports:
      - "8980:8980"
    environment:
      VOICELINK_RUNTIME_MODE: light
      VOICELINK_RUN_SCHEDULER: "true"
      VOICELINK_RUN_SCHEMA_UPGRADE: "true"
      UVICORN_WORKERS: ${LIGHT_UVICORN_WORKERS:-4}
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      SESSION_SECRET_KEY: ${SESSION_SECRET_KEY}
      SQL_PATH: ${SQL_PATH}
      LIMITER_REDIS_URL: ${LIMITER_REDIS_URL}

  voicelink-heavy:
    image: ${VOICELINK_IMAGE}
    container_name: voicelink-heavy
    restart: unless-stopped
    ports:
      - "8981:8980"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      VOICELINK_RUNTIME_MODE: heavy
      VOICELINK_RUN_SCHEDULER: "false"
      VOICELINK_RUN_SCHEMA_UPGRADE: "false"
      UVICORN_WORKERS: ${HEAVY_UVICORN_WORKERS:-2}
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      SESSION_SECRET_KEY: ${SESSION_SECRET_KEY}
      SQL_PATH: ${SQL_PATH}
      LIMITER_REDIS_URL: ${LIMITER_REDIS_URL}
      WHISPER_ENDPOINTS: ${WHISPER_ENDPOINTS}
      WHISPER_BACKEND: ${WHISPER_BACKEND:-faster-whisper}
      WHISPER_MODEL: ${WHISPER_MODEL:-Systran/faster-whisper-large-v3}
      ASR_QUEUE_LIMIT_ENABLED: "true"
      ASR_QUEUE_WHISPER_MAX_CONCURRENCY: ${ASR_QUEUE_WHISPER_MAX_CONCURRENCY:-4}
      ASR_QUEUE_WHISPER_MAX_WAITING: ${ASR_QUEUE_WHISPER_MAX_WAITING:-16}
      ASR_QUEUE_WHISPER_TIMEOUT_SECONDS: ${ASR_QUEUE_WHISPER_TIMEOUT_SECONDS:-10}
      ASR_QUEUE_WHISPER_RETRY_AFTER_SECONDS: ${ASR_QUEUE_WHISPER_RETRY_AFTER_SECONDS:-2}
```

`all` 模式保留原服务：

```yaml
services:
  voicelink:
    image: ${VOICELINK_IMAGE}
    environment:
      VOICELINK_RUNTIME_MODE: all
```

## Heavy 队列策略

拆分后 `heavy` 仍然需要保护，否则只是把问题从 `light` 隔离出去了，heavy 自己仍会排队雪崩。

推荐先改为：

```env
ASR_QUEUE_LIMIT_ENABLED=true
ASR_QUEUE_WHISPER_MAX_CONCURRENCY=按实际 FW 端点能力设置
ASR_QUEUE_WHISPER_MAX_WAITING=16~32
ASR_QUEUE_WHISPER_TIMEOUT_SECONDS=8~15
ASR_QUEUE_WHISPER_RETRY_AFTER_SECONDS=2~5
```

原则：

- 不再允许 90 秒长时间排队。
- 等不到 slot 快速返回 `503 + Retry-After`。
- 客户端收到 `503` 后按 `Retry-After` 或指数退避重试。
- 如果未来有 2 个 faster-whisper 端点，可以提高全局可用并发，但不要把 `max_waiting` 放得很大。

后续更彻底的版本应增加 Redis 全局 admission gate：

```text
请求进入 heavy
-> 判断路径是否是 heavy
-> Redis 全局 permit
-> 拿到 permit 才进入本地 ASR 队列/下游 FW
-> 拿不到快速 503
```

这样可以避免多个 heavy 容器、多个 Uvicorn worker 各自维护本地队列导致总并发不可控。

## 代码改造点

### 1. 配置项

文件：`src/core/config.py`

新增：

```python
VOICELINK_RUNTIME_MODE: str = "all"
VOICELINK_RUN_SCHEDULER: bool = True
VOICELINK_RUN_SCHEMA_UPGRADE: bool = True
```

启动时校验：

```text
VOICELINK_RUNTIME_MODE in {"all", "light", "heavy"}
```

### 2. 路径分类

文件：`src/main.py`

新增：

```python
HEAVY_PATH_PATTERNS = (...)
LIGHT_ALWAYS_ALLOWED_PATHS = (...)

def is_heavy_path(path: str) -> bool: ...
def is_light_path(path: str) -> bool: ...
```

建议先用 path 前缀和精确 path，不要依赖 router name。

重接口表：

```text
/v1/api/whisper/
/v2/api/whisper/
/v1/api/func/translateToEnglish
/v1/api/func/translateToOtherLanguage
/v1/api/func/multitranslateToOtherLanguage
/v1/api/func/photoTranslate
/v1/api/func/tts
/v1/api/func/webtranslate
/v2/api/func/translateToEnglish
/v2/api/func/translateToOtherLanguage
/v2/api/func/multitranslateToOtherLanguage
/v2/api/func/photoTranslate
/v2/api/func/tts
/v2/api/func/webtranslate
/v2/api/tts/
/v2/api/clone/
```

### 3. 模式准入中间件

文件：`src/main.py`

在尽可能早的位置判断：

```python
mode = settings.VOICELINK_RUNTIME_MODE
path = request.url.path

if mode == "light" and is_heavy_path(path):
    return JSONResponse(status_code=503, content={...}, headers={"Retry-After": "2"})

if mode == "heavy" and not is_heavy_path(path) and path not in LIGHT_ALWAYS_ALLOWED_PATHS:
    return JSONResponse(status_code=404, content={"detail": "Not Found"})
```

建议 `LIGHT_ALWAYS_ALLOWED_PATHS` 至少包含：

```text
/
/healthz
/readyz
```

### 4. 生命周期任务控制

文件：`src/main.py`

`lifespan()` 中：

```python
if settings.VOICELINK_RUN_SCHEMA_UPGRADE:
    run_schema_upgrade()

if settings.VOICELINK_RUN_SCHEDULER:
    scheduler.start()
```

`heavy` 不跑 scheduler，不跑 schema upgrade。

### 5. 健康检查

新增或确认：

```text
GET /healthz
GET /readyz
```

返回建议：

```json
{
  "status": "ok",
  "mode": "heavy"
}
```

`readyz` 可以检查 DB/Redis/FW 依赖；`healthz` 只表示进程存活。

## 数据库要求

如果线上仍使用 SQLite 文件，多容器拆分风险较高：

- 多容器同时写 request log / quota / session，容易遇到写锁。
- schema upgrade 和 scheduler 如果重复执行，会进一步增加锁竞争。
- voice clone preset、support ticket attachment 这类文件目录也要共享或按角色隔离。

推荐生产使用 MySQL/MariaDB：

```env
SQL_PATH=mysql+pymysql://user:password@mysql-host:3306/voicelink?charset=utf8mb4
```

如果短期必须继续 SQLite：

1. 只允许 `light` 执行 schema upgrade。
2. 只允许 `light` 执行 scheduler。
3. `light` 和 `heavy` 必须挂同一个 data volume。
4. 要重点监控 `database is locked`。

## 发布步骤

### 阶段 1：代码准备

1. 新增 `VOICELINK_RUNTIME_MODE`。
2. 新增 path 分类和模式准入。
3. 新增 scheduler/schema upgrade 开关。
4. 新增 `/healthz`、`/readyz`。
5. 保持默认 `all`，确认旧部署不受影响。

验收：

```bash
curl http://127.0.0.1:8980/healthz
curl http://127.0.0.1:8980/v2/auth/me
curl -F file=@sample.wav http://127.0.0.1:8980/v2/api/whisper/multitranscription
```

### 阶段 2：本机/53 测试 light + heavy

启动两个容器：

```text
light: 8980 -> 8980
heavy: 8981 -> 8980
```

验收：

```bash
curl http://127.0.0.1:8980/healthz
curl http://127.0.0.1:8981/healthz
curl http://127.0.0.1:8980/v2/api/whisper/multitranscription
curl http://127.0.0.1:8981/v2/auth/me
```

预期：

- `light /healthz` 返回 `mode=light`。
- `heavy /healthz` 返回 `mode=heavy`。
- `light` 上重接口返回 `503 heavy_endpoint_not_available_on_light`。
- `heavy` 上轻接口返回 `404`。

### 阶段 3：反代灰度

公网 Nginx 默认仍指向 `light`。

先只把一个重接口切到 `heavy`：

```text
/v2/api/func/multitranslateToOtherLanguage
```

观察：

```bash
docker logs --since 10m voicelink-light
docker logs --since 10m voicelink-heavy
docker stats --no-stream voicelink-light voicelink-heavy
```

确认：

- light 的 auth/account p95 不被 heavy 请求影响。
- heavy 的 503 只出现在重接口。
- heavy 返回 503 时带 `Retry-After`。
- faster-whisper 健康正常。

### 阶段 4：全量切 heavy 路径

把所有 heavy path 转发到 heavy upstream。

观察至少 30 分钟：

```bash
docker logs --since 30m voicelink-light | grep -E "status=5|API-END|RATE-LIMIT"
docker logs --since 30m voicelink-heavy | grep -E "status=5|ASR_QUEUE_LIMIT|asr_queue"
```

关键指标：

- `/v2/auth/me` p95 应保持毫秒级或低百毫秒。
- `/v2/api/account/profile` p95 应保持稳定。
- heavy 的 `asr_queue_timeout` 可以存在，但不能拖慢 light。
- heavy 队列等待不应长期接近 `max_waiting`。

## 回滚方案

保留 `all` 模式是关键。

如果 light/heavy 拆分异常：

1. Nginx 全部流量切回 `voicelink-all`。
2. 或停止 `light/heavy`，启动一个 `VOICELINK_RUNTIME_MODE=all` 的容器。
3. 镜像不用回滚，除非 `all` 模式也异常。

Nginx 回滚示例：

```nginx
location / {
    proxy_pass http://voicelink_all;
}
```

## 风险和处理

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| 反代路径漏配 | 重接口打到 light 或轻接口打到 heavy | 模式准入快速返回，并在日志中打出 `runtime_mode_rejected` |
| JWT/Session 密钥不一致 | heavy 认证失败 | light/heavy 使用同一 `.env` Secret |
| DB 不共享 | heavy 查不到 session/user/quota | 使用同一 MySQL 或同一 SQLite volume |
| SQLite 写锁 | 登录、quota、日志变慢 | 生产切 MySQL；短期关闭 heavy scheduler/schema upgrade |
| 本地 ASR 队列仍按 worker 放大 | heavy 仍可能过量打 FW | 后续加 Redis 全局 admission gate |
| heavy 等待队列过长 | 请求堆积后成片超时 | 小 waiting + 短 timeout + Retry-After |
| 客户端无重试 | 用户看到 503 | 客户端遇到 `503 + Retry-After` 后等待重试，不要取消上一条再狂发 |

## 推荐最终形态

```text
公网入口
  |
  |-- /v2/auth/*、/v2/api/account/*、/v2/api/billing/*、/v2/api/content/*、下载/版本/管理
  |       -> voicelink-light
  |
  |-- /v1|v2/api/whisper/*、/func/translate*、/func/tts、/tts/*、/clone/*
          -> voicelink-heavy
                  |
                  |-- Redis 全局 admission gate
                  |-- 本地小队列
                  |-- faster-whisper / FireRed / TTS / Gemma
```

推荐优先级：

1. 先实现三模式和反代拆分，解决认证接口被 heavy 拖慢的问题。
2. 再收紧 heavy 的等待队列，避免 90 秒排队雪崩。
3. 最后做 Redis 全局 ASR admission gate，解决多 worker、多 heavy 容器下的真实全局容量控制。
