# 旧 SQL 绑定迁移实施结果汇总

## 本次实施目标

围绕旧业务数据库：

- [voicelinkvr_server_main.sql](D:/git/gitlab/VoiceLinkServer1/sqlback/voicelinkvr_server_main.sql)

完成以下改造：

1. 旧数据导入后不再保留历史迁移模式
2. 新用户绑定旧账号时，旧剩余权益按绑定时刻折算
3. 如果新账号已付费，则折算为当前套餐续费
4. 如果新账号免费，则归一到当前新模式并生成当前套餐
5. 官网与管理后台不再把 `limit_rule` 当成当前状态展示
6. 客户端补齐当前套餐/加油包状态字段消费

---

## 实际改动

### 后端

核心文件：

- [portal_api.py](D:/git/gitlab/VoiceLinkServer1/src/routers/portal_api.py)
- [entitlement_service.py](D:/git/gitlab/VoiceLinkServer1/src/core/entitlement_service.py)
- [schema_upgrade.py](D:/git/gitlab/VoiceLinkServer1/src/core/schema_upgrade.py)
- [admin_service.py](D:/git/gitlab/VoiceLinkServer1/src/core/admin_service.py)

关键改动：

1. 旧账号绑定合并逻辑改成“剩余价值折算”
2. 不再直接复制旧 `expiration_date + limit_rule`
3. 旧 SQL 导入后统一归一到当前新模式
4. 清除了 `legacy_migrated / legacy-*`
5. 后台接口改为返回新的套餐字段：
   - `plan_ref`
   - `pricing_mode`
   - `plan_price`
   - `daily_quota`
   - `current_quota_display`

### 官网前端

核心文件：

- [user.ts](D:/git/gitlab/vrclswebsite/portal/src/stores/user.ts)
- [Overview.vue](D:/git/gitlab/vrclswebsite/portal/src/views/dashboard/Overview.vue)
- [Usage.vue](D:/git/gitlab/vrclswebsite/portal/src/views/dashboard/Usage.vue)
- [DashboardLayout.vue](D:/git/gitlab/vrclswebsite/portal/src/layouts/DashboardLayout.vue)
- [App.vue](D:/git/gitlab/vrclswebsite/admin/src/App.vue)

关键改动：

1. 用户前端当前状态只看真实权益字段
2. 不再把 `billingGuide` 当作当前状态回退来源
3. 不再把 `limit_rule` 直接展示为“当前状态”
4. 管理后台用户列表改为显示“当前额度”
5. 管理后台用户编辑表单不再隐藏提交 `limit_rule`

### 客户端

核心文件：

- [AccountStatusInfo.cs](D:/git/gitlab/VRCLS-CSharp/01.VRCLS.Core/Models/AccountStatusInfo.cs)
- [BackendAuthService.cs](D:/git/gitlab/VRCLS-CSharp/02.VRCLS.Services/Auth/BackendAuthService.cs)
- [SettingsViewModel.cs](D:/git/gitlab/VRCLS-CSharp/03.VRCLS.UI/ViewModels/SettingsViewModel.cs)
- [UserCenterTab.xaml](D:/git/gitlab/VRCLS-CSharp/03.VRCLS.UI/Views/SettingsTabs/UserCenterTab.xaml)

关键改动：

1. 补齐套餐标识、定价模式、加油包余额字段
2. 设置页可显示加油包余额
3. 账户概览说明文本改为包含当前套餐与加油包余额

---

## 测试服部署

测试服务器：

- `root@192.168.2.53`

部署位置：

- 后端：`/opt/vrcls-preview-backend`
- 官网：`/root/vrcls-preview/frontend/site`

容器：

- `vrcls-preview-backend`
- `vrcls-preview-frontend`

---

## 验证结果

### 1. 旧模式清理

已在测试服数据库确认：

- `user_subscription_state` 中 `legacy_migrated = 0`
- `payment_order` 中 `legacy_migrated = 0`
- `plan_ref like 'legacy-%' = 0`

### 2. 旧付费账号并入当前已付费账号

测试场景：

- 新账号当前套餐：`10 元 / 6000/day`
- 旧账号剩余套餐：`15 元 / 4500/day`
- 旧账号剩余时间：`20 天`

结果：

- 旧剩余权益折算为约 `10 元`
- 再按当前 `10 元档` 折算为 `30 天`
- 新账号套餐保持 `10 元 / 6000/day`
- 新账号到期时间成功追加 `30 天`

### 3. 旧付费账号并入当前免费账号

测试场景：

- 新账号当前套餐：免费
- 旧账号剩余套餐：`15 元 / 4500/day`
- 旧账号剩余时间：`20 天`

结果：

- 旧剩余权益折算为当前新模式下的 `15 元档`
- 新账号套餐变为 `15 元 / 9000/day`
- 到期时间正确生成

### 4. 官网状态展示

结果：

- 用户侧总览不再显示 `plan_id=...;.../day;.../minute`
- 当前展示改为：
  - 当前订阅模式
  - 当前套餐
  - 当前套餐标识
  - 当前剩余资源
  - 日请求上限
  - 加油包余额

### 5. 管理后台展示

结果：

- 用户列表“限额”列已改为“当前额度”
- 不再把 raw `limit_rule` 直接用于当前状态展示

---

## 当前状态

当前这轮改造已经达到：

1. 旧 SQL 导入后不保留历史迁移模式
2. 旧账号绑定时按剩余权益折算并入当前套餐
3. 已付费绑定与免费绑定两条链路都已实测通过
4. 官网用户前端不再显示旧镜像口径
5. 管理后台主要展示已切换到新字段
6. 客户端已接入新套餐/加油包状态字段

---

## 关联文档

规则说明：

- [legacy-sql-bind-migration-rules-2026-04-18.md](D:/git/gitlab/VoiceLinkServer1/docs/legacy-sql-bind-migration-rules-2026-04-18.md)

迁移与额度口径说明：

- [sql-migration-and-quota-rules-2026-04-18.md](D:/git/gitlab/VoiceLinkServer1/docs/sql-migration-and-quota-rules-2026-04-18.md)
