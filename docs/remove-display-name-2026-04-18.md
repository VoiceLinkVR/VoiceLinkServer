# 删除 `display_name` / 公开昵称 说明

## 结论

本次改造已按“`username` 可以完全替代 `display_name`”的前提，移除了用户账号体系中的 `display_name / 公开昵称` 逻辑。

现在系统中：

- 不再依赖后端 `display_name` 字段
- 不再通过接口返回 `display_name`
- 官网和后台不再提供“公开昵称 / 显示名称”编辑入口
- 客户端不再依赖后端 `display_name` 来展示当前账号

## 后端改动

涉及仓库：

- [VoiceLinkServer1](D:/git/gitlab/VoiceLinkServer1)

主要处理：

1. 从用户 ORM 模型中移除 `display_name`
2. 从 auth/admin/portal schema 中移除 `display_name`
3. 从接口返回中移除 `display_name`
4. 用户资料更新改成只允许更新 `username`
5. 注册、管理员创建、旧版兼容创建用户时，不再写入 `display_name`

说明：

- 新后端启动时会尝试删除 `user.display_name` 和 `v2_user.display_name` 旧列
- 旧 SQL 导入到新库后，也会在升级阶段被清理到只保留 `username` 体系

## 官网改动

涉及仓库：

- [vrclswebsite](D:/git/gitlab/vrclswebsite)

主要处理：

1. Portal 用户资料页移除“公开昵称”编辑项
2. 当前身份展示统一使用 `username`
3. 授权页显示绑定账号时统一显示 `username`
4. 管理后台移除“显示名称”输入项
5. 管理后台用户列表、最近用户、顶部身份区统一改为显示 `username`

## 客户端改动

涉及仓库：

- [VRCLS-CSharp](D:/git/gitlab/VRCLS-CSharp)

主要处理：

1. 本地 `UserInfo` 不再保存 `display_name`
2. `BackendAuthService` 不再从后端读取 `display_name`
3. 客户端账号显示逻辑改为：
   - 优先 `username`
   - 否则 `user_uuid`
   - 匿名模式和旧版兼容模式保留原有特殊文案

## 当前展示规则

现在所有和“用户名字”相关的显示都按以下优先级：

1. `username`
2. `user_uuid` 短码
3. 特殊模式固定文案

不再存在：

- `display_name`
- `公开昵称`
- `显示名称`

作为独立用户资料字段的业务意义。

## 当前状态

截至 2026-04-18，本轮已经完成以下收口：

1. 后端代码、官网源码、客户端认证状态模型中的 `display_name` 业务口径已移除。
2. 新后端启动时会主动清理 `user.display_name` 与 `v2_user.display_name` 历史列。
3. 测试服务器 `root@192.168.2.53` 的预览后端已重新部署并恢复启动。
4. 测试服务器预览站点已重新同步，旧静态资源目录已清空后重放，避免旧包残留误判。

## 测试服验证

测试时间：2026-04-18

验证结果：

- `http://127.0.0.1:18980/v2/adminapi/auth/state` 返回 `200`
- 预览后端启动日志显示已执行：
  - `Dropped legacy column user.display_name`
  - `Dropped legacy column v2_user.display_name`
- MySQL 中执行
  - `SHOW COLUMNS FROM user LIKE 'display_name'`
  - `SHOW COLUMNS FROM v2_user LIKE 'display_name'`
  返回空结果，说明旧列已不存在

前端静态产物说明：

- 如果对压缩后的前端 JS 做全文检索，仍可能看到 `displayName`
- 该命中来自 Vue 运行时内部的组件显示名属性，不是我们业务上的 `display_name`
- 业务字段 `display_name` 现已无前端接口、无前端状态字段、无前端表单入口、无后端列

额外处理：

- 预览环境原本缺少 `AFDIAN_CUSTOM_ID_KEY` 容器注入，导致新版安全校验阻止后端启动
- 本次已同步修正测试服预览 `compose.yaml` 与 `.env`，后端已恢复正常
