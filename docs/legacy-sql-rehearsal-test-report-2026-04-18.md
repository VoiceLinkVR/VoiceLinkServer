# 旧 SQL 迁移 rehearsal 测试报告

## 测试目标

验证以下完整流程在当前新后端中是否可用：

1. 旧业务数据导入
2. 新后端启动并自动迁移旧数据
3. 新用户注册
4. 新用户直接绑定旧账号
5. 新用户先发电，再绑定旧账号

本次演练基于真实旧业务数据库来源：

- [voicelinkvr_server_main.sql](D:/git/gitlab/VoiceLinkServer1/sqlback/voicelinkvr_server_main.sql)

但由于原始 SQL 文件接近 6GB，本次 rehearsal 按你确认后的要求，采用：

- **抽样真实业务数据导入**

而不是全量导入。

---

## 测试环境

### 服务器

- `root@192.168.2.53`

### 数据库

- MySQL 容器：`vrcls-preview-mysql`
- rehearsal 库名：`voicelinkvr_server_main_rehearsal`

### 后端服务

- 容器名：`vrcls-rehearsal-backend`
- 对外端口：`28980`

### 资费目录

rehearsal 服务使用和当前预览环境一致的资费目录：

- `subscription_mode = ladder`
- `5 元 = 3000/day`
- 每 `5 元` 增 `3000/day`

---

## 样本数据说明

本次从旧 SQL 中抽取了两个真实旧付费用户及其已激活订单：

### 旧账号样本 A

- 用户名：`xuefeihua`
- 旧套餐：`4500/day`
- 旧到期时间：`2026-05-12 02:04:32`
- 对应已激活订单：`payment_order.id = 130`
- 导入后归一到当前新模式：
  - `sub-m1500-q9000`
  - `15 元 / 9000/day`

### 旧账号样本 B

- 用户名：`qck`
- 旧套餐：`3000/day`
- 旧到期时间：`2026-05-15 13:51:29`
- 对应已激活订单：`payment_order.id = 126`
- 导入后归一到当前新模式：
  - `sub-m1000-q6000`
  - `10 元 / 6000/day`

---

## 过程记录

## 步骤 1：导入旧样本数据

导入内容：

1. 旧 `user` 表结构
2. 旧 `payment_order` 表结构
3. 两个真实旧付费用户记录
4. 两条真实已激活订单记录

### 导入过程中的兼容修正

旧 SQL 来自 MariaDB，直接导入 MySQL 8 会遇到兼容问题：

- `UNIQUE INDEX custom_id(custom_id) USING HASH`

该写法在 MySQL 8 下会报错，因此测试导入时改成：

- `UNIQUE INDEX custom_id(custom_id(255)) USING BTREE`

### 导入结论

- 旧样本数据成功导入 `voicelinkvr_server_main_rehearsal`

---

## 步骤 2：启动新后端并自动迁移

启动容器：

- `vrcls-rehearsal-backend`

### 首次启动时发现的问题

真实旧库在启动迁移时暴露了一个问题：

- `schema_upgrade.py` 在旧表补列前，就先用 ORM 查询 `user` 表
- 导致旧表缺少 `user_uuid` 等新列时启动失败

### 已修复

本次已修复为：

- 先执行 `_ensure_table_columns()`
- 再做 `db.query(User).count()`

同时进一步把旧表补列逻辑改成：

- 直接使用 `SHOW COLUMNS`

以适配真实旧表结构。

### 启动迁移结果

rehearsal 服务启动成功，并完成：

1. 旧 `user` 表补列
2. 旧 `payment_order` 表补列
3. 新表创建：
   - `v2_user`
   - `user_subscription_state`
   - `user_addon_wallet`
   - 其它 v2/portal 相关表
4. 旧样本数据归一到当前新模式

### 迁移后样本状态

旧账号 `qck`：

- `plan_ref = sub-m1000-q6000`
- `pricing_mode = ladder`
- `plan_price = 10`
- `daily_quota = 6000`

旧账号 `xuefeihua`：

- `plan_ref = sub-m1500-q9000`
- `pricing_mode = ladder`
- `plan_price = 15`
- `daily_quota = 9000`

---

## 步骤 3：流程 A，新用户注册后直接绑定旧账号

### 3.1 新用户注册

新用户：

- 邮箱：`flow1_bind_free_20260418@example.com`

流程：

1. 调用 `/v2/auth/register/send-code`
2. 从 rehearsal 库中读取注册验证码
3. 调用 `/v2/auth/register`

结果：

- 注册成功
- 获得正式 `access_token`
- 新账号初始状态：
  - `plan_price = 0`
  - `daily_limit = 800`
  - `plan_ref = free`

### 3.2 绑定旧账号

绑定目标旧账号：

- `xuefeihua`

调用：

- `/v2/api/account/legacy-link`

绑定前：

- `expiration = null`
- `plan_price = 0`
- `daily_limit = 800`
- `plan_ref = free`

绑定后：

- `expiration = 2026-05-12T12:57:54Z`
- `plan_price = 15.0`
- `daily_limit = 9000`
- `plan_ref = sub-m1500-q9000`

### 3.3 结论

流程 A 通过。

说明：

- 免费新账号绑定旧付费账号时
- 旧剩余权益已被转换成当前新模式下的套餐
- 并正确写入当前订阅状态

---

## 步骤 4：流程 B，新用户先自己发电，再绑定旧账号

### 4.1 新用户注册

新用户：

- 邮箱：`flow2_bind_paid_20260418@example.com`

流程同上：

1. 调用 `/v2/auth/register/send-code`
2. 从 rehearsal 库读取验证码
3. 调用 `/v2/auth/register`

注册结果：

- 注册成功
- 初始仍是免费状态：
  - `plan_price = 0`
  - `daily_limit = 800`

### 4.2 新用户先发电

流程：

1. 调用 `/v2/api/billing/guide`
2. 创建 `10 元` 续费订单
3. 模拟爱发电 webhook 回调

结果：

发电前：

- `expiration = null`
- `plan_price = 0`
- `daily_limit = 800`

发电后：

- `expiration = 2026-05-19T13:00:10Z`
- `plan_price = 10.0`
- `daily_limit = 6000`
- `plan_ref = sub-m1000-q6000`

### 4.3 再绑定旧账号

绑定目标旧账号：

- `qck`

绑定前：

- `expiration = 2026-05-19T13:00:10Z`
- `plan_price = 10.0`
- `daily_limit = 6000`
- `plan_ref = sub-m1000-q6000`

绑定后：

- `expiration = 2026-06-16T13:00:10Z`
- `plan_price = 10.0`
- `daily_limit = 6000`
- `plan_ref = sub-m1000-q6000`

### 4.4 结论

流程 B 通过。

说明：

- 已付费新账号绑定旧付费账号时
- 旧剩余权益没有覆盖当前套餐
- 而是折算为当前 `10 元档` 的续费时间并追加

---

## 步骤 5：库内最终状态确认

### 5.1 新账号与旧账号绑定关系

`v2_user`：

- `flow1_bind_free_20260418 -> legacy_user_id = 766`
- `flow2_bind_paid_20260418 -> legacy_user_id = 714`

`user_identity_link`：

- `xuefeihua -> flow1_bind_free_20260418`
- `qck -> flow2_bind_paid_20260418`

### 5.2 当前套餐状态

`user_subscription_state`：

- `flow1_bind_free_20260418`
  - `sub-m1500-q9000`
  - `ladder`
  - `15`
  - `9000/day`
- `flow2_bind_paid_20260418`
  - `sub-m1000-q6000`
  - `ladder`
  - `10`
  - `6000/day`

### 5.3 旧订单归属

旧订单 `126`：

- 已归属到 `flow2_bind_paid_20260418`

旧订单 `130`：

- 已归属到 `flow1_bind_free_20260418`

---

## 最终结论

本次 rehearsal 结论如下：

### 1. 旧样本导入

- 成功

### 2. 新后端启动并自动迁移旧数据

- 成功

### 3. 新用户注册

- 成功

### 4. 新用户直接绑定旧账号

- 成功

### 5. 新用户先自己发电再绑定旧账号

- 成功

### 6. 剩余权益折算逻辑

- 成功生效

其中：

- 免费新号绑定旧付费号：旧剩余权益转成当前新模式套餐
- 已付费新号绑定旧付费号：旧剩余权益转成当前套餐续费时间

这和你要求的业务口径一致。

---

## 补充说明

### 1. 本次不是全量 6GB 导入

本次按你的允许，采用的是：

- 真实旧样本数据抽样导入

不是全量导入。

### 2. 本次演练过程中修复了一个真实兼容问题

修复内容：

- 旧表补列必须早于 ORM 查询

否则真实旧 SQL 导入后的服务无法启动。

### 3. 当前报告可作为后续全量导入的流程基线

后续若做全量导入，建议沿用本次顺序：

1. 导入旧库
2. 启动新后端触发迁移
3. 注册新账号
4. 绑定旧账号
5. 校验当前套餐与到期时间
