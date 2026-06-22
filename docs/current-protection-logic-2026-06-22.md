# 当前业务后端保护逻辑梳理

日期：2026-06-22

本文只描述当前代码和 81 当前部署参数下的保护逻辑，不包含尚未落地的 Redis 全局 ASR permit、异步日志队列、heavy/light 多实例拆分等设计。

## 结论摘要

当前保护主要有四层：

1. 普通请求频率限流：`core.rate_limiter`，触发后返回 `430`。
2. 资源额度保护：`set_user_context_cost()` + `quota_service`，额度不足返回 `430`。
3. ASR 队列保护：`core.services` 内存队列，队列满或等待超时返回 `503`。
4. 客户端断链取消：路由和下游 HTTP 请求的 `cancel_check`，触发后业务后端返回 `499`。

当前最容易造成用户感知异常的是第 3 层：ASR 队列保护。它判断的是业务后端进程内的 active/waiting 槽位，不是 GPU/CPU 真实满载。因此会出现“faster-whisper 看起来没满载，但业务后端仍然返回 503”的情况。

## 81 当前关键参数

来自 `/root/compose/voicelinkvr-v2/.env`：

```env
WHISPER_HOST=host.docker.internal
WHISPER_PORT=18080
WHISPER_ENDPOINTS=http://192.168.2.81:18080/v1,http://192.168.2.198:18080/v1

LIMIT_ENABLE=True
DEFAULT_TEST_USER_IP_RATE_LIMIT=10/minute

FIRERED_LID_ENABLED=True
FIRERED_LID_HOST=192.168.2.81
FIRERED_LID_PORT=18000
FIRERED_LID_TIMEOUT=4
FIRERED_LID_GLOBAL_MIN_INTERVAL=0
FIRERED_LID_QUEUE_TIMEOUT=2
FIRERED_LID_MAX_CONCURRENCY=4

ASR_QUEUE_LIMIT_ENABLED=true
ASR_QUEUE_WHISPER_MAX_CONCURRENCY=12
ASR_QUEUE_WHISPER_MAX_WAITING=160
ASR_QUEUE_WHISPER_TIMEOUT_SECONDS=90
ASR_QUEUE_WHISPER_RETRY_AFTER_SECONDS=3

ASR_QUEUE_FIRERED_MAX_CONCURRENCY=12
ASR_QUEUE_FIRERED_MAX_WAITING=96
ASR_QUEUE_FIRERED_TIMEOUT_SECONDS=90
ASR_QUEUE_FIRERED_RETRY_AFTER_SECONDS=3
```

注意：当前业务后端实际是多个 Uvicorn worker。ASR 队列状态保存在 Python 进程内，不是 Redis 全局状态。因此这些配置不是全局唯一上限，而是会按 worker、按端点产生放大效应。

## 入口公共路径

文件：`src/main.py`

所有请求都会经过 `request_logging_middleware()`：

1. 生成 `request_id`。
2. 尝试解析当前用户，用于日志和统计。
3. 对未认证且非白名单路径应用默认 IP 限流。
4. 调用实际路由。
5. 在 `finally` 中提交 quota、限流命中和请求日志/统计。

当前风险点：

- 用户解析、quota 提交、请求日志写入都是同步 DB 路径。
- heavy 请求和 auth/account 请求共用同一套 middleware。
- 如果 heavy 请求大量堆积，同步日志和同步 DB 路径会放大整体延迟。

## 普通频率限流

文件：`src/core/rate_limiter.py`

核心对象：

- `RateLimiter`
- `RateLimitContext`
- `RateLimitExceeded`
- `enforce_user_rate_limit()`

触发状态码：

```text
430
```

返回内容大致为：

```json
{
  "error": "Too many request",
  "LimitRules": "...",
  "limit": "..."
}
```

逻辑：

1. 认证用户先解析用户身份。
2. `testuser` 使用 `DEFAULT_TEST_USER_IP_RATE_LIMIT`，当前 81 是 `10/minute`。
3. 普通用户使用用户自身 limit rule。
4. 限流检查通过后，把 context 放到 `request.state.rate_limit_contexts`。
5. 请求结束时 middleware 根据状态码决定是否 `context.commit()`。

重要细节：

- `testuser` 是按 IP scope 限流，不是全局账号总限流。
- 当前用户明确要求不做额外的 `testuser` 特殊强限流，因此这里仅保留已有规则。
- 最近观察到的 ASR 503 不是这层触发的，普通 `429/430` 当时为 0。

## 资源额度保护

相关文件：

- `src/core/rate_limiter.py`
- `src/core/quota_service.py`
- `src/main.py`
- `src/routers/api.py`
- `src/routers/v2_tts.py`

触发状态码：

```text
430
```

额度设置入口：

```python
set_user_context_cost(request, cost)
```

业务路由通过 `_apply_effective_resource_cost()` 或 TTS 路由里的 cost 设置消耗量。典型消耗：

- 单 ASR：`1`
- 识别 + 1 个翻译目标：`2`
- 多目标翻译：`1 + translation_count`
- 图片翻译：`1 + translation_count`
- TTS/clone 根据具体路由设置

新额度模式下：

1. `set_user_context_cost()` 会调用 `prepare_quota_allocation()`。
2. `prepare_quota_allocation()` 查询用户当天已用额度和 addon 余额。
3. 请求成功后，middleware 在 finally 中调用 `commit_quota_allocation()`。
4. 如果额度不足，返回 `430 resource_quota_exceeded`。

重要细节：

- 额度扣减不能简单异步丢后台，否则可能出现“请求已成功但额度未扣”的账务问题。
- 当前实现仍在请求 finally 中同步提交，heavy 请求量大时会增加公共路径压力。

## ASR 队列保护

文件：`src/core/services.py`

核心配置：

```python
ASR_QUEUE_LIMIT_ENABLED
ASR_QUEUE_MAX_CONCURRENCY
ASR_QUEUE_MAX_WAITING
ASR_QUEUE_TIMEOUT_SECONDS
ASR_QUEUE_RETRY_AFTER_SECONDS
ASR_QUEUE_SLOW_LOG_SECONDS
ASR_QUEUE_LATENCY_WINDOW
```

每个 ASR endpoint 还支持覆盖：

```text
ASR_QUEUE_WHISPER_MAX_CONCURRENCY
ASR_QUEUE_WHISPER_MAX_WAITING
ASR_QUEUE_WHISPER_TIMEOUT_SECONDS
ASR_QUEUE_WHISPER_RETRY_AFTER_SECONDS

ASR_QUEUE_FIRERED_MAX_CONCURRENCY
ASR_QUEUE_FIRERED_MAX_WAITING
ASR_QUEUE_FIRERED_TIMEOUT_SECONDS
ASR_QUEUE_FIRERED_RETRY_AFTER_SECONDS
```

状态码：

```text
503
```

典型错误：

```json
{
  "error": "asr_queue_timeout",
  "engine": "whisper",
  "language": "ja",
  "active": 12,
  "waiting": 9,
  "maxConcurrency": 12,
  "maxWaiting": 160,
  "queueTimeoutSeconds": 90.0
}
```

另一个错误：

```json
{
  "error": "asr_queue_full",
  "engine": "whisper",
  "language": "ja"
}
```

### 单端点逻辑

核心函数：

```python
_acquire_asr_endpoint_slot(endpoint, language, cancel_check)
_release_asr_endpoint_slot(permit, record_latency=True)
_run_asr_endpoint(endpoint, language, operation, cancel_check)
```

流程：

1. 如果 `ASR_QUEUE_LIMIT_ENABLED=false`，直接放行，不排队。
2. 如果 `active < max_concurrency`，立即 `active += 1` 并放行。
3. 如果 `waiting >= max_waiting`，返回 `503 asr_queue_full`。
4. 否则 `waiting += 1`，等待 slot。
5. 等待超过 `timeout_seconds`，返回 `503 asr_queue_timeout`。
6. ASR 调用结束后 finally 释放 slot。

### 多 Whisper 后端逻辑

当前代码支持 `WHISPER_ENDPOINTS`，例如：

```env
WHISPER_ENDPOINTS=http://192.168.2.81:18080/v1,http://192.168.2.198:18080/v1
```

核心函数：

```python
_whisper_backends()
_acquire_asr_backend_slot("whisper", backends, language, cancel_check)
_run_asr_backend("whisper", backends, language, operation, cancel_check)
```

当有多个 Whisper endpoint 时：

1. 每个 endpoint 被命名为 `whisper-1`、`whisper-2`。
2. 配置读取先找精确 key，再找 group key。
3. 如果没有 `ASR_QUEUE_WHISPER_1_*`，就回退到 `ASR_QUEUE_WHISPER_*`。
4. 当前 81 的两个端点都会使用 `ASR_QUEUE_WHISPER_MAX_CONCURRENCY=12`。
5. 在单个 worker 内，理论 whisper 总 active 上限会接近 `端点数量 * 12`。
6. 再叠加多个 Uvicorn worker，总体可放行量会进一步放大。

### 当前 ASR 队列的关键问题

ASR 队列是进程内内存状态：

```python
_asr_endpoint_queues: dict[str, AsrEndpointQueue] = {}
```

这意味着：

- 每个 Uvicorn worker 有自己的 active/waiting。
- active/waiting 不是 Redis 全局值。
- 某个 worker 可能 active 满了并返回 503，而 faster-whisper/GPU 看起来并没有满载。
- 多 endpoint 加多 worker 会让配置值和真实系统上限变得不直观。

这也是“后台服务明明没满载，为什么还是进入保护”的主要原因：保护判断的是业务后端本地队列槽位，不是真实 GPU/CPU 利用率。

## FireRedLID 保护

文件：`src/core/services.py`

核心配置：

```env
FIRERED_LID_ENABLED=True
FIRERED_LID_MAX_CONCURRENCY=4
FIRERED_LID_QUEUE_TIMEOUT=2
FIRERED_LID_GLOBAL_MIN_INTERVAL=0
FIRERED_LID_TIMEOUT=4
```

作用：

- 在多语言/自动路由前调用 FireRedLID 判断语言。
- LID 有本地 semaphore 控制并发。
- 如果 LID 队列超时或失败，路由逻辑会尽量保持请求语言，不应直接把整个服务打挂。

重要细节：

- LID 保护和 ASR 队列保护是两套机制。
- 最近的 `503 asr_queue_timeout` 是 Whisper ASR 队列，不是 LID 队列。

## 客户端断链取消

相关文件：

- `src/routers/api.py`
- `src/core/services.py`
- `src/main.py`

路由里定义：

```python
async def _raise_if_client_disconnected(request: Request, stage: str) -> None:
    if await request.is_disconnected():
        raise ClientDisconnectedError(stage)
```

调用点包括：

- 读音频前后
- opus 解码后
- LID 前后
- ASR 前后
- 翻译前后

下游 HTTP 调用使用：

```python
_post_with_cancel()
_await_with_cancel()
```

作用：

- 客户端断开后，watcher 会取消当前 httpx 请求。
- middleware 捕获 `ClientDisconnectedError` 后返回 `499`。
- ASR slot 在 finally 中释放。

当前限制：

- 如果某些同步工作已经进入线程池或第三方库内部，Python 层取消不一定能立即中断底层执行。
- ASR 服务内部是否真正停止计算，取决于 ASR 服务自身实现。

## 当前重接口范围

当前容易进入 heavy 路径的接口主要包括：

```text
/v1/api/whisper/transcriptions
/v1/api/whisper/translations
/v1/api/whisper/multitranscription
/v1/api/func/translateToEnglish
/v1/api/func/translateToOtherLanguage
/v1/api/func/multitranslateToOtherLanguage
/v1/api/func/photoTranslate
/v1/api/func/tts
/v1/api/func/webtranslate

/v2/api/whisper/transcriptions
/v2/api/whisper/translations
/v2/api/whisper/multitranscription
/v2/api/func/translateToEnglish
/v2/api/func/translateToOtherLanguage
/v2/api/func/multitranslateToOtherLanguage
/v2/api/func/photoTranslate
/v2/api/func/tts
/v2/api/func/webtranslate

/v2/api/tts/*
/v2/api/clone/*
```

其中最近触发保护最多的是：

```text
/v2/api/func/multitranslateToOtherLanguage
```

## 状态码对照

| 状态码 | 来源 | 含义 |
| --- | --- | --- |
| `200` | 路由正常返回 | 请求成功 |
| `400` | 参数校验 | 例如语言参数错误 |
| `401` | 认证 | 未登录或 token 无效 |
| `429` | 当前代码较少使用 | 常规 Too Many Requests 语义 |
| `430` | 频率限流/额度不足 | `RateLimitExceeded` 或 `resource_quota_exceeded` |
| `499` | 客户端断开 | `ClientDisconnectedError` |
| `503` | ASR 队列保护/下游不可用 | `asr_queue_timeout`、`asr_queue_full` 等 |

## 为什么认证会被重接口影响

认证和 heavy 接口当前仍在同一个 FastAPI 应用、同一个容器、同一组 Uvicorn worker 内。

虽然认证本身不走 ASR，但它仍经过公共 middleware：

```text
request_logging_middleware
用户解析
默认限流
请求日志/统计写入
```

当 heavy 请求大量挂起时，业务后端会出现：

- ASGI 请求生命周期变长。
- 音频 buffer、下游 HTTP 连接、cancel watcher 等资源长期存在。
- middleware finally 中同步 DB 写入增加压力。
- 多 worker 的本地 ASR 队列状态不一致。

因此历史事故里认证接口也会变慢。但最近观察中，认证/账户接口已经恢复到毫秒级，当前主要问题是 Whisper heavy 接口大量 `503 asr_queue_timeout`。

## 当前逻辑的主要短板

1. ASR 队列是进程内状态，不是全局状态。
2. active/waiting 不等于真实 GPU/CPU 负载。
3. `ASR_QUEUE_WHISPER_TIMEOUT_SECONDS=90` 太长，请求会在业务后端挂太久。
4. `ASR_QUEUE_WHISPER_MAX_WAITING=160` 太大，容易制造业务后端堆积。
5. 多 Whisper 端点 + 多 Uvicorn worker 会放大实际放行量。
6. 请求日志/统计仍在 middleware finally 同步写 DB。
7. heavy 请求的部分同步工作还没有统一从 event loop 中隔离出去。

## 推荐后续修改方向

在保持一个镜像、一个容器、一个端口的前提下，建议按这个顺序做：

1. 增加 `is_heavy_path()`，识别 heavy 接口。
2. heavy 请求在最早阶段进入 admission gate，拿不到许可快速返回 `503 + Retry-After`。
3. ASR permit 改为 Redis 全局状态，而不是进程内 `_asr_endpoint_queues`。
4. heavy 请求的同步 DB 查询、额度 prepare/commit 放到 `asyncio.to_thread()`，避免阻塞 event loop。
5. heavy 请求的 request log / v2 metrics 改为后台队列批量写入；队列满时丢弃非关键 audit，不丢额度扣减。
6. Opus 解码、同步 web translator、其它 CPU/阻塞调用也统一放到线程池并设置超时。
7. 下调当前队列参数，避免 90 秒长时间挂起：

```env
ASR_QUEUE_WHISPER_TIMEOUT_SECONDS=10~20
ASR_QUEUE_WHISPER_MAX_WAITING=小队列，例如 16~32
```

具体数值要结合实际 faster-whisper 端点数量和真实并发能力压测后确定。

## 线上排查命令

查看 30 分钟状态码和 ASR 队列触发：

```bash
docker logs --since 30m voicelink-backend 2>&1 | grep -E "API-END|ASR_QUEUE_LIMIT|asr_queue"
```

统计 `503`：

```bash
docker logs --since 30m voicelink-backend 2>&1 | grep "status=503" | wc -l
```

查看 ASR 队列保护详情：

```bash
docker logs --since 30m voicelink-backend 2>&1 | grep "ASR_QUEUE_LIMIT" | tail -n 100
```

查看 faster-whisper 实际处理耗时：

```bash
docker logs --since 30m fwsd-test 2>&1 | grep -E "Transcribed|Processing audio" | tail -n 100
```

查看容器和 GPU：

```bash
docker stats --no-stream voicelink-backend fwsd-test
nvidia-smi
```

判断是否是普通限流：

```bash
docker logs --since 30m voicelink-backend 2>&1 | grep -E "RATE-LIMIT|Too many request|status=430|status=429"
```

判断是否是 ASR 队列保护：

```bash
docker logs --since 30m voicelink-backend 2>&1 | grep -E "ASR_QUEUE_LIMIT|asr_queue_timeout|asr_queue_full|status=503"
```
