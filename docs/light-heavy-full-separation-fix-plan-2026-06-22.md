# 503 根因修复与 Light/Heavy 分离方案

日期：2026-06-22

仓库：`D:\git\gitlab\VoiceLinkServer1`

## 1. 已确认事实

81 当前已经关闭 ASR 保护：

```env
ASR_QUEUE_LIMIT_ENABLED=false
ASR_BACKEND_STATUS_ENABLED=false
```

线上容器实际是：

```env
UVICORN_WORKERS=1
```

备份：

```text
/root/compose/voicelinkvr-v2/.env.bak.disable-asr-protection-20260622210039
```

保护关闭后 503 仍发生：

```text
/v2/api/func/multitranslateToOtherLanguage: 517 次 200，9 次 503，503 平均 35.381s
/v2/api/whisper/multitranscription: 73 次 200，1 次 503，503 为 123.315s
```

这些 503 没有扣额度。

81/198 faster-whisper 没有长期满载：

```text
activeRequests=0
recent p95 约 1.2s / 2.2s
GPU 不是长期满载
```

关键现象：

```text
业务后端已收到 ASR 下游 HTTP/1.1 200 OK 响应头
但后续 body/read/parse/close 或业务处理链路卡住
最终触发业务侧 31s / 121s hard timeout
返回 503
```

53 复测后的新增结论：

```text
直接 curl faster-whisper：约 0.27s 返回 body
业务后端旧实现：headers 约 0.2s 到达，body 卡到 hard timeout 边界才返回
根因是 ASR 断链检查 watcher 调用 request.is_disconnected() 后不能及时取消
导致正常完成的下游请求被 watcher 回收路径拖住
```

因此 hard timeout 仍应保留为最后兜底，但不能作为正常请求完成路径的一部分；断链检查必须是有超时的短轮询，不能用长期后台 watcher。

另一个独立阻塞源：

```text
/v2/adminapi/stats
/v2/adminapi/users
```

这两个接口多次执行 16-17s。由于线上只有 1 个 worker，`admin stats` 的同步 SQLAlchemy 查询会阻塞唯一 event loop，导致 `healthz` 也出现 5-17s 延迟。

## 2. 根因判断

这次不是被攻击，也不是 whisper / FireRed 算力不够。

ASR 503 的直接根因：

```text
_await_with_cancel 使用后台 watcher 轮询 request.is_disconnected()
  -> 下游 ASR 已经返回 headers/body
  -> watcher 取消/回收阻塞正常返回
  -> 外层 hard timeout 到点后才释放
  -> 已完成请求被误判为 503 或被拖到 40s/140s
```

根因链路：

```text
UVICORN_WORKERS=1
  -> admin stats/users 同步慢查询阻塞 event loop
  -> 阻塞释放后积压请求集中进入
  -> ASR 保护关闭，无界突发并发
  -> ASR 响应读取/解析/关闭、翻译、日志、DB 提交互相挤压
  -> 部分请求 wall-clock 超过 hard timeout
  -> 业务后端返回 503
```

因此当前修改重点：

1. 修 admin 慢接口阻塞。
2. ASR 恢复短队列，不恢复长等待。
3. ASR 加业务侧 endpoint 熔断，不再用下游 `activeRequests/oldestActiveSeconds` 做主判断。
4. ASR HTTP 加分阶段日志，确认 200 OK 后卡在哪。
5. 切 light/heavy，避免 auth/admin 和 ASR 共享 worker。

## 3. 代码修改方案

### 3.1 Admin 慢接口

目标接口：

```text
/v2/adminapi/stats
/v2/adminapi/users
```

要求：

- 不要在 `async def` 中直接执行同步 SQLAlchemy 慢查询。
- 短期用 `asyncio.to_thread()` 或改同步 `def` 路由。
- `/v2/adminapi/stats` 加 30-60s 缓存。
- `/v2/adminapi/users` 保持分页，慢查询补索引或线程池隔离。

验收：

```text
admin stats 执行时 /healthz 不再出现 5-17s 延迟
admin stats 执行时 /v2/auth/me 不被拖慢
```

### 3.2 ASR 短队列

恢复有界 ASR 队列，但只吸收瞬时抖动。

推荐配置：

```env
ASR_QUEUE_LIMIT_ENABLED=true
ASR_BACKEND_STATUS_ENABLED=false

ASR_QUEUE_WHISPER_MAX_CONCURRENCY=4
ASR_QUEUE_WHISPER_MAX_WAITING=4
ASR_QUEUE_WHISPER_TIMEOUT_SECONDS=1.5
ASR_QUEUE_WHISPER_RETRY_AFTER_SECONDS=2

ASR_QUEUE_FIRERED_MAX_CONCURRENCY=4
ASR_QUEUE_FIRERED_MAX_WAITING=4
ASR_QUEUE_FIRERED_TIMEOUT_SECONDS=1.5
ASR_QUEUE_FIRERED_RETRY_AFTER_SECONDS=2
```

原则：

- 没槽位就快速 503 + `Retry-After`。
- 不让请求排 30-120s。
- 503 不扣额度。

### 3.3 ASR endpoint 熔断

按业务侧真实结果统计，不用下游自报状态做主判断。

每个 endpoint 记录：

```text
inflight
success latency
timeout count
consecutive timeout
circuit state: closed/open/half_open
```

规则：

```text
60s 内连续超时 >= 2
或 60s 内超时率 >= 20% 且样本数 >= 5
  -> 熔断 10-20s

熔断到期
  -> 半开，只放 1 个探针
  -> 成功恢复，失败继续熔断
```

路由：

```text
whisper-1 熔断 -> 路由 whisper-2
whisper-2 熔断 -> 路由 whisper-1
全部熔断 -> 快速 503 + Retry-After
FireRed 独立熔断
```

### 3.4 ASR HTTP 分阶段日志

修改 `_post_asr_form_and_extract_text` 或等价函数。

必须记录：

```text
request_start
headers_received
body_read_finished
json_parse_finished
response_close_finished
client_close_finished
```

实现要求：

- 显式 `await response.aread()`。
- 不只依赖 `response.json()`。
- 日志包含 `request_id`、`engine`、`endpoint`、`language`、阶段耗时。

下一次 200 OK 后再卡住，要能直接判断卡在：

```text
body read / json parse / response close / client close / 后续翻译 / DB 日志
```

## 4. Light/Heavy 适配

现有代码已有基础：

```env
VOICELINK_RUNTIME_MODE=all|light|heavy
VOICELINK_RUN_SCHEDULER=true|false
VOICELINK_RUN_SCHEMA_UPGRADE=true|false
```

### Light

配置：

```env
VOICELINK_RUNTIME_MODE=light
VOICELINK_RUN_SCHEDULER=true
VOICELINK_RUN_SCHEMA_UPGRADE=true
UVICORN_WORKERS=4
```

承接：

```text
/v2/auth/*
/v2/api/account/*
/v2/api/billing/*
/v2/api/content/*
/v2/adminapi/*
/v1/manageapi/*
/v2/api/latestVersionInfo
/v2/api/updates/*
/healthz
/readyz
```

注意：admin 放在 light 前必须先做缓存/线程池隔离，否则它仍可能拖慢 auth。

### Heavy

配置：

```env
VOICELINK_RUNTIME_MODE=heavy
VOICELINK_RUN_SCHEDULER=false
VOICELINK_RUN_SCHEMA_UPGRADE=false
UVICORN_WORKERS=2
ASR_QUEUE_LIMIT_ENABLED=true
ASR_BACKEND_STATUS_ENABLED=false
```

承接：

```text
^/(v1|v2)/api/whisper/
^/(v1|v2)/api/func/(translateToEnglish|translateToOtherLanguage|multitranslateToOtherLanguage|photoTranslate|tts|webtranslate)$
^/v2/api/(tts|clone)/
```

Light 和 heavy 必须共享：

```text
SQL_PATH
LIMITER_REDIS_URL
JWT_SECRET_KEY
SESSION_SECRET_KEY
```

## 5. FRP/OpenResty

FRP。若旧 all 容器仍占用 8980，新 light/heavy 默认先用 18980/18981：

```text
81 light: 127.0.0.1:18980 -> 公网本机 18180
81 heavy: 127.0.0.1:18981 -> 公网本机 18181
```

OpenResty：

```nginx
upstream voicelink_light { server 127.0.0.1:18180; }
upstream voicelink_heavy { least_conn; server 127.0.0.1:18181; }

location ~ ^/(v1|v2)/api/whisper(/|$) {
    proxy_pass http://voicelink_heavy;
    proxy_read_timeout 120s;
}

location ~ ^/(v1|v2)/api/func/(translateToEnglish|translateToOtherLanguage|multitranslateToOtherLanguage|photoTranslate|tts|webtranslate)$ {
    proxy_pass http://voicelink_heavy;
    proxy_read_timeout 120s;
}

location ~ ^/v2/api/(tts|clone)(/|$) {
    proxy_pass http://voicelink_heavy;
    proxy_read_timeout 300s;
}

location / {
    proxy_pass http://voicelink_light;
    proxy_read_timeout 30s;
}
```

## 6. 上线顺序

53：

1. 修 admin stats/users 阻塞。
2. 加 ASR 分阶段日志。
3. 加 ASR endpoint 熔断。
4. 恢复 ASR 短队列。
5. 启动 light/heavy 双容器测试。

81：

1. 保留旧 all 容器作为回滚。
2. 启动 light/heavy。
3. FRP 加 18180/18181。
4. OpenResty 先灰度一个 heavy path。
5. 观察 10-15 分钟。
6. 再切全部 heavy path。

## 7. 验收标准

必须满足：

```text
/healthz 在 admin stats 查询期间不再 5-17s
/v2/auth/me 在 ASR 压力下不被拖慢
ASR 503 接近 0.5-2s 快速返回，不再 31s/121s
503 不扣额度
ASR 日志能看出卡在 headers/body/parse/close 哪个阶段
whisper 单 endpoint 熔断后能自动切另一个 endpoint
全部 endpoint 不可用时才 503 + Retry-After
```

## 8. 回滚

light/heavy 异常：

```text
OpenResty 全部切回旧 all upstream
```

ASR 短队列或熔断异常：

```env
ASR_QUEUE_LIMIT_ENABLED=false
ASR_ENDPOINT_CIRCUIT_BREAKER_ENABLED=false
```

admin 缓存异常：

```text
回滚 admin stats/users 改动
但保留 light/heavy 隔离和 worker 提升
```
