# 旧 SQL 导入、旧账号绑定迁移与剩余权益折算说明

## 1. 范围确认

本说明只针对你指定的旧业务数据库来源：

- [voicelinkvr_server_main.sql](D:/git/gitlab/VoiceLinkServer1/sqlback/voicelinkvr_server_main.sql)

这里的“旧数据”指的就是这个 SQL dump 导入后的：

- `user`
- `payment_order`
- `request_log`

而不是其它来源的历史库。

---

## 2. 旧业务套餐规则

根据旧 SQL 中已激活订单的 `raw_payload` 可观察到：

- `5 元 -> 1500/day`
- `10 元 -> 3000/day`
- `15 元 -> 4500/day`
- `20 元 -> 6000/day`

所以旧业务套餐步长是：

- 每 `5 元` 增加 `1500/day`

反推公式：

- `old_plan_price = (old_daily_quota / 1500) * 5`

这套规则只用于“旧账号绑定时的剩余权益折算”，不再作为新系统长期运行规则。

---

## 3. 当前新系统的数据真相

当前新系统里，业务真相分三层：

### 3.1 订单真相

- `payment_order`

### 3.2 当前套餐真相

- `user_subscription_state`

### 3.3 当前加油包真相

- `user_addon_wallet`

镜像缓存字段仍然存在：

- `user.limit_rule`
- `v2_user.limit_rule`

但它们现在只是镜像，不是唯一真相。

---

## 4. 旧 SQL 导入后的处理规则

### 4.1 导入阶段

旧 SQL 导入后，系统启动会执行 `run_schema_upgrade()`。

执行顺序：

1. 补表结构
2. 回填用户基础字段
3. 回填 `v2_user`
4. 回填关联表
5. 回填订单快照
6. 回填当前权益表

### 4.2 订单快照补齐

旧 `payment_order` 会补齐以下快照字段：

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

数据来源优先级：

1. `custom_id`
2. `raw_payload`
3. `amount / months / action`

### 4.3 导入后不再保留历史迁移模式

当前实现已经明确统一成：

- 不生成 `legacy_migrated`
- 不生成 `legacy-*`

旧数据导入后会直接归一到当前新模式。

实测测试服数据库中：

- `user_subscription_state.pricing_mode='legacy_migrated'` 数量为 `0`
- `payment_order.pricing_mode_snapshot='legacy_migrated'` 数量为 `0`
- `plan_ref like 'legacy-%'` 数量为 `0`

### 4.4 旧套餐归一到当前模式的规则

当前实现位于：

- [entitlement_service.py](D:/git/gitlab/VoiceLinkServer1/src/core/entitlement_service.py)

核心函数：

- `build_subscription_snapshot_for_existing_values()`

归一逻辑：

#### 免费用户

如果：

- `plan_price <= 0`
- 且 `daily_quota <= free_plan.daily_quota`

则直接归一为：

- `plan_ref = free`
- `pricing_mode = free`
- `plan_price = 0`

#### 当前模式为 `fixed_tiers`

按以下顺序归一：

1. 先按 `daily_quota` 精确匹配
2. 再按 `plan_price` 精确匹配
3. 仍不匹配则选最接近的档位

#### 当前模式为 `ladder`

按当前 ladder 配置归一：

- `base_price`
- `min_monthly_price`
- `max_monthly_price`
- `price_step`
- `base_daily_quota`
- `daily_quota_step`

如果旧价格缺失但旧日额度存在，会先根据旧日额度反推价格，再对齐当前 ladder。

---

## 5. 新用户绑定旧账号时的迁移规则

这部分是你当前最关心的规则，当前代码已经实现。

入口：

- [portal_api.py](D:/git/gitlab/VoiceLinkServer1/src/routers/portal_api.py)
- 接口：`/v2/api/account/legacy-link`

核心函数：

- `_merge_legacy_assets()`
- `_merge_legacy_subscription_value()`

### 5.1 绑定时做的事情

绑定旧账号时，会做以下处理：

1. 校验旧账号用户名密码
2. 建立 `UserIdentityLink`
3. 把旧订单归属迁到当前新账号
4. 把旧加油包余额迁到当前新账号
5. 把旧账号在“绑定时刻”的剩余订阅权益折算成当前系统续费价值

### 5.2 剩余权益折算规则

#### 第一步：确定旧账号剩余时间

取：

- `legacy_expiration = legacy_user.expiration_date`
- `bind_time = 当前绑定时刻`

计算：

- `remaining_seconds = max(0, legacy_expiration - bind_time)`
- `remaining_days = remaining_seconds / 86400`

#### 第二步：确定旧账号当时的旧套餐价格

当前实现优先使用旧账号最新一笔已激活订阅订单：

- 从 `payment_order.raw_payload.custom_payload.plan_price` 读取
- 如果缺失，再读 `raw_payload.limit_rule`
- 再按旧规则反推：
  - `old_plan_price = (old_daily_quota / 1500) * 5`

如果旧订单里没有足够信息，最后才回退到旧用户表上的 `limit_rule`。

#### 第三步：把旧剩余权益折算成价值

当前实现公式：

- `remaining_value = (remaining_days / AFDIAN_RENEW_DAYS) * old_plan_price`

其中：

- `AFDIAN_RENEW_DAYS = 30`

这表示：

- 旧账号在绑定时刻剩下的订阅时间，先换算成“还值多少钱”

### 5.3 绑定到已付费新账号时

如果新账号当前已经有有效付费套餐，则：

1. 读取当前新账号套餐价格 `current_plan_price`
2. 把 `remaining_value` 折算成当前套餐的续费天数

公式：

- `converted_days = (remaining_value / current_plan_price) * AFDIAN_RENEW_DAYS`

然后：

- 追加到新账号当前到期时间
- 当前套餐档位不变
- 当前日额度不变

#### 实测结果

测试场景：

- 新账号当前套餐：`10 元 / 6000/day`
- 旧账号剩余套餐：`15 元 / 4500/day`
- 旧账号剩余时间：`20 天`

折算：

- `remaining_value = 20/30 * 15 = 10`
- `converted_days = 10/10 * 30 = 30 天`

实测结果：

- 新账号到期时间从 `2026-05-18` 延长到 `2026-06-17`
- 当前套餐仍然是 `10 元 / 6000/day`

### 5.4 绑定到当前免费新账号时

如果新账号当前没有有效付费套餐，则：

1. 先把旧套餐按当前新模式归一
2. 得到当前新模式下的：
   - `normalized_plan_ref`
   - `normalized_plan_price`
   - `normalized_daily_quota`
3. 再把 `remaining_value` 折算成这个新套餐下的续费天数

公式：

- `converted_days = (remaining_value / normalized_plan_price) * AFDIAN_RENEW_DAYS`

然后：

- 直接把新账号当前套餐切换为 `normalized_plan_ref`
- 到期时间设置为 `bind_time + converted_days`

#### 实测结果

测试场景：

- 新账号当前套餐：免费
- 旧账号剩余套餐：`15 元 / 4500/day`
- 旧账号剩余时间：`20 天`
- 当前系统模式：`ladder`
- 当前 ladder 规则：`15 元 -> 9000/day`

实测结果：

- 新账号绑定后套餐变成：`15 元 / 9000/day`
- `plan_ref = sub-m1500-q9000`
- 到期时间约为绑定后 `20 天`

也就是说：

- 旧账号剩余权益已经按当前新模式转成了当前套餐

### 5.5 旧加油包余额迁移规则

绑定旧账号时，加油包处理仍然是独立的：

1. 迁移旧余额到新账号
2. 累加到 `addon_resource_balance`
3. 累加到 `addon_resource_total`
4. 到期时间取更晚的 `addon_expiration_date`
5. 最后再同步到 `user_addon_wallet`

这不会覆盖当前套餐，只会增加加油包钱包。

---

## 6. 当前后端额度规则

### 6.1 当前套餐来源

当前套餐真相：

- `user_subscription_state`

关键字段：

- `plan_ref`
- `pricing_mode`
- `plan_price`
- `daily_quota`
- `rate_limit_rule`
- `expires_at`

### 6.2 当前加油包来源

当前加油包真相：

- `user_addon_wallet`

关键字段：

- `balance`
- `total_purchased`
- `expires_at`

### 6.3 当前 `limit_rule` 的意义

当前 `limit_rule` 仍会同步生成：

- `plan_id={plan_ref};{daily_quota}/day;{rate_limit_rule}`

例如：

- `plan_id=sub-m1000-q6000;6000/day;8/minute`

但它只是镜像缓存字段，不应作为业务唯一真相。

### 6.4 当前额度快照规则

由：

- [quota_service.py](D:/git/gitlab/VoiceLinkServer1/src/core/quota_service.py)

提供。

核心函数：

- `build_quota_snapshot()`

返回：

- `daily_limit`
- `today_daily_used`
- `remaining_daily`
- `addon_resource_balance`
- `addon_resource_total`
- `addon_expiration_utc`

### 6.5 当前请求扣减顺序

当前顺序固定为：

1. 先扣日额度
2. 日额度不足，再扣加油包
3. 两者都不够，返回额度不足

对应代码：

- `prepare_quota_allocation()`
- `commit_quota_allocation()`

### 6.6 当前爱发电激活规则

入口：

- [user_center.py](D:/git/gitlab/VoiceLinkServer1/src/routers/user_center.py)

规则：

#### `renew`

- 免费态用户也视为首购态
- 首购时直接切到目标套餐
- 已有付费套餐时按续费延长当前套餐

#### `upgrade`

- 先把旧套餐剩余时间按价格比例折算
- 再加上本次支付在新套餐下可购买的时长

#### `addon`

- 只增加加油包余额
- 不改当前套餐档位

---

## 7. 当前结论

### 7.1 你要求的迁移方案是否正确

结论：

- **正确**

尤其是这条要求：

- 旧账号绑定时，把绑定时刻剩余权益折算成当前套餐续费价值

这是当前已经实现并实测通过的规则。

### 7.2 当前实现是否已经满足

结论：

- **已满足**

具体体现在：

1. 旧 SQL 导入后不会再保留历史迁移模式
2. 绑定旧账号时，不再直接复制旧套餐字段
3. 旧剩余权益会先换算成价值，再折算成当前套餐续费
4. 当前付费账号与当前免费账号两条绑定路径都已实测通过

### 7.3 当前系统的正确展示口径

前端和后台应优先展示：

- `plan_ref`
- `pricing_mode`
- `plan_price`
- `daily_quota`
- `addon_resource_balance`
- `addon_resource_total`

不应把：

- `limit_rule`

直接当作“当前套餐展示口径”给用户看。
