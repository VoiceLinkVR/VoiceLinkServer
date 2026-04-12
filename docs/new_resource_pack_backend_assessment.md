# 新版本加油包方案后端改造评估

## 结论

“订阅日额度 + 加油包总量”这个方案可以接入当前后端，但不能只靠现有 `limit_rule` 直接实现，需要补一层新的额度扣减逻辑。

当前代码已经具备以下基础：

- `LEGACY_CLIENT_COMPAT=true` 时，资源成本统一按 `1` 兼容
- `LEGACY_CLIENT_COMPAT=false` 时，`src/routers/api.py` 已经会按接口类型设置真实 `resource_cost`
- `request_log` 已经记录：
  - `status`
  - `resource_cost`
  - `resource_deducted`
- 爱发电支付链路和订单激活链路已经成型，可扩展新动作类型

因此，这次改造不需要重做支付系统，但需要把“总量补充额度”的存储和扣减链路补齐。

## 现状限制

### 1. 当前用户限额模型只有单一日额度

当前限额核心仍是 `User.limit_rule`，例如：

- `1500/day`
- `3000/day;8/minute`

这类规则适合表达“每天最多多少”，不适合表达：

- 先扣日额度
- 超出后再扣本周期加油包余额

### 2. 当前 `limits` 限流器不适合做“主额度 + 备用额度”回退

`src/core/rate_limiter.py` 当前做法是：

- 请求进入时先检查用户限额
- 通过后挂起一个待提交的 rate limit context
- 响应成功后再按 `resource_cost` 提交扣减

这个模型只能表达“一个桶里扣多少”，不能表达：

- 同一次请求先尝试扣 A 桶
- A 不够时改扣 B 桶
- 同时还要保留分钟级限流

所以如果要上加油包，不能继续只依赖现有 `MovingWindowRateLimiter` 处理总量。

### 3. 当前日志无法区分资源是从日额度还是加油包扣掉的

`request_log` 目前只有 `resource_cost` 和 `resource_deducted`，没有记录：

- 这次成功请求是扣了“订阅日额度”
- 还是扣了“加油包”

如果不补这个信息，用户中心和客服解释都会比较困难。

## 推荐改造方案

推荐采用“两层额度、两套职责”：

- 分钟级/小时级防刷限流：继续使用现有 `limits`
- 日额度与加油包余额：改为数据库驱动的业务额度扣减

换句话说：

- `rate_limiter` 继续负责“防爆”
- 新增 `quota_service` 负责“计费额度”

## 目标规则

仅在 `LEGACY_CLIENT_COMPAT=false` 时启用：

- 订阅套餐：`5 元 = 3000 资源 / 日`
- 加油包：`5 元 = 5000 资源`
- 仅有效订阅用户可购买加油包
- 请求扣减顺序：
  1. 先扣当天订阅日额度
  2. 日额度不足时，再扣加油包
  3. 两者都不足时，拒绝请求

## 推荐数据结构

### 方案建议

不建议把加油包继续塞进 `limit_rule` 字符串。

建议在 `user` 表新增以下字段：

- `addon_resource_balance`：当前周期剩余加油包资源
- `addon_resource_total`：当前周期累计购买的加油包资源，可选
- `addon_expiration_date`：加油包失效时间，建议与订阅周期一致

建议在 `request_log` 表新增：

- `quota_source`：`daily` / `addon` / `none`

如果你希望后续财务和客服可追溯，建议再加一张额度流水表，例如：

- `quota_ledger`
  - `username`
  - `change_type`：`subscription_reset` / `addon_purchase` / `request_deduct`
  - `amount`
  - `source`
  - `related_order_id`
  - `created_at`

如果想先最小改造，也可以先不加流水表，只加用户余额字段和日志来源字段。

## 推荐代码改造点

### 1. 数据模型

文件：

- `src/db/models.py`
- `src/main.py`

需要新增：

- `User.addon_resource_balance`
- `User.addon_resource_total`
- `User.addon_expiration_date`
- `RequestLog.quota_source`

并在 `check_and_update_db()` 中补迁移逻辑。

### 2. 新增额度服务

建议新增文件：

- `src/core/quota_service.py`

建议职责：

- 解析用户当前是否处于新版本计费模式
- 计算当天已使用订阅额度
- 判断本次请求是否应从日额度或加油包扣减
- 在成功响应后原子扣减

建议接口形态：

- `check_quota_before_request(...)`
- `commit_quota_after_success(...)`

### 3. 用户请求入口

文件：

- `src/core/rate_limiter.py`
- `src/main.py`
- `src/routers/api.py`

建议做法：

- 继续保留分钟级或小时级限流
- 不再把“日总量”完全交给 `limits` 做最终裁决
- 在新模式下，业务总量判断改走 `quota_service`

否则一旦日额度耗尽，现有 `limits` 会直接拒绝请求，根本没有机会切到加油包余额。

### 4. 支付与激活链路

文件：

- `src/routers/user_center.py`

当前已支持动作：

- `renew`
- `upgrade`

建议新增：

- `addon`

需要改造的点：

- `ACTION_ALIASES` 增加 `addon`
- `payment-guide` 在 `LEGACY_CLIENT_COMPAT=false` 时返回加油包购买入口
- `payment-link` 支持生成 `addon` 订单
- 激活逻辑里新增 `addon` 分支：
  - 校验用户已有有效订阅
  - 按 `5 元 = 5000 资源` 累加到 `addon_resource_balance`
  - `addon_expiration_date` 对齐当前订阅到期时间

### 5. 用户中心展示

文件：

- `src/routers/api.py`
- `src/templates/*`

建议新增展示：

- 当前日额度
- 今日已用资源
- 今日剩余订阅额度
- 加油包剩余资源
- 加油包失效时间

并在 `/api/account/usage` 中增加：

- `addon_resource_balance`
- `addon_expiration_utc`
- `quota_source` 汇总信息

## 启用策略

建议采用以下开关策略：

- `LEGACY_CLIENT_COMPAT=true`
  - 保持旧兼容逻辑
  - 资源成本统一按 1 计
  - 不开放加油包购买

- `LEGACY_CLIENT_COMPAT=false`
  - 启用真实 `resource_cost`
  - 启用订阅日额度 + 加油包总量
  - 启用用户中心相关展示

注意：你提到的 “lagecy” 实际环境变量名是 `LEGACY_CLIENT_COMPAT`。

## 推荐实施顺序

### 第一阶段：数据与展示准备

- 增加数据库字段
- 增加 `quota_source`
- 用户中心先展示加油包字段，但前端入口可先隐藏

### 第二阶段：支付链路接通

- `user_center` 增加 `addon` 购买动作
- 激活后能正确入账 `addon_resource_balance`

### 第三阶段：额度扣减切换

- 新版本下启用 `quota_service`
- 请求成功后按“日额度优先、加油包兜底”扣减
- 旧版本继续兼容模式

## 风险点

### 1. 仅靠现有 rate limiter 直接实现会导致逻辑冲突

这是本次改造最大的技术风险。若不拆出业务额度服务，加油包在日额度用完后无法接管请求。

### 2. 并发扣减需要原子性

如果用户同一时间有多个请求，数据库扣减必须串行或带条件更新，否则会出现加油包余额被超扣。

### 3. 周期对齐要一次性定死

建议明确：

- 加油包失效时间跟随当前订阅到期时间
- 订阅过期后加油包失效
- 续费是否恢复旧加油包：建议不恢复，直接作废，规则最简单

## 最小可行版本建议

如果希望先做一版风险最低的实现，建议：

1. 仅在 `LEGACY_CLIENT_COMPAT=false` 时开放加油包
2. 不引入独立月套餐，只做“订阅 + 加油包”
3. 先加用户余额字段和 `quota_source`
4. 先不做额度流水表
5. 先保证扣减顺序正确，再补运营统计

这样改动面最小，也最符合你现在的产品方向。
