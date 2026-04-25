# 53 测试环境迁移验证与上线评估报告

## 1. 文档目的

本文档用于记录以下内容：

- 本轮迁移与适配修改了什么
- 我在 `192.168.2.53` 上实际执行了哪些测试
- 每个测试的结果是什么
- 当前网站与后端是否具备上线条件
- 还有哪些风险点没有被完全排除

本文档覆盖的代码仓库：

- 后端：`D:\git\gitlab\VoiceLinkServer1`
- 官网与管理后台：`D:\git\gitlab\vrclswebsite`

验证时间：

- 2026-04-19 至 2026-04-20

验证环境：

- 测试服务器：`ssh root@192.168.2.53`
- 数据库：`voicelinkvr_server_main`
- 当前容器：
  - `vrcls-prod-backend`
  - `vrcls-prod-frontend`
  - `vrcls-prod-mariadb`
  - `vrcls-prod-redis`

---

## 2. 结论摘要

当前 53 环境已经完成了新后端 + 新官网 + 新管理后台的主要链路验证，以下核心能力已经通过：

- 新用户注册、登录、登录态保持
- 用户中心受保护接口访问
- 旧账号绑定迁移
- 旧账号绑定后禁用旧 `user` 账户
- 旧订单克隆进入 `v2_payment_order`
- 管理后台登录与主面板加载
- 状态页正确读取 JSON 数据
- 新站点 v2 审计数据写入新表，不再写旧 `request_log`

当前判断：

- 适合继续进行灰度上线/受控上线
- 不适合宣称“所有风险和漏洞已经完全排除”

原因：

- 核心站点链路已可用
- 真实爱发电回调与真实支付激活，本轮没有做线上实单复验
- 管理后台统计页目前已修复为可用，但在“没有 v2 聚合统计数据”的情况下会返回空统计，不再强行扫旧 3200 多万行历史日志

---

## 3. 本轮关键修改

### 3.1 迁移架构改造

后端已按“旧库保留、v2 新表承接新业务”的方向完成调整：

- 新业务表统一使用 `v2_` 前缀，例如：
  - `v2_user`
  - `v2_payment_order`
  - `v2_request_audit_log`
  - `v2_user_identity_link`
  - `v2_user_subscription_state`
  - `v2_user_addon_wallet`
  - `v2_announcement_entry`
  - `v2_faq_entry`
  - `v2_site_config`
- 旧表 `user`、`payment_order`、`request_log` 保留历史数据，不做结构迁移作为新业务主表
- 新后端启动时，活动迁移路径只维护 `v2_*` 体系，不再把旧表当作主业务表升级

### 3.2 旧账号绑定迁移逻辑

已实现并验证：

- 新注册用户绑定旧账号后：
  - 在 `v2_user` 上写入 `legacy_user_id`
  - 在 `v2_user_identity_link` 写入绑定记录
  - 旧 `user.is_active` 直接置为 `0`
- 旧订单不会删改旧表记录
- 旧订单会克隆到 `v2_payment_order`
- 克隆时会补齐当前新计费体系所需快照字段，例如：
  - `plan_price_snapshot`
  - `months_snapshot`
  - `expected_amount_snapshot`
  - `plan_ref`
  - `pricing_mode_snapshot`
  - `billing_version`

### 3.3 旧登录链路修正

已实现并验证：

- 旧 v1 登录判断旧账号是否可登录时，以 `is_active` 为准
- 不再因为“过期回落”去修改旧 `user.limit_rule`、旧额度字段
- 旧 `manage_api` 已被关闭，不再继续作为活动管理入口

### 3.4 本轮新增修复

本轮为解决“站点可用性和验收”新增修复如下：

- 修复测试环境官网登录态丢失
  - 原因：53 当前使用 `http://192.168.2.53`，后端此前下发了带 `Secure` 的 session cookie，浏览器直接丢弃
  - 处理：
    - 53 环境将 `SESSION_COOKIE_HTTPS_ONLY=False`
    - `PORTAL_PUBLIC_BASE_URL` 改为 `http://192.168.2.53/app`
    - 后端部署模板 `compose.yaml` 补齐 `SESSION_COOKIE_*` 透传
- 修复管理后台统计接口
  - 问题：`/v2/adminapi/stats` 在无 v2 聚合统计数据时回退扫描旧 `request_log`，但旧表没有 `status/resource_cost` 字段，导致异常或超时
  - 处理：
    - 后端 `src/core/admin_service.py` 增加旧日志字段缺失时的兼容处理
    - 统计选择逻辑优先使用 v2 新体系，不再回退重扫历史大表
- 修复管理后台首页 5 个接口并发加载导致的页面超时
  - 问题：管理后台首屏并发拉 `summary/users/orders/stats/configs`，在当前测试环境会触发超时
  - 处理：
    - 前端 `admin/src/App.vue` 将 `refreshAll()` 从 `Promise.all` 改为串行加载
- 修复状态页读取到 HTML 而不是 JSON
  - 问题：53 正在使用的 `/opt/vrcls-prod-frontend/nginx.conf` 缺少：
    - `location = /status-probe/api/status/public`
    - `location = /status-probe/api/status/refresh`
  - 处理：
    - 用仓库内 `deploy/prod-nginx.conf` 覆盖 53 生效配置
    - 重启 `vrcls-prod-frontend`

### 3.5 本轮涉及的关键文件

后端：

- `D:\git\gitlab\VoiceLinkServer1\src\core\admin_service.py`
- `D:\git\gitlab\VoiceLinkServer1\src\core\dependencies.py`
- `D:\git\gitlab\VoiceLinkServer1\src\core\v2_dependencies.py`
- `D:\git\gitlab\VoiceLinkServer1\src\core\schema_upgrade.py`
- `D:\git\gitlab\VoiceLinkServer1\src\db\models.py`
- `D:\git\gitlab\VoiceLinkServer1\src\main.py`
- `D:\git\gitlab\VoiceLinkServer1\compose.yaml`

官网与管理后台：

- `D:\git\gitlab\vrclswebsite\admin\src\App.vue`
- `D:\git\gitlab\vrclswebsite\deploy\prod-nginx.conf`

---

## 4. 数据库现状快照

2026-04-20 抽样查询结果：

| 表名 | 行数 |
| --- | ---: |
| `user` | 736 |
| `payment_order` | 148 |
| `request_log` | 32714892 |
| `v2_user` | 6 |
| `v2_payment_order` | 1 |
| `v2_request_log` | 6 |
| `v2_request_audit_log` | 147 |
| `v2_user_identity_link` | 2 |
| `v2_announcement_entry` | 0 |
| `v2_faq_entry` | 0 |

说明：

- 旧表仍然保留大体量历史数据
- 新体系数据已进入 `v2_*` 表
- `v2_request_log` 目前只保留兼容意义
- 新版站点和后台请求实际审计落在 `v2_request_audit_log`

---

## 5. 测试案例与结果

### 5.1 容器与基础服务

| 编号 | 测试内容 | 结果 | 证据摘要 |
| --- | --- | --- | --- |
| T01 | 检查 53 容器运行状态 | 通过 | `vrcls-prod-backend`、`vrcls-prod-frontend`、`vrcls-prod-mariadb`、`vrcls-prod-redis` 均为 `Up` |
| T02 | 检查后端启动迁移日志 | 通过 | 启动日志显示 `Schema upgrade completed. v2_users=6 v2_orders=1`，未出现旧表字段追加日志 |

### 5.2 官网与用户中心

| 编号 | 测试内容 | 结果 | 证据摘要 |
| --- | --- | --- | --- |
| T03 | 官网首页 `http://192.168.2.53/` 打开 | 通过 | 浏览器快照正常展示首页，无 console error |
| T04 | 状态页 `http://192.168.2.53/status/` 打开 | 通过 | 修复 nginx 后页面显示“VoiceLinkVR 公开服务状态”，不再报 `Unexpected token '<'` |
| T05 | 新用户发送注册验证码 | 通过 | `POST /v2/auth/register/send-code` 返回 `200`，创建测试邮箱 `login-fix-1776616987@example.com` |
| T06 | 新用户注册后立即获取登录态 | 通过 | `POST /v2/auth/register` 返回 `200`，随后 `GET /v2/api/account/profile` 返回 `200` |
| T07 | 已注册用户网页登录 | 通过 | 浏览器真实登录 `portal-order-bind-case@example.com` 后进入 `/app/overview` |
| T08 | 登录后受保护接口访问 | 通过 | `/v2/api/account/profile`、`/entitlements`、`/billing/guide`、`/orders`、`/usage` 均返回 `200` |
| T09 | 新注册用户是否误写入旧 `user` 表 | 通过 | `v2_user` 中存在 `login-fix-1776616987@example.com`，旧 `user` 表中计数为 `0` |

### 5.3 旧账号绑定迁移

| 编号 | 测试内容 | 结果 | 证据摘要 |
| --- | --- | --- | --- |
| T10 | 旧账号绑定记录是否写入新体系 | 通过 | `v2_user_identity_link` 中存在 `legacy_expired_test` 与 `legacy_order_bind_case` 两条绑定记录 |
| T11 | 绑定后旧账号是否禁用 | 通过 | 旧 `user` 表中 `legacy_expired_test` 与 `legacy_order_bind_case` 的 `is_active=0` |
| T12 | 绑定后新用户是否记录 `legacy_user_id` | 通过 | `v2_user.id=4` 的 `portal_order_bind_case` 对应 `legacy_user_id=783` |
| T13 | 旧订单是否克隆到 `v2_payment_order` | 通过 | `legacy-order-bind-case-001` 同时存在于旧 `payment_order` 与新 `v2_payment_order` |
| T14 | 克隆订单是否补齐新快照字段 | 通过 | `plan_price_snapshot=5`、`months_snapshot=1`、`expected_amount_snapshot=5`、`plan_ref=sub-m500-q3000`、`pricing_mode_snapshot=ladder`、`billing_version=3` |
| T15 | 旧订单原记录是否保留 | 通过 | 旧 `payment_order.id=148` 仍保留，未被覆盖或删除 |

### 5.4 管理后台

| 编号 | 测试内容 | 结果 | 证据摘要 |
| --- | --- | --- | --- |
| T16 | 管理后台登录接口 | 通过 | `POST /v2/adminapi/auth/login` 返回 `200` |
| T17 | 管理后台初始并发加载问题复现 | 失败已定位 | 登录后并发请求曾出现 `504`，根因是 `/stats` 卡住 + 前端首屏并发拉取 |
| T18 | 修复后管理后台浏览器首屏加载 | 通过 | 新前端资源 `index-LRMX-cDf.js` 上线后，浏览器里 `dashboard/summary`、`users`、`orders`、`stats`、`configs` 全部 `200` |
| T19 | 管理后台页面内容渲染 | 通过 | 浏览器快照显示 `用户总数=6`、`活跃用户=6`、`订单总数=1`、`已激活金额=￥5` |
| T20 | 管理后台统计接口单点复测 | 通过 | `GET /v2/adminapi/stats` 约 `51ms` 返回 `200`，当前为空统计而不是超时 |

### 5.5 新日志与旧日志口径

| 编号 | 测试内容 | 结果 | 证据摘要 |
| --- | --- | --- | --- |
| T21 | v2 新站点请求是否落到新审计表 | 通过 | `v2_request_audit_log` 当前行数 `147`，最新记录包含 `/v2/adminapi/*` 200 响应 |
| T22 | 旧 `request_log` 是否仍作为新站点主日志 | 不再作为主日志 | `v2_request_log` 当前仅 6 条，且均为旧兼容/v1 登录记录；新官网/后台请求不再写旧日志体系 |

---

## 6. 关键数据库证据

### 6.1 绑定迁移证据

旧账号禁用：

- `legacy_expired_test`：`id=782`，`is_active=0`
- `legacy_order_bind_case`：`id=783`，`is_active=0`

绑定映射：

- `v2_user_identity_link`
  - `legacy_expired_test -> user_uuid=c2b9afcf-dfd7-4f33-bb47-ebd23f4c3ed6`
  - `legacy_order_bind_case -> user_uuid=83fd6f7a-bf09-4651-9e69-3f9b3bc6db6a`

### 6.2 订单克隆证据

新表克隆记录：

- `v2_payment_order.id=2`
- `order_no=legacy-order-bind-case-001`
- `username=portal_order_bind_case`
- `status=activated`
- `amount=5`
- `action=renew`
- `plan_price_snapshot=5`
- `months_snapshot=1`
- `expected_amount_snapshot=5`
- `plan_ref=sub-m500-q3000`
- `pricing_mode_snapshot=ladder`
- `billing_version=3`

旧表原记录仍在：

- `payment_order.id=148`
- `out_trade_no=legacy-order-bind-case-001`
- `username=legacy_order_bind_case`
- `status=activated`

### 6.3 新用户只写新表证据

测试新用户：

- 邮箱：`login-fix-1776616987@example.com`

查询结果：

- `v2_user` 中数量：`1`
- 旧 `user` 表中对应用户名/邮箱数量：`0`

---

## 7. 当前上线判断

### 7.1 已具备的上线条件

以下站点级核心能力已通过：

- 官网首页
- 状态页
- 用户注册
- 用户登录
- 登录态保持
- 用户中心加载
- 旧账号绑定迁移
- 管理后台登录
- 管理后台主面板加载

如果你当前目标是：

- 上线新官网
- 上线新用户中心
- 上线新管理后台
- 启用旧账号绑定迁移

那么当前 53 环境已经基本具备上线前提。

### 7.2 不能宣称“完全排除”的风险

以下风险尚未被完全排除：

1. 真实爱发电支付链路本轮没有做线上实单复验。
2. 当前 `stats` 页在“没有 v2 聚合统计数据”时会返回空统计，不会去扫旧 3200 多万行历史日志；这是为了避免后台卡死。
3. 当前测试环境为 HTTP，登录态修复采用了 `SESSION_COOKIE_HTTPS_ONLY=False`。正式 HTTPS 上线时必须切回 `True`。
4. 登录页与后台登录页仍有浏览器可访问性提示，例如缺少更完整的 `autocomplete`/表单标识；这不影响功能，但属于前端质量尾项。
5. 本轮没有做“真实公网域名 + 真实 HTTPS 证书 + 正式 nginx 反代”全链路最终验证。

### 7.3 我的判断

建议结论：

- 可以进入正式上线准备阶段
- 可以做灰度或受控上线
- 不建议在未补齐真实支付回调验证和正式 HTTPS 验证前，直接下结论说“已经百分之百没有风险”

---

## 8. 建议的上线前最后清单

建议在正式切域名前，再补以下 5 项：

1. 用正式域名与正式 HTTPS 证书跑一遍注册、登录、管理后台登录。
2. 将后端环境变量切回生产值：
   - `PORTAL_PUBLIC_BASE_URL=https://你的域名/app`
   - `SESSION_COOKIE_HTTPS_ONLY=True`
3. 用真实爱发电订单做一次完整下单、回调、激活验证。
4. 再次确认反代后的 `/status-probe/api/status/public` 返回头是 `Content-Type: application/json`。
5. 如果你希望后台统计继续显示旧历史流量，需要单独决定是否做“旧日志离线聚合导入 v2 统计表”。

---

## 9. 本轮最终结论

这轮验证后，53 测试环境的核心问题已经从“登录失效、后台超时、状态页错误返回 HTML”修复到“官网、控制台、后台、状态页都可正常打开并完成关键链路”。

当前最重要的事实是：

- 新业务数据已经进入 `v2_*` 表
- 旧数据仍保留在旧表
- 旧账号绑定后会被禁用
- 旧订单会迁移为新体系可识别的快照订单
- 站点级主要页面可用

当前状态可以进入正式上线前最后一轮域名/HTTPS/支付验证。
