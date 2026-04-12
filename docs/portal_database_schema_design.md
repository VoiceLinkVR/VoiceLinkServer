# 官网与统一身份体系数据库设计初稿

## 1. 目标

本设计用于支撑以下目标：

- 邮箱注册、登录、找回密码、邮箱验证
- 统一认证中心
- 官网用户中心
- 爱发电支付、订单、权益发放
- 旧客户端账号与订单平滑迁移

当前版本范围说明：

- 当前版本不做微信登录
- 当前版本不做完整 OAuth2
- 但数据库结构仍建议为未来扩展预留空间

设计原则：

- 内部主身份统一为 `user_id`
- 登录方式与主账号解耦
- 支付、订阅、资源余额从用户主表拆出
- 兼容现有 `request_log` 统计链路
- 尽量支持先兼容、后切换、再清理

---

## 2. 总体分层

建议按 6 个数据域建模：

1. 账号主实体
2. 登录凭据与第三方身份
3. Token / Session / 未来授权对象
4. 支付与订单
5. 订阅与资源权益
6. 资源调用与审计

---

## 3. 核心表设计

## 3.1 用户主表

### `users`

用途：

- 保存平台内部唯一用户实体
- 不直接承载邮箱密码、微信身份、支付订单等外部关系

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 用户主键 |
| `display_name` | varchar(64) | 展示名称 |
| `username_legacy` | varchar(50) null | 历史用户名兼容字段 |
| `status` | varchar(20) | `active / disabled / deleted / pending` |
| `is_admin` | boolean | 是否管理员 |
| `avatar_url` | varchar(512) null | 头像地址 |
| `locale` | varchar(16) null | 用户语言偏好 |
| `timezone` | varchar(64) null | 时区偏好 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |
| `last_login_at` | datetime null | 最后登录时间 |

索引建议：

- `uk_users_username_legacy`
- `idx_users_status`
- `idx_users_created_at`

说明：

- `username_legacy` 只用于历史兼容，不建议继续作为未来登录主键。

---

## 3.2 用户凭据表

### `user_credentials`

用途：

- 保存邮箱密码等“可用于登录的凭据”

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id | 所属用户 |
| `credential_type` | varchar(32) | `email_password / legacy_password` |
| `email` | varchar(255) null | 邮箱 |
| `password_hash` | varchar(255) null | 密码哈希 |
| `password_algo` | varchar(32) null | 哈希算法标识 |
| `is_primary` | boolean | 是否主凭据 |
| `is_verified` | boolean | 是否已验证 |
| `verified_at` | datetime null | 验证时间 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

索引建议：

- `uk_user_credentials_email`
- `idx_user_credentials_user_id`
- `idx_user_credentials_type`

说明：

- 老系统用户名密码可以先落为 `legacy_password`。
- 邮箱登录稳定后，再逐步淡化旧用户名登录。

---

## 3.3 第三方身份表

### `user_identities`

用途：

- 保存微信等第三方身份绑定关系

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id | 所属用户 |
| `provider` | varchar(32) | `wechat_official_account` 等 |
| `provider_app_id` | varchar(64) | 公众号或应用 ID |
| `openid` | varchar(128) null | 微信 openid |
| `unionid` | varchar(128) null | 微信 unionid |
| `nickname` | varchar(128) null | 第三方昵称快照 |
| `avatar_url` | varchar(512) null | 第三方头像快照 |
| `raw_profile_json` | text null | 原始资料 |
| `bound_at` | datetime | 绑定时间 |
| `last_login_at` | datetime null | 最近使用该身份登录时间 |
| `status` | varchar(20) | `active / revoked` |

索引建议：

- `uk_user_identities_provider_openid` on (`provider`, `provider_app_id`, `openid`)
- `uk_user_identities_provider_unionid` on (`provider`, `provider_app_id`, `unionid`)
- `idx_user_identities_user_id`

说明：

- 优先使用 `unionid` 做跨微信体系识别。
- 若没有 `unionid`，退化为 `provider_app_id + openid`。

---

## 3.4 邮箱验证与密码重置

### `email_verification_tokens`

用途：

- 邮箱验证、改邮箱验证

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id | 用户 |
| `email` | varchar(255) | 目标邮箱 |
| `token_hash` | varchar(255) | token 哈希 |
| `purpose` | varchar(32) | `verify_email / change_email` |
| `expires_at` | datetime | 到期时间 |
| `consumed_at` | datetime null | 使用时间 |
| `created_at` | datetime | 创建时间 |

### `password_reset_tokens`

用途：

- 找回密码

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id | 用户 |
| `token_hash` | varchar(255) | token 哈希 |
| `expires_at` | datetime | 到期时间 |
| `consumed_at` | datetime null | 使用时间 |
| `created_at` | datetime | 创建时间 |

---

## 3.5 Web 会话表

### `user_sessions`

用途：

- 官网登录态
- 后台登录态

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `session_id` | varchar(128) | 会话 ID |
| `user_id` | bigint fk users.id | 用户 |
| `session_type` | varchar(32) | `portal / admin` |
| `ip` | varchar(64) null | 登录 IP |
| `user_agent` | varchar(512) null | UA |
| `expires_at` | datetime | 过期时间 |
| `revoked_at` | datetime null | 注销时间 |
| `created_at` | datetime | 创建时间 |
| `last_seen_at` | datetime | 最后活跃时间 |

索引建议：

- `uk_user_sessions_session_id`
- `idx_user_sessions_user_id`
- `idx_user_sessions_expires_at`

---

## 4. OAuth2 表设计

## 4.1 客户端表

### `oauth_clients`

用途：

- 记录官网前端、桌面客户端、内部服务等 OAuth client

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `client_id` | varchar(64) | 客户端 ID |
| `client_secret_hash` | varchar(255) null | 机密客户端密钥哈希 |
| `client_name` | varchar(128) | 客户端名称 |
| `client_type` | varchar(32) | `public / confidential` |
| `redirect_uris_json` | text | 回调地址列表 |
| `allowed_scopes_json` | text | scopes 列表 |
| `is_first_party` | boolean | 是否第一方客户端 |
| `status` | varchar(20) | `active / disabled` |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

## 4.2 授权码表

### `oauth_authorization_codes`

用途：

- Authorization Code + PKCE

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `code` | varchar(128) | 授权码 |
| `user_id` | bigint fk users.id | 授权用户 |
| `client_id` | varchar(64) | OAuth client_id |
| `redirect_uri` | varchar(512) | 回调地址 |
| `scope` | varchar(512) | scope 字符串 |
| `code_challenge` | varchar(128) null | PKCE challenge |
| `code_challenge_method` | varchar(16) null | `S256` |
| `expires_at` | datetime | 到期时间 |
| `consumed_at` | datetime null | 已消费时间 |
| `created_at` | datetime | 创建时间 |

## 4.3 Access Token 表

### `oauth_access_tokens`

用途：

- 可审计 access token

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `token_id` | varchar(128) | jti 或内部 token id |
| `user_id` | bigint fk users.id null | 用户 |
| `client_id` | varchar(64) | 客户端 |
| `grant_type` | varchar(32) | `authorization_code / refresh_token / client_credentials` |
| `scope` | varchar(512) | scopes |
| `expires_at` | datetime | 到期时间 |
| `revoked_at` | datetime null | 撤销时间 |
| `created_at` | datetime | 创建时间 |

说明：

- 如果 access token 本身采用 JWT，也建议存一份审计索引。

## 4.4 Refresh Token 表

### `oauth_refresh_tokens`

用途：

- refresh token 续期

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `token_id` | varchar(128) | token id |
| `user_id` | bigint fk users.id | 用户 |
| `client_id` | varchar(64) | 客户端 |
| `scope` | varchar(512) | scopes |
| `expires_at` | datetime | 到期时间 |
| `rotated_from_token_id` | varchar(128) null | 上一个 refresh token |
| `revoked_at` | datetime null | 撤销时间 |
| `created_at` | datetime | 创建时间 |

---

## 5. 计费与支付表设计

## 5.1 套餐定义表

### `plans`

用途：

- 套餐配置中心

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `plan_code` | varchar(64) | 套餐编码 |
| `display_name` | varchar(128) | 套餐名 |
| `plan_type` | varchar(32) | `subscription / addon` |
| `monthly_price` | decimal(10,2) null | 月价 |
| `resource_quota_daily` | int null | 每日资源额度 |
| `resource_increment` | int null | 加油包增量 |
| `duration_days` | int null | 生效天数 |
| `is_active` | boolean | 是否启用 |
| `sort_order` | int | 排序 |
| `metadata_json` | text null | 附加配置 |

## 5.2 支付意图表

### `payment_intents`

用途：

- 所有支付从“意图”开始，不再直接由用户名密码驱动

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `intent_no` | varchar(64) | 内部支付意图号 |
| `user_id` | bigint fk users.id | 用户 |
| `provider` | varchar(32) | `afdian` |
| `action` | varchar(32) | `renew / upgrade / addon` |
| `plan_id` | bigint fk plans.id null | 套餐 |
| `months` | int null | 月数 |
| `amount` | decimal(10,2) | 支付金额 |
| `currency` | varchar(8) | `CNY` |
| `custom_payload_json` | text null | 业务附加参数 |
| `status` | varchar(20) | `created / pending / paid / failed / cancelled / fulfilled` |
| `expires_at` | datetime null | 意图过期时间 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

索引建议：

- `uk_payment_intents_intent_no`
- `idx_payment_intents_user_id`
- `idx_payment_intents_status`

## 5.3 支付提供方订单表

### `payment_provider_orders`

用途：

- 记录爱发电侧订单映射关系

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `intent_id` | bigint fk payment_intents.id | 对应支付意图 |
| `provider` | varchar(32) | `afdian` |
| `order_no` | varchar(128) null | 平台订单号 |
| `out_trade_no` | varchar(128) null | 商户订单号 |
| `custom_id` | varchar(512) null | 自定义字段 |
| `status` | varchar(20) | `pending / paid / activated / failed / unpaid` |
| `raw_payload_json` | text null | 原始响应 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

说明：

- 未来可将当前 `payment_order` 迁移或并入此表。

## 5.4 支付事件表

### `payment_events`

用途：

- 记录 webhook、手动补单、查询确认等所有支付事件

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `intent_id` | bigint fk payment_intents.id null | 相关支付意图 |
| `provider_order_id` | bigint fk payment_provider_orders.id null | 相关支付单 |
| `event_type` | varchar(64) | 事件类型 |
| `source` | varchar(64) | `webhook / query / admin_manual / retry_job` |
| `payload_json` | longtext | 原始事件数据 |
| `processed_result_json` | longtext null | 处理结果 |
| `created_at` | datetime | 创建时间 |

---

## 6. 订阅与资源权益表设计

## 6.1 订阅表

### `subscriptions`

用途：

- 表示用户当前有效或历史订阅

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id | 用户 |
| `plan_id` | bigint fk plans.id | 套餐 |
| `status` | varchar(20) | `active / expired / cancelled` |
| `starts_at` | datetime | 生效时间 |
| `expires_at` | datetime | 过期时间 |
| `source_intent_id` | bigint fk payment_intents.id null | 来源支付意图 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

## 6.2 加油包余额表

### `addon_balances`

用途：

- 从用户表中剥离加油包余额

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id | 用户 |
| `resource_balance` | int | 当前余额 |
| `resource_total` | int | 历史累计发放 |
| `expires_at` | datetime null | 有效期 |
| `updated_at` | datetime | 更新时间 |

## 6.3 权益发放流水表

### `entitlement_grants`

用途：

- 记录每次支付发放了什么权益，便于幂等和审计

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id | 用户 |
| `intent_id` | bigint fk payment_intents.id | 来源支付意图 |
| `grant_type` | varchar(32) | `subscription_extend / plan_upgrade / addon_credit` |
| `grant_payload_json` | text | 发放内容 |
| `applied_at` | datetime | 实际发放时间 |
| `idempotency_key` | varchar(128) | 幂等键 |

索引建议：

- `uk_entitlement_grants_idempotency_key`
- `idx_entitlement_grants_user_id`

---

## 7. API 资源与审计表

## 7.1 资源调用日志

### `api_request_logs`

建议沿用当前 `request_log` 思路，但命名可逐步收敛。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id null | 用户 |
| `username_legacy` | varchar(50) null | 兼容字段 |
| `ip` | varchar(64) | IP |
| `endpoint` | varchar(128) | 接口 |
| `request_id` | varchar(64) | 请求 ID |
| `status` | varchar(20) | `success / failed / rate_limited / error` |
| `duration_ms` | int | 耗时 |
| `resource_cost` | int | 消耗额度 |
| `quota_source` | varchar(20) | `daily / addon / none` |
| `created_at` | datetime | 时间 |

说明：

- 可以先保留旧表，后续做视图或平移。

## 7.2 认证审计日志

### `auth_audit_logs`

用途：

- 记录登录、登出、邮箱验证、微信绑定、token 撤销等安全事件

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint pk | 主键 |
| `user_id` | bigint fk users.id null | 用户 |
| `event_type` | varchar(64) | 事件类型 |
| `ip` | varchar(64) null | IP |
| `user_agent` | varchar(512) null | UA |
| `result` | varchar(20) | `success / failure` |
| `detail_json` | text null | 附加信息 |
| `created_at` | datetime | 时间 |

---

## 8. 与现有表的映射关系

## 8.1 `user`

现有字段迁移建议：

| 旧字段 | 新去向 |
| --- | --- |
| `username` | `users.username_legacy` |
| `password` | `user_credentials.password_hash` |
| `is_admin` | `users.is_admin` |
| `is_active` | `users.status` |
| `limit_rule` | 先过渡到 `subscriptions/plans`，兼容期可保留 |
| `expiration_date` | `subscriptions.expires_at` |
| `addon_resource_balance` | `addon_balances.resource_balance` |
| `addon_resource_total` | `addon_balances.resource_total` |
| `addon_expiration_date` | `addon_balances.expires_at` |

## 8.2 `payment_order`

现有表迁移建议：

| 旧字段 | 新去向 |
| --- | --- |
| `order_no` | `payment_provider_orders.order_no` |
| `out_trade_no` | `payment_provider_orders.out_trade_no` |
| `custom_id` | `payment_provider_orders.custom_id` |
| `username` | 转换成 `payment_intents.user_id` |
| `action` | `payment_intents.action` |
| `amount` | `payment_intents.amount` |
| `status` | `payment_intents.status` 或 `payment_provider_orders.status` |
| `activation_source` | `payment_events.source` |

## 8.3 `request_log`

迁移策略：

- 第一阶段可直接保留
- 第二阶段增加 `user_id`
- 第三阶段再考虑是否重命名

---

## 9. 迁移建议

## 9.1 迁移顺序

建议顺序：

1. 新建表，不先删旧表
2. 新登录和新支付链路写入新表
3. 旧接口继续兼容旧表或双写
4. 数据回填
5. 前端和客户端切流
6. 下线旧表依赖

## 9.2 双写策略

兼容期内建议：

- 登录事件写新审计表
- 支付事件写新支付表
- 资源调用仍写旧 `request_log`
- 订阅状态先从旧 `user` 派生，逐步切到 `subscriptions`

## 9.3 风险控制

高风险点：

- 旧用户名与新邮箱冲突
- 一个微信身份误绑两个账号
- 旧订单重复激活
- 资源余额从用户表切出时出现统计不一致

建议：

- 所有支付权益发放必须走幂等键
- 所有绑定操作必须有唯一索引兜底
- 所有迁移脚本先只读校验，再执行写入

---

## 10. 推荐技术决策

- 数据库建议从 SQLite 逐步切换到 MySQL 或 PostgreSQL
- 数据库迁移建议引入 Alembic
- token 不直接存明文，存哈希或内部 token id
- 所有第三方身份表必须加唯一约束
- 支付意图与权益发放必须分表，不建议继续把“支付状态”和“权益结果”混在一行里

---

## 11. 实施优先级

### P0

- `users`
- `user_credentials`
- `user_identities`
- `oauth_clients`
- `oauth_authorization_codes`
- `oauth_refresh_tokens`
- `payment_intents`
- `payment_provider_orders`
- `entitlement_grants`

### P1

- `user_sessions`
- `email_verification_tokens`
- `password_reset_tokens`
- `auth_audit_logs`
- `subscriptions`
- `addon_balances`

### P2

- `payment_events`
- 更细粒度的风控与登录审计表

---

## 12. 结论

本轮数据库重构的关键不是“多建几张表”，而是完成三件事：

1. 主账号与登录方式解耦
2. 支付与权益发放解耦
3. 旧客户端兼容与新官网体系并行

只要这三件事做对，后面的官网、微信登录、邮箱注册登录和 OAuth2 都会顺畅很多。
