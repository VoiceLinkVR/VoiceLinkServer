# 旧 SQL 数据迁移与当前额度规则说明

## 文档目的

本文说明两件事：

1. 旧 SQL 数据导入到新后端后，数据库数据如何转换
2. 当前后端中，套餐额度、加油包、请求扣减的实际生效逻辑是什么

本文对应的是当前代码状态，不包含“历史迁移模式”展示口径。

当前系统已经统一要求：

- 不生成 `legacy_migrated`
- 不保留 `legacy-*` 计划标识
- 旧数据导入后，直接归一到当前新模式

## 相关代码位置

- 迁移入口：`src/core/schema_upgrade.py`
- 权益与订单快照：`src/core/entitlement_service.py`
- 资费目录与模式：`src/core/plan_catalog.py`
- 额度快照与扣减：`src/core/quota_service.py`
- 爱发电激活：`src/routers/user_center.py`

---

## 一、旧 SQL 导入后的迁移转化规则

### 1. 迁移前提

旧 SQL 文件导入后，系统启动时会执行 `run_schema_upgrade()`。

执行顺序：

1. 补表结构
2. 补用户字段
3. 回填 `v2_user`
4. 回填关联表
5. 回填订单快照
6. 回填当前权益表

### 2. 涉及的源表

旧数据主要来自：

- `user`
- `v2_user`
- `payment_order`
- `request_log`
- `v2_request_log`

### 3. 新结构中的目标表

迁移后，核心业务真相落到三类结构：

#### 3.1 `payment_order`

用于保存订单事实和冻结快照。

新增关键字段：

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

#### 3.2 `user_subscription_state`

用于保存用户当前生效套餐。

关键字段：

- `user_uuid`
- `plan_ref`
- `pricing_mode`
- `plan_price`
- `daily_quota`
- `rate_limit_rule`
- `status`
- `expires_at`
- `source_order_id`

#### 3.3 `user_addon_wallet`

用于保存用户当前加油包钱包。

关键字段：

- `user_uuid`
- `balance`
- `total_purchased`
- `expires_at`
- `last_order_id`

### 4. 旧订单如何转为新订单快照

#### 4.1 数据来源优先级

每条旧 `payment_order` 按以下顺序提取快照信息：

1. `custom_id`
2. `raw_payload`
3. `order.amount + action + months`

#### 4.2 action 识别

识别结果分为：

- `renew`
- `upgrade`
- `addon`

并写入：

- `order_kind=subscription`，当 action 为 `renew/upgrade`
- `order_kind=addon`，当 action 为 `addon`

#### 4.3 金额与套餐字段回填

回填规则：

- `plan_price_snapshot`：优先取 `plan_price`，否则按 `amount / months`
- `months_snapshot`：优先取 `months`，缺省为 `1`
- `expected_amount_snapshot`：优先取 `requested_amount`
- `paid_amount_snapshot`：取 `order.amount`

#### 4.4 加油包额度回填

如果旧订单是加油包：

- 优先使用 `addon_resource_amount`
- 如果旧数据没有，则按当前 addon 配置推算

当前推算规则：

- 取 `addons[0]` 作为锚点
- `resource_amount = round(amount / addon_price) * addon_resource_amount`

#### 4.5 旧订单归一到当前模式

本系统现在不保留“历史迁移模式”。

旧订单快照在迁移时会直接归一到当前资费模式：

- 当前模式为 `fixed_tiers` 时，归一到当前固定档位
- 当前模式为 `ladder` 时，归一到当前阶梯模式

### 5. 旧用户套餐如何转为当前套餐

#### 5.1 基础回填

每个用户先按当前用户表字段回填一次：

- `limit_rule`
- `expiration_date`
- `is_active`
- `addon_resource_balance`
- `addon_resource_total`
- `addon_expiration_date`

这一步会生成：

- `user_subscription_state`
- `user_addon_wallet`

#### 5.2 最新已激活订阅订单优先

如果该用户存在最新已激活订阅单，则会再按该订单快照覆盖一次当前套餐：

- `plan_price`
- `daily_quota`
- `plan_ref`
- `pricing_mode`
- `expires_at`
- `source_order_id`

这样做的目的：

- 避免只靠旧 `limit_rule` 还原套餐
- 保证“最近一次已激活订阅单”优先成为当前权益

### 6. 旧套餐如何归一到当前新模式

这是本次规则里最重要的一点。

系统不再生成任何 `legacy_*` 模式，而是直接套入当前新模式。

#### 6.1 免费用户

如果满足：

- `plan_price <= 0`
- 且 `daily_quota <= free_plan.daily_quota`

则直接归一为免费档：

- `plan_ref = free`
- `pricing_mode = free`
- `plan_price = 0`

#### 6.2 当前模式为 `fixed_tiers`

归一规则：

1. 先按 `daily_quota` 精确匹配当前 tiers
2. 匹配不到，再按 `plan_price` 精确匹配
3. 再匹配不到，按最接近的 `daily_quota`
4. 还不行，按最接近的 `plan_price`

#### 6.3 当前模式为 `ladder`

归一规则：

1. 取当前 ladder 配置：
   - `base_price`
   - `min_monthly_price`
   - `max_monthly_price`
   - `price_step`
   - `base_daily_quota`
   - `daily_quota_step`
2. 如果价格缺失但旧日额度存在，则根据旧日额度反推阶梯档位
3. 将价格 clamp 到当前梯度合法范围
4. 再按当前 `price_step` 对齐
5. 最终用 `build_tier_for_price()` 构造当前标准档位

### 7. 旧加油包钱包如何迁移

旧用户字段：

- `addon_resource_balance`
- `addon_resource_total`
- `addon_expiration_date`

迁移为：

- `user_addon_wallet.balance`
- `user_addon_wallet.total_purchased`
- `user_addon_wallet.expires_at`

生效条件：

- 余额大于 0
- 加油包到期时间未过期
- 订阅未过期

否则钱包余额会归零。

### 8. 用户表中的 `limit_rule` 现在是什么角色

迁移后：

- `user.limit_rule`
- `v2_user.limit_rule`

仍然会被同步写入，但它们现在是镜像缓存字段。

它们不是套餐真相来源。

真正的真相来源是：

- 当前套餐：`user_subscription_state`
- 当前加油包：`user_addon_wallet`
- 实际订单：`payment_order`

---

## 二、当前后端额度逻辑与规则

### 1. 当前资费模式来源

当前资费模式来自 `plan_catalog.json` 中的：

- `billing.subscription_mode`

合法值：

- `fixed_tiers`
- `ladder`

### 2. 当前套餐真相来源

运行时当前套餐以 `user_subscription_state` 为准。

关键字段：

- `plan_ref`
- `pricing_mode`
- `plan_price`
- `daily_quota`
- `rate_limit_rule`
- `expires_at`

### 3. 当前加油包真相来源

运行时加油包以 `user_addon_wallet` 为准。

关键字段：

- `balance`
- `total_purchased`
- `expires_at`

### 4. `limit_rule` 的当前意义

当前 `limit_rule` 只是同步镜像。

生成方式：

- `plan_id={plan_ref};{daily_quota}/day;{rate_limit_rule}`

示例：

- `plan_id=sub-m1000-q6000;6000/day;8/minute`

但实际业务判断不应再直接依据这个字符串。

### 5. 当前额度快照如何计算

`build_quota_snapshot()` 返回当前额度快照。

包含：

- `daily_limit`
- `today_daily_used`
- `remaining_daily`
- `addon_resource_balance`
- `addon_resource_total`
- `addon_expiration_utc`

#### 5.1 `daily_limit`

来自用户镜像字段中的日额度：

- 普通用户：从 `limit_rule` 解析 `/day`
- 默认测试用户：用 `DEFAULT_TEST_USER_IP_DAILY_QUOTA`

#### 5.2 `today_daily_used`

从请求日志统计：

- 只统计当天
- 只统计 `status=success`
- 只统计 `resource_deducted=true`
- 只统计 `quota_source in (daily, none, null)`

#### 5.3 `remaining_daily`

计算方式：

- `remaining_daily = max(0, daily_limit - today_daily_used)`

#### 5.4 `addon_resource_balance`

有效余额条件：

- `balance > 0`
- `addon_expiration` 未过期
- `subscription_expiration` 未过期

实际生效的 addon 到期时间取：

- `min(addon_expiration, subscription_expiration)`

### 6. 当前请求扣减顺序

当前新模式下，扣减顺序固定为：

1. 先扣日额度
2. 日额度不够，再扣加油包
3. 两者都不够，返回额度不足

对应逻辑：

#### 6.1 `prepare_quota_allocation()`

先判断：

- 如果 `remaining_daily >= cost`，则本次走 `daily`
- 否则如果 `addon_balance >= cost`，则本次走 `addon`
- 否则直接抛额度不足错误

#### 6.2 `commit_quota_allocation()`

最终提交时：

- 如果还能走 `daily`，只记录来源，不扣 addon 钱包
- 如果必须走 `addon`，则扣减：
  - `user.addon_resource_balance -= cost`

### 7. 当前限流与日额度的关系

当前系统里：

- `minute` 限流是限流器逻辑
- `day` 限额是额度逻辑

在 `LEGACY_CLIENT_COMPAT=False` 下：

- rate limiter 会去掉 `/day`
- 只保留分钟级规则

也就是说：

- `8/minute` 由限流器执行
- `6000/day` 由额度系统执行

### 8. 爱发电激活后的额度变化规则

#### 8.1 首购 / 续费 `renew`

规则：

- 未过期免费态也视为“首购态”
- 首购态时，续费后套餐直接变为目标套餐
- 非首购态时，普通续费延长当前套餐时长

#### 8.2 升级 `upgrade`

规则：

1. 把旧套餐剩余时长按价格比例折算到新套餐
2. 再按本次支付金额折算出新套餐可购买时长
3. 两者相加得到新到期时间
4. 当前套餐切换到新档位

#### 8.3 加油包 `addon`

规则：

- 只增加 `user_addon_wallet.balance`
- 不改当前套餐档位
- 不改当前套餐价格
- 只同步加油包到期时间到当前订阅到期时间

### 9. 默认测试用户规则

默认测试用户不是正式套餐用户。

规则：

- 日额度来自 `DEFAULT_TEST_USER_IP_DAILY_QUOTA`
- 速率来自 `DEFAULT_TEST_USER_IP_RATE_LIMIT`
- 加油包无效
- 额度按 IP 维度结算

---

## 三、当前结论

### 迁移层结论

旧 SQL 导入后：

- 不再保留历史迁移模式
- 不再生成 `legacy-*`
- 旧订单和旧用户当前权益都会被归一到当前新模式

### 运行时结论

当前系统实际口径是：

- 订单真相：`payment_order`
- 当前套餐：`user_subscription_state`
- 当前加油包：`user_addon_wallet`
- `limit_rule`：兼容镜像，不是业务真相

### 请求扣减结论

当前扣减顺序固定为：

1. 先日额度
2. 后加油包
3. 都不够则拒绝

### 展示口径结论

前端和后台的正确展示字段应当优先使用：

- `plan_ref`
- `pricing_mode`
- `plan_price`
- `daily_quota`
- `addon_resource_balance`
- `addon_resource_total`

不应再直接拿 `limit_rule` 当“当前套餐显示”。
