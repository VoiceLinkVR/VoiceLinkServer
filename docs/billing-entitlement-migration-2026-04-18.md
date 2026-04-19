# 计费与权益拆分改造说明

## 背景

本次改造的目标是把以下三类逻辑拆开：

1. 订单计费事实
2. 当前生效套餐权益
3. 加油包钱包余额

旧实现里，这几类信息长期混在 `limit_rule`、`payment_order.plan_id` 和用户表字段里，导致几个问题：

- 订单激活时会从 `custom_id`、支付金额、当前 `limit_rule` 反推套餐
- 切换计费模式后，老订单可能被按“当前目录”重新解释
- 套餐和加油包虽然运行时部分独立，但数据层并没有彻底分开
- 免费已注册用户做首单订阅时，会被旧逻辑误判，首购链路不稳定

## 本次改动

### 1. 订单层新增快照字段

在 `payment_order` 增加了以下字段：

- `order_kind`
- `pricing_mode_snapshot`
- `plan_ref`
- `plan_price_snapshot`
- `months_snapshot`
- `expected_amount_snapshot`
- `paid_amount_snapshot`
- `daily_quota_snapshot`
- `rate_limit_rule_snapshot`
- `addon_resource_snapshot`
- `catalog_version_snapshot`
- `entitlement_snapshot_json`

现在实际计费的审计口径以 `payment_order` 快照为准，不再依赖 `limit_rule` 反推。

### 2. 新增当前权益表

新增两张表：

- `user_subscription_state`
- `user_addon_wallet`

职责如下：

- `user_subscription_state`：保存当前正在生效的套餐权益
- `user_addon_wallet`：保存当前加油包钱包余额和累计购买量

### 3. 用户表改成镜像缓存

`user` / `v2_user` 上原有的：

- `limit_rule`
- `expiration_date`
- `addon_resource_balance`
- `addon_resource_total`
- `addon_expiration_date`

现在继续保留，但它们已经是镜像缓存字段，不再是唯一业务真相。

业务真相变成：

- 实际计费：`payment_order`
- 当前套餐权益：`user_subscription_state`
- 当前加油包余额：`user_addon_wallet`

### 4. 激活逻辑改成“订单快照优先”

爱发电回调激活时，优先读取本地订单快照：

- action
- plan_price
- months
- expected_amount
- addon_resource_amount

只有历史旧单没有快照时，才回退到旧 `custom_id` 解析逻辑。

### 5. 历史套餐兼容标识

为了处理类似 `15 元 / 4500/day` 这种历史权益，新增了可解析的 legacy plan 标识：

- `legacy-m1500-q4500`

这样即使当前目录已经不是当时的档位定义，历史权益仍然能被正确识别，不会被误判成别的套餐。

### 6. 修复免费已注册用户首购判定

旧逻辑把“免费但已注册”的用户不当作首购用户，导致首单订阅在激活时可能报：

- `续费不支持升级套餐，请选择升级操作`

本次已修正为：

- 免费态用户也视为首购态

## 数据迁移口径

迁移由现有 `schema_upgrade` 自动完成，主要做了两类事：

### 1. 结构迁移

- 给 `payment_order` 加新快照字段
- 新建 `user_subscription_state`
- 新建 `user_addon_wallet`

### 2. 数据回填

- 老用户：按现有账号状态回填当前权益
- 老订单：优先按 `custom_id` / `raw_payload` 回填快照
- 已存在历史非标准套餐时，生成 `legacy-m{price_cents}-q{daily_quota}` 标识
- 最新已激活订阅单会优先回填到当前权益表，避免被当前资费目录误解释

## 当前生效规则

现在系统的生效口径如下：

### 实际计费

以 `payment_order` 的快照字段为准。

### 当前套餐权益

以 `user_subscription_state` 为准。

### 当前加油包余额

以 `user_addon_wallet` 为准。

### 运行时限额展示与扣减

- 展示和接口对外仍会带 `limit_rule`
- 但它只是由当前权益表同步出来的镜像字段

## 部署情况

已部署到测试服务器：

- 主机：`192.168.2.53`
- 容器：`vrcls-preview-backend`
- 目录：`/opt/vrcls-preview-backend`

部署方式：

- 备份远端后端源码
- 同步本次变更文件到 `/opt/vrcls-preview-backend/app`
- 执行 `docker compose up -d --build backend`

## 联调测试结果

已在测试服完成以下链路验证。

### 1. 首单订阅

测试结果：

- 订单创建成功
- 爱发电 webhook 模拟支付成功
- `payment_order` 正常变为 `activated`
- `user_subscription_state` 正常写入套餐
- `v2_user.limit_rule` 同步为镜像值

验证结果：

- 套餐从 `Free` 变为 `CNY 5.00 / month`
- 日额度从 `800` 变为 `3000`

### 2. 加油包

测试结果：

- 加油包订单创建成功
- webhook 激活成功
- `user_addon_wallet.balance = 5000`
- 套餐档位未被改写

验证结果：

- 当前套餐仍是 `5 元档`
- 加油包余额变为 `5000`

### 3. 升级

测试结果：

- 从 `5 元档` 升级到 `10 元档` 成功
- `user_subscription_state` 正常升级到 `sub-m1000-q6000`
- 加油包余额未丢失

验证结果：

- 套餐变为 `10 元档`
- 日额度变为 `6000`
- 加油包余额仍为 `5000`

## 当前状态总结

当前测试服已经达到以下状态：

- 订单计费事实与当前权益已拆层
- 套餐与加油包已独立存储
- 爱发电首单订阅可正常激活
- 爱发电加油包可正常激活
- 爱发电升级可正常激活
- 升级不会清掉加油包余额
- 当前接口返回的数据已经能反映新的权益状态

## 额外说明

### 1. webhook 转发

测试服当前仍启用了：

- `AFDIAN_WEBHOOK_FORWARD_ENABLED=True`

目标转发地址在本次测试中超时，但不会阻塞本地激活，当前属于非阻塞告警。

### 2. 预览环境代码底子比本地旧

测试服预览后端比本地仓库落后了一些，因此本次部署过程中额外补齐了这些依赖文件：

- `core/rate_limiter.py`
- `core/security_policy.py`
- `core/config.py`

### 3. schema upgrade 在多 worker 下重复触发

当前预览容器启用了多 worker，`schema_upgrade` 会在每个 worker 启动时执行，但因为已经有 MySQL 启动锁，所以不会产生并发写坏库的问题，只是日志会重复出现多次 `Schema upgrade completed`。
