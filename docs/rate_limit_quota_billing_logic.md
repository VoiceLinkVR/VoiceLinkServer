# 限速、额度与改价逻辑审查

本文基于当前仓库代码梳理运行逻辑，重点覆盖：

- 请求限速：控制单位时间内请求频率。
- 资源额度：控制每日额度和加油包额度消耗。
- 套餐与改价：管理员、支付订单、套餐目录如何影响用户权益。

相关核心文件：

- `src/db/models.py`
- `src/core/rate_limiter.py`
- `src/core/quota_service.py`
- `src/core/entitlement_service.py`
- `src/core/plan_catalog.py`
- `src/core/site_config_service.py`
- `src/routers/api.py`
- `src/routers/v2_tts.py`
- `src/routers/admin_api.py`
- `src/routers/user_center.py`
- `src/routers/portal_api.py`
- `src/main.py`

## 1. 核心开关

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `LIMIT_ENABLE` | `True` | 是否启用限速器。关闭后不做限速检查。 |
| `LIMITER_REDIS_URL` | `redis://localhost:6379/0` | limits 的存储后端；初始化失败会退回内存。 |
| `LEGACY_CLIENT_COMPAT` | `False` | 关键开关。`False` 表示启用新额度模式；`True` 表示兼容旧客户端，主要靠限速规则里的 `/day` 做日额度。 |
| `FREE_PLAN_LIMIT_RULE` | `800/day;8/minute` | 免费套餐基础规则的环境兜底。 |
| `DEFAULT_TEST_USER_IP_DAILY_QUOTA` | `800` | 默认测试用户按 IP 统计的日额度。 |
| `DEFAULT_TEST_USER_IP_RATE_LIMIT` | `8/minute` | 默认测试用户按 IP 限速规则。 |
| `PLAN_CATALOG_PATH` | `src/data/plan_catalog.json` | 套餐目录文件路径。 |
| `GUEST_AUTH_ENABLED` | `True` | 是否允许 `/v2/auth/guest` 获取默认测试用户 token。 |
| `API_V2_BILLING_ENABLED` | `False` | 是否开放门户侧支付相关 API。 |

## 2. 数据库字段

### 2.1 用户运行时字段

`user` 是旧账号表，`v2_user` 是新账号表。两张表都有一组限速/额度相关字段。

| 字段 | 表 | 含义 |
| --- | --- | --- |
| `username` | `user`, `v2_user` | 用户名。旧表也会用它查用户。 |
| `user_uuid` | `v2_user` | 新账号主身份。v2 统计和权益表主要按它关联。 |
| `is_default_test_user` | `v2_user` | 默认测试用户标记。该用户按 IP 计量，而不是按用户计量。 |
| `is_active` | `user`, `v2_user` | 是否可用。v2 过期后会降级免费，不是简单禁用。 |
| `expiration_date` | `user`, `v2_user` | 订阅到期时间。v2 过期时会重置到免费套餐。 |
| `limit_rule` | `user`, `v2_user` | 运行时缓存规则，格式通常是 `plan_id=...;6000/day;8/minute`。 |
| `boost_limit_rule` | `user`, `v2_user` | 临时提升规则。有效期内覆盖 `limit_rule`。 |
| `boost_expiration_date` | `user`, `v2_user` | 临时提升到期时间。 |
| `addon_resource_balance` | `user`, `v2_user` | 加油包余额缓存。 |
| `addon_resource_total` | `user`, `v2_user` | 历史购买加油包总量缓存。 |
| `addon_expiration_date` | `user`, `v2_user` | 加油包到期时间缓存。 |

重点：`v2_user.limit_rule` 不是唯一事实来源。v2 新逻辑中，权益源数据在下面两张表，`v2_user` 字段是同步后的运行时缓存。

### 2.2 v2 权益源表

`v2_user_subscription_state` 是订阅权益源。

| 字段 | 含义 |
| --- | --- |
| `user_uuid` | 关联用户。唯一。 |
| `plan_ref` | 套餐 ID，例如 `free`、`sub-m1000-q6000`。 |
| `pricing_mode` | `free`、`fixed_tiers` 或 `ladder`。 |
| `plan_price` | 月价格。 |
| `daily_quota` | 每日资源额度。 |
| `rate_limit_rule` | 频率限速规则，不包含 `plan_id=` 和 `/day`。例如 `8/minute`。付费套餐当前可为空。 |
| `status` | `free`、`active`、`inactive`。 |
| `expires_at` | 订阅到期时间。免费套餐为 `NULL`。 |
| `source_order_id` | 最近来源订单 ID。 |
| `snapshot_json` | 生成该状态时的快照和来源。 |

`v2_user_addon_wallet` 是加油包权益源。

| 字段 | 含义 |
| --- | --- |
| `user_uuid` | 关联用户。唯一。 |
| `balance` | 加油包余额。 |
| `total_purchased` | 累计购买量。 |
| `expires_at` | 加油包到期时间，通常跟随订阅到期。 |
| `last_order_id` | 最近加油包订单。 |
| `snapshot_json` | 变更快照。 |

`sync_user_cache_from_entitlements()` 会把这两张表同步回 `v2_user`：

- `v2_user.limit_rule = plan_id + daily_quota + rate_limit_rule`
- `v2_user.expiration_date = subscription_state.expires_at`
- `v2_user.is_active = subscription_state.status != "inactive"`
- `v2_user.addon_resource_balance = addon_wallet.balance`
- `v2_user.addon_expiration_date = addon_wallet.expires_at`

### 2.3 支付订单快照

`v2_payment_order` 保存支付订单和权益快照。

| 字段 | 含义 |
| --- | --- |
| `order_kind` | `subscription` 或 `addon`。 |
| `pricing_mode_snapshot` | 下单时的定价模式。 |
| `plan_ref` / `plan_id` | 下单时目标套餐。 |
| `plan_price_snapshot` | 下单时月价格。 |
| `months_snapshot` | 下单月数。 |
| `expected_amount_snapshot` | 系统期望支付金额。 |
| `paid_amount_snapshot` | 实际支付金额。 |
| `daily_quota_snapshot` | 下单时每日额度。 |
| `rate_limit_rule_snapshot` | 下单时频率限速规则。 |
| `addon_resource_snapshot` | 加油包资源量。 |
| `catalog_version_snapshot` | 套餐目录版本。 |
| `entitlement_snapshot_json` | 完整权益快照。 |

支付激活时优先读取订单快照，避免管理员后来改套餐目录导致已创建订单被重新按新价格解释。

### 2.4 日志与用量统计表

`v2_request_audit_log`：每个 v2 请求都写审计日志。

关键字段：

- `request_id`
- `username`
- `user_uuid`
- `actor_type`
- `rate_limit_scope`
- `quota_scope`
- `endpoint`
- `status`
- `status_code`
- `resource_cost`
- `resource_deducted`
- `quota_source`
- `extra_payload`

`v2_usage_event`：只记录需要重点追踪的事件，包括已扣费、被限速、5xx。

`v2_subject_daily_usage`：每日聚合用量。新额度模式下，v2 用户当天已用日额度主要看这里的 `daily_resource_used`。

`request_log`：旧表。旧账号或非 v2 请求仍会写入，用于兼容旧统计。

## 3. 套餐目录

套餐目录来自 `src/data/plan_catalog.json`。当前内容：

| 套餐 | 价格 | 日额度 | 频率限速 |
| --- | ---: | ---: | --- |
| free | 0 | 800 | `8/minute` |
| default_test | 0 | 800 | `8/minute` |
| sub-m500-q3000 | 5 | 3000 | 空 |
| sub-m1000-q6000 | 10 | 6000 | 空 |
| sub-m2000-q12000 | 20 | 12000 | 空 |
| sub-m5000-q30000 | 50 | 30000 | 空 |
| sub-m10000-q60000 | 100 | 60000 | 空 |
| addon_5 | 5 | 5000 加油包资源 | 365 天 |

`build_limit_rule(base_rule, plan_id, daily_quota)` 会生成：

```text
plan_id=<plan_id>;<daily_quota>/day;<base_rule>
```

如果 `base_rule` 为空，付费用户会得到类似：

```text
plan_id=sub-m1000-q6000;6000/day
```

当前代码在请求限速时会先丢弃 `plan_id=...` 这类元数据；新额度模式下还会丢弃 `/day`，因为日额度由 quota 系统处理。

## 4. 权益生效顺序

### 4.1 读取用户有效规则

`get_effective_user_limit_rule(user)`：

1. 如果 `boost_expiration_date` 未过期且 `boost_limit_rule` 非空，返回 `boost_limit_rule`。
2. 否则返回 `user.limit_rule`。
3. 如果为空，返回免费套餐规则。

v2 用户一般由 `sync_user_cache_from_entitlements()` 把权益源同步到 `v2_user.limit_rule`。

### 4.2 v2 用户过期处理

`fallback_expired_v2_user_to_free()`：

1. 如果 `expiration_date` 为空或未过期，不处理。
2. 如果已过期，调用 `reset_user_entitlements_to_free()`。
3. 重置订阅为免费套餐，默认会清空加油包。
4. 同步回 `v2_user.limit_rule`、`expiration_date`、`addon_*`。

触发点：

- v2 登录。
- v2 token 刷新。
- v2 认证依赖解析用户时。
- 每天 UTC 00:00 的后台任务。

旧 `user` 表账号当前不会按到期时间自动降级，是否可用主要看 `is_active`。

## 5. 请求限速逻辑

### 5.1 全局匿名限速

在 `request_logging_middleware` 中，如果请求未认证，并且不是豁免路径，会创建默认限速：

```text
3000/hour
```

维度：

```text
scope = shared_limit_scope
key = client_ip
```

豁免路径包括：

- `/v2/auth`
- `/v2/api/account`
- `/v2/api/billing`
- `/v2/api/content`
- `/v2/adminapi`
- `OPTIONS`
- `/v2/auth/me`

### 5.2 登录和认证尝试限速

`enforce_auth_attempt_rate_limit()` 用在登录、管理员登录、guest 登录、旧账号迁移等入口。

常见配置：

- `AUTH_LOGIN_IP_LIMIT = 20/10 minutes`
- `AUTH_LOGIN_IDENTITY_LIMIT = 8/10 minutes`
- `LEGACY_LOGIN_IP_LIMIT = 20/10 minutes`
- `LEGACY_LOGIN_IDENTITY_LIMIT = 8/10 minutes`
- `ADMIN_LOGIN_IP_LIMIT = 10/10 minutes`
- `ADMIN_LOGIN_IDENTITY_LIMIT = 5/10 minutes`
- `GUEST_LOGIN_IP_LIMIT = 30/10 minutes`

认证限速的扣次数条件是状态码 `100-499`。也就是成功登录和认证失败都会计入；5xx 不计入。

### 5.3 用户 API 限速

收费 API 使用：

```python
Depends(enforce_user_rate_limit)
```

流程：

1. v2 路径只解析 `V2User`；解析不到就返回 401，不再回退到旧 `User`。
2. 写入 `request.state.quota_user`、`quota_db`、`quota_username`、`quota_client_ip`。
3. 取 `get_effective_user_limit_rule(user)`。
4. 去掉 `plan_id=...` 这类元数据。
5. 默认测试用户单独处理：
   - 使用 `DEFAULT_TEST_USER_IP_RATE_LIMIT`，默认 `8/minute`。
   - scope 为 `anonymous_ip:<client_ip>`。
   - key 包含 `client_ip + UTC day`。
6. 普通用户：
   - 如果 `LEGACY_CLIENT_COMPAT=False`，去掉 `/day` 规则，只保留分钟/小时等频率规则。
   - 如果去掉后没有分钟/小时等频率规则，不创建用户限速上下文，不回退默认限速。
   - v2 路径的 scope/key 统一使用 `user_uuid`，不再回退 username。
   - 非 v2 旧用户路径仍使用 `user_uuid or username`。
7. 进入业务前先 `test()`。
8. 请求结束后，只有状态码为 `200` 时才 `hit()`。

限速异常统一返回 HTTP `430`：

```json
{
  "error": "Too many request",
  "LimitRules": "...",
  "limit": "..."
}
```

### 5.4 限速成本

路由进入后调用：

```python
set_user_context_cost(request, cost)
```

它会：

1. 更新当前用户限速上下文的 `cost`。
2. 再做一次 `check()`，避免高成本请求绕过限速。
3. 如果是新额度模式，准备 quota allocation。

如果某个收费接口只挂了 `enforce_user_rate_limit`，但没有调用 `set_user_context_cost()`，新额度模式下不会扣资源额度。

## 6. 额度扣减逻辑

### 6.1 新额度模式

当 `LEGACY_CLIENT_COMPAT=False` 时：

```python
is_new_quota_mode_enabled() == True
```

此时日额度不靠限速器的 `/day` 扣，改由 `quota_service` 管。

`set_user_context_cost()` 会调用 `prepare_quota_allocation()`：

1. 计算本次成本，最小为 1。
2. 构建额度快照：
   - `daily_limit`
   - `today_daily_used`
   - `remaining_daily`
   - `addon_resource_balance`
   - `addon_expiration_utc`
3. 如果日额度足够，生成 `requested_source=daily` 的 allocation。
4. 如果日额度不足但加油包足够，生成 `requested_source=addon` 的 allocation。
5. 两者都不足，立即抛 HTTP `430`：

```json
{
  "code": "resource_quota_exceeded",
  "message": "Insufficient resource quota",
  "cost": 2,
  "daily_limit": 6000,
  "today_daily_used": 5999,
  "remaining_daily": 1,
  "addon_resource_balance": 0
}
```

请求业务执行完成后，`request_logging_middleware` 在 finally 中提交额度：

1. 只在非限速触发、存在 pending allocation、状态码 `200-399` 时提交。
2. `commit_quota_allocation()` 重新锁定并读取用户。
3. 再构建一次额度快照，避免并发超用。
4. 如果日额度仍足够，最终来源为 `daily`。
5. 否则如果加油包足够，扣减 `addon_resource_balance`，最终来源为 `addon`。
6. 否则把响应改为 HTTP `430`。
7. 写审计日志和每日统计。

日额度本身不直接在用户表扣字段，而是通过 `v2_subject_daily_usage.daily_resource_used` 统计。

### 6.2 旧兼容模式

当 `LEGACY_CLIENT_COMPAT=True` 时：

- 不启用新 quota allocation。
- `set_user_context_cost()` 在兼容层会把成本压成 `1`。
- middleware 在状态码满足条件时把 `resource_cost` 记到日志。
- `/day` 限制仍留在限速规则中，由 limits 存储计数。
- 加油包额度不会参与本次请求扣减。

### 6.3 额度来源

`quota_source` 有三种：

| 值 | 含义 |
| --- | --- |
| `daily` | 消耗订阅每日额度。 |
| `addon` | 消耗加油包。 |
| `none` | 未扣额度。 |

`resource_deducted=True` 表示本次请求真正扣了资源。

### 6.4 默认测试用户

默认测试用户逻辑：

- `actor_type=anonymous_test`
- `quota_scope=ip`
- 用 IP 作为每日用量 subject。
- 日额度来自 `DEFAULT_TEST_USER_IP_DAILY_QUOTA`。
- 加油包余额固定视为 0。

## 7. 资源成本表

收费接口必须同时满足：

1. 路由依赖 `Depends(enforce_user_rate_limit)`。
2. 路由内调用 `set_user_context_cost()` 或封装函数。

当前主要成本：

| 接口 | 成本 |
| --- | ---: |
| `/v1/api/whisper/transcriptions`、`/v2/api/whisper/transcriptions` | 1 |
| `/v1/api/whisper/translations`、`/v2/api/whisper/translations` | 2 |
| `/v1/api/func/translateToEnglish`、`/v2/api/func/translateToEnglish` | 2 |
| `/v1/api/func/translateToOtherLanguage`、`/v2/api/func/translateToOtherLanguage` | 2 |
| `/v1/api/func/multitranslateToOtherLanguage`、`/v2/api/func/multitranslateToOtherLanguage` | `1 + 目标语言数量`，即 2 到 4 |
| `/v1/api/whisper/multitranscription`、`/v2/api/whisper/multitranscription` | 1 |
| `/v1/api/func/photoTranslate`、`/v2/api/func/photoTranslate` | `1 + 目标语言数量`，即 2 到 4 |
| `/v1/api/func/tts`、`/v2/api/func/tts` | 1 |
| `/v1/api/func/webtranslate`、`/v2/api/func/webtranslate` | 目标语言数量，即 1 到 3 |
| `/v2/api/tts` | 1 |
| `/v2/api/clone` | 2 |

## 8. 管理员改价和套餐变更

管理员有三类入口会影响价格、额度或限速。

### 8.1 修改全局套餐目录

接口：

```text
PUT /v2/adminapi/configs
```

`site_config_service` 把以下 key 映射到 `plan_catalog.json`：

- `billing.subscription_mode`
- `billing.ladder.base_price`
- `billing.ladder.min_monthly_price`
- `billing.ladder.max_monthly_price`
- `billing.ladder.price_step`
- `billing.ladder.base_daily_quota`
- `billing.ladder.daily_quota_step`
- `free_plan.daily_quota`
- `free_plan.resource_quota`
- `free_plan.rate_limit`
- `default_test_plan.daily_quota`
- `default_test_plan.resource_quota`
- `default_test_plan.rate_limit`
- `tiers`
- `addons`

处理逻辑：

1. 每个值先走 `normalize_catalog()` 校验。
2. `tiers` 和 `addons` 可以用 JSON 数组编辑。
3. `tiers[].monthly_price` 必须大于 0 且价格唯一。
4. `tiers[].daily_quota` 必须大于 0。
5. `addons[].price`、`resource_amount`、`expires_in_days` 都必须大于 0。
6. 保存时写入 `PLAN_CATALOG_PATH`。
7. 同时写 `v2_site_config` 和 `v2_site_config_audit_log`。

影响范围：

- 影响后续套餐列表、下单、订单快照、管理员手动修正时的归一化。
- 不会自动批量重算现有用户的 `v2_user_subscription_state`。
- 现有用户只有在手动改权益、支付激活、过期降级、重建权益时才会重新同步。

### 8.2 管理员直接改用户基础字段

接口：

```text
PATCH /v2/adminapi/users/{user_id}
```

可改：

- `username`
- `email`
- `password`
- `is_admin`
- `is_active`
- `limit_rule`
- `expiration_date`

如果改了 `limit_rule` 或 `expiration_date`，最后会调用：

```python
rebuild_user_entitlement_state_from_legacy_fields(db, user)
```

该函数会从 `user.limit_rule` 和 `expiration_date` 反推出套餐，然后写入权益源表，再同步回 `v2_user`。

注意：

- 这是兼容旧字段的入口，不是最精确的改价入口。
- 反推套餐时会按当前套餐目录匹配 `plan_id` 或 `/day`。
- 如果 `limit_rule` 不在当前目录里，可能归到免费或最近档位。

### 8.3 管理员手动修正用户权益

接口：

```text
PATCH /v2/adminapi/users/{user_id}/entitlements
```

请求字段：

| 字段 | 作用 |
| --- | --- |
| `plan_price` | 月价格。管理员只需要选择或填写月价，后端按套餐目录生成套餐标识和日额度。 |
| `pricing_mode` | `free`、`fixed_tiers`、`ladder`。 |
| `subscription_status` | `free`、`active`、`inactive`。 |
| `subscription_expires_at` | 订阅到期时间。 |
| `rate_limit_rule` | 频率限速规则，不应包含 `plan_id` 或 `/day`。 |
| `addon_balance` | 加油包余额。 |
| `addon_total_purchased` | 加油包累计购买量。 |
| `addon_expires_at` | 加油包到期时间。 |
| `clear_addon` | 清空加油包余额和到期时间。 |
| `reset_to_free` | 重置订阅为免费套餐。 |

处理逻辑：

1. 读取或创建 `subscription_state` 和 `addon_wallet`。
2. 如果 `reset_to_free=True`：
   - 订阅重置为免费套餐。
   - `clear_addon=True` 时同时清空加油包。
3. 否则使用请求值；没传的字段沿用当前权益状态。
4. 如果显式传入 `plan_price` 或 `pricing_mode`，后端重新按套餐目录构建订阅快照。
5. 如果付费快照但状态仍是 `free`，自动改为 `active`。
6. 调用 `build_subscription_snapshot_for_existing_values()` 归一化套餐：
   - `fixed_tiers` 下由月价匹配固定档位，并生成对应 `plan_ref` 和 `daily_quota`。
   - `ladder` 下按阶梯价格范围和步长归一化。
   - 如果价格小于等于 0，归为免费。
7. 不再接受管理员手填 `plan_ref` 或 `daily_quota`，这些字段只由后端从套餐目录推导。
8. `rate_limit_rule` 如果显式传空字符串，付费套餐允许为空。
9. 如果最终是免费状态，强制写回免费套餐价格、额度、状态、到期时间。
10. 加油包字段按请求覆盖或清空。
11. 写 `snapshot_json.source = admin_manual_correction`。
12. 调用 `sync_user_cache_from_entitlements()` 同步回 `v2_user`。

管理员手动修正后的 `v2_user.limit_rule` 示例：

```text
plan_id=sub-m1000-q6000;6000/day
```

如果管理员设置了 `rate_limit_rule=20/minute`：

```text
plan_id=sub-m1000-q6000;6000/day;20/minute
```

## 9. 支付购买和激活

### 9.1 门户下单

接口：

```text
POST /v2/api/billing/order-intents
```

前置：

- `API_V2_BILLING_ENABLED=True`
- 用户必须已登录 v2。
- 用户必须完成迁移。
- 请求必须带当前有效的 `guideToken`。

下单流程：

1. `_resolve_checkout_request()` 解析动作：`renew`、`upgrade`、`addon`。
2. 如果是订阅：
   - `planId` 格式为 `subscription:<price>:<months>`。
   - 目标价格必须存在于当前套餐目录或符合 ladder 规则。
   - 降级不允许。
   - 升级时月数按剩余订阅时间折算。
3. 如果是加油包：
   - `planId` 格式为 `addon:<amount>:1`，或使用 `addonAmount`。
   - 金额必须符合加油包步长。
   - 必须有有效订阅。
4. `_validate_checkout_request_against_guide()` 用实时 guide 再校验一次可购买动作和金额。
5. `_create_order_intent()` 创建 `v2_payment_order`：
   - 生成 custom id。
   - 写订单金额。
   - 调用 `set_payment_order_snapshot()` 保存下单时权益快照。

金额计算：

| 动作 | 金额 |
| --- | --- |
| 首购/续费 | `目标月价 * 月数` |
| 升级 | `(目标月价 - 当前月价) * 折算月数` |
| 加油包 | 加油包金额 |

### 9.2 订单激活

激活入口：

- 爱发电 webhook 后台任务。
- 管理员 `/v2/adminapi/orders/activate`。

核心函数：

```python
activate_remote_afdian_order()
```

激活流程：

1. 同步远端订单到本地 `v2_payment_order`。
2. 如果订单已激活，走幂等返回。
3. 如果远端订单未支付，拒绝。
4. 解析 custom id，要求：
   - 未过期。
   - `kind=portal_billing`。
   - 有 `user_uuid`。
5. 找到 v2 用户。
6. 优先读取订单快照里的 action、months、plan_price、expected_amount。
7. 校验实际支付金额等于系统期望金额。
8. 根据 action 更新权益：
   - `renew`：首购/免费/过期按目标套餐开通；已有付费用户续费不能升档，到期时间按月延长并加 1 天宽限。
   - `upgrade`：目标价必须高于当前价；旧套餐剩余时间按 `当前价 / 目标价` 折算，再叠加本次支付可购买天数。
   - `addon`：必须在新额度模式且用户有有效订阅；加油包余额增加，过期时间跟随订阅。
9. 写回订单状态 `activated`、激活来源、支付快照和 raw payload。

## 10. 日志和统计写入

请求结束后 middleware 统一写日志。

v2 请求：

- 一定写 `v2_request_audit_log`。
- 已扣费、限速、5xx 会写 `v2_usage_event`。
- 非排除路径会聚合进 `v2_endpoint_hourly_stats` 和 `v2_subject_daily_usage`。

v2 统计排除路径：

- `/v2/auth`
- `/v2/api/account`
- `/v2/api/billing`
- `/v2/api/content`
- `/v2/adminapi`
- 部分版本、模型、翻译能力查询接口。

旧请求或旧用户请求：

- 仍写 `request_log`。

## 11. 当前审查关注点

### 11.1 生产回滚版本风险

当前仓库代码已经在 `parse_limit_string()` 和 `enforce_user_rate_limit()` 里跳过 `plan_id=...` 元数据。普通用户没有分钟/小时等频率规则时，不再回退默认限速。

如果线上运行的是未包含该修复的回滚版本，付费账号的规则如果只有：

```text
plan_id=sub-m1000-q6000
```

或解析失败后没有有效规则，会出现无有效用户限速的风险。生产日志里出现 `No valid limit rules were parsed from 'plan_id=...'` 就属于这种情况。

### 11.2 当前付费套餐默认没有频率限速

当前套餐目录里付费档位的 `rate_limit` 都是空。新额度模式会去掉 `/day`，只把分钟/小时等频率规则交给限速器。

因此当前代码下，付费用户如果没有手工设置 `rate_limit_rule`，就只有额度扣减，没有用户级频率限速。如果需要付费档位有不同频率，需要在 `tiers[].rate_limit` 或用户权益 `rate_limit_rule` 里明确配置。

### 11.3 新额度模式依赖每个收费接口显式设置成本

新额度模式下，`enforce_user_rate_limit` 只负责用户解析和频率限速上下文。真正额度检查发生在：

```python
set_user_context_cost(request, cost)
```

所以新增收费接口时必须同时做两件事：

```python
current_user = Depends(enforce_user_rate_limit)
set_user_context_cost(request, cost)
```

否则该接口可能只限速、不扣每日额度。

### 11.4 加油包余额有缓存与源表同步风险

购买加油包时会写 `v2_user_addon_wallet`，并同步到 `v2_user.addon_resource_balance`。

但请求扣加油包时，`commit_quota_allocation()` 当前扣的是用户表缓存字段 `addon_resource_balance`，没有同步扣 `v2_user_addon_wallet.balance`。后续如果调用 `ensure_user_entitlement_state()`，可能会用 wallet 源表把用户表缓存字段重新覆盖。

审查时建议重点确认：加油包扣减是否应该同时更新 `v2_user_addon_wallet.balance`，并以 wallet 作为唯一来源。

### 11.5 v2 扣加油包时先按旧表 username 查用户

`commit_quota_allocation()` 当前先执行：

```python
db.query(User).filter(User.username == allocation.username)
```

找不到才查 `V2User.user_uuid` 和 `V2User.username`。如果旧表和 v2 表存在相同 username，理论上可能扣到旧表用户缓存字段。

审查时建议确认 v2 allocation 是否应优先按 `user_uuid` 查 `V2User`。

### 11.6 改套餐目录不会自动改已有用户

管理员通过配置页改 `tiers`、`addons`、阶梯参数后：

- 新下单会使用新目录。
- 手动修正用户权益会使用新目录。
- 已有用户不会自动批量变价或变额度。

如果要让存量用户跟随新价格，需要额外设计批量迁移逻辑。

### 11.7 管理员手动修正不再接受派生字段

`plan_ref` 和 `daily_quota` 是由套餐目录推导出来的派生字段，不再作为管理员手动修正接口输入。管理员调整订阅时只传月价或状态/到期时间，后端负责生成 `plan_ref`、`daily_quota` 和最终 `limit_rule`。
