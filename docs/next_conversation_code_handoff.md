# 下个对话代码改造交接文档

## 1. 目的

本文件用于下一次新对话直接进入代码改造阶段，不再重复讨论总体方向。

当前已经确定的产品与技术结论如下：

- 当前版本不做微信登录
- 当前版本不做完整 OAuth2
- 当前版本采用“统一认证中心”方案
- 官网与客户端统一收敛到内部 `user_id`
- 官网使用 Session
- 客户端使用 Access Token + Refresh Token
- 用户中心与爱发电功能逐步从旧客户端模式迁移到官网

---

## 1.1 本次对话产出的文档

下个对话开始前，建议优先阅读以下文档。

### 当前版本主依据

1. [portal_oauth2_wechat_migration_plan.md](D:/git/gitlab/VoiceLinkServer1/docs/portal_oauth2_wechat_migration_plan.md)
   - 当前总方案主文档
   - 已明确：不做微信登录、不做完整 OAuth2、改为统一认证中心

2. [portal_database_schema_design.md](D:/git/gitlab/VoiceLinkServer1/docs/portal_database_schema_design.md)
   - 数据库方向文档
   - 当前版本虽然不做完整 OAuth2，但可用于指导认证模型与后续扩展

3. [deployment_topology_frp_nginx.md](D:/git/gitlab/VoiceLinkServer1/docs/deployment_topology_frp_nginx.md)
   - 部署拓扑文档
   - 主要用于公网服务器、FRP、入口拓扑设计参考

### 未来扩展参考，不是当前版本实施依据

4. [oauth2_interface_design.md](D:/git/gitlab/VoiceLinkServer1/docs/oauth2_interface_design.md)
   - 已改为“未来扩展”文档
   - 当前版本不按此文直接实现

5. [wechat_login_binding_design.md](D:/git/gitlab/VoiceLinkServer1/docs/wechat_login_binding_design.md)
   - 已明确当前版本不实施
   - 仅保留为未来参考

### 当前交接文档

6. [next_conversation_code_handoff.md](D:/git/gitlab/VoiceLinkServer1/docs/next_conversation_code_handoff.md)
   - 下个对话的直接执行说明

---

## 2. 当前版本实施范围

### 必做

- 邮箱注册
- 邮箱登录
- 客户端 token 登录
- refresh token
- logout
- 为后续找回密码、邮箱验证预留模型
- 统一 `user_id`
- 支付链路从“用户名密码驱动”向“用户身份驱动”演进

### 不做

- 微信登录
- 公众号绑定
- 网站应用扫码登录
- OAuth2 授权码模式
- PKCE
- 第三方 client 管理
- consent 页面

---

## 3. 当前建议的技术落点

## 3.1 认证形态

### 官网

- 使用 HttpOnly Session Cookie

### 客户端

- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- Bearer Access Token
- Refresh Token

### 服务端统一

- 统一认证依赖
- 统一 `current_user`
- 统一 `user_id`

---

## 4. 建议的第一批代码改造目标

建议下一对话先做“认证基础骨架”，不要一口气动支付和官网页面。

### 第一批目标

1. 新增认证相关数据模型
2. 新增认证相关 schema
3. 新增认证路由
4. 新增 token/session 工具函数
5. 保留旧 `/api/login` 兼容
6. 不做本地测试，由用户手动验证

---

## 5. 建议第一批新增对象

## 5.1 数据表 / 模型

建议先在现有 SQLAlchemy 模型上新增最小集合：

- `UserSession`
- `RefreshToken`

视实现方式可选：

- 先不拆 `User`，继续复用旧 `User`
- 先不引入完整 `user_credentials` 表
- 先用 `User.username` 兼容邮箱字段，或新增邮箱字段

更稳的方案是：

- 给 `User` 增加 `email`
- 给 `User` 增加 `email_verified`
- 给 `User` 增加 `display_name`
- 新增 `RefreshToken`
- 新增 `UserSession`

如果你要一步到位，也可以直接新建：

- `user_credentials`

但第一批代码改造建议不要铺太大。

---

## 6. 建议第一批接口

建议新增路由文件：

- `src/routers/auth.py`

建议接口：

### `POST /auth/register`

输入：

- `email`
- `password`
- `display_name`

### `POST /auth/login`

输入：

- `login`
- `password`

输出：

- `access_token`
- `refresh_token`
- `user`

### `POST /auth/refresh`

输入：

- `refresh_token`

输出：

- 新的 `access_token`
- 可选新的 `refresh_token`

### `POST /auth/logout`

输入：

- `refresh_token` 或当前 session

### `GET /auth/me`

返回当前用户资料

---

## 7. 现有代码中的主要切入点

### 入口

- `src/main.py`

需要做：

- 挂载新 `auth` 路由

### 鉴权依赖

- `src/core/dependencies.py`

需要做：

- 保留旧 `get_current_user`
- 新增统一认证相关 token 解析逻辑
- 逐步让新认证接口与旧接口共存

### 数据模型

- `src/db/models.py`

需要做：

- 追加新模型，避免破坏旧模型

### Schemas

- `src/schemas/`

建议新增：

- `src/schemas/auth.py`

### 路由

- `src/routers/`

建议新增：

- `src/routers/auth.py`

---

## 8. 与旧系统的兼容策略

### 保留

- `/api/login`
- 旧 JWT 校验逻辑
- 旧 `User` 模型
- 旧用户中心接口

### 新增

- `/auth/*`
- 新 refresh token 体系
- 新 session/token 体系

### 原则

- 先并行
- 再迁移
- 最后收口

不要第一步就删旧逻辑。

---

## 9. 爱发电链路暂时怎么处理

下一对话先不要重构整条支付链路。

第一阶段建议只做到：

- 新认证体系能识别用户
- 后续支付接口可以基于 `current_user` 扩展

先不要在同一轮里处理：

- `custom_id` 去密码化
- `payment_intent`
- 订单模型重构

这些放第二轮更稳。

---

## 10. 当前工作区注意事项

当前工作区不是干净状态。

已知本地改动包括：

- `src/db/models.py`
- `src/main.py`
- `src/server.py`
- `src/routers/portal_api.py`
- `src/schemas/portal.py`

下一对话开始改代码前，应当先确认：

- 是基于当前工作区继续改
- 还是继续按已提交版本为基线做增量

不要直接覆盖未知改动。

---

## 11. 验收优先级

下一对话第一轮代码改造完成后，优先让用户手动验证：

1. 邮箱注册是否成功
2. 邮箱登录是否成功
3. 客户端 token 是否可用
4. refresh token 是否可刷新
5. 旧 `/api/login` 是否仍兼容

注意：

- 不做本地测试执行
- 由用户手动验证并反馈结果

---

## 12. 下个对话的推荐起手式

建议下个对话直接从这几个动作开始：

1. 检查当前工作区状态
2. 读取 `src/main.py`、`src/core/dependencies.py`、`src/db/models.py`
3. 新增 `src/schemas/auth.py`
4. 新增 `src/routers/auth.py`
5. 扩展 `src/db/models.py`
6. 在 `src/main.py` 挂载新路由

---

## 13. 最终决策摘要

当前版本路线已经确定：

- 做官网
- 做统一认证中心
- 做邮箱登录
- 做用户中心迁移
- 做支付链路后续重构

当前版本明确不做：

- 微信登录
- 完整 OAuth2

这份范围不要在下一对话里重新讨论，直接按此进入代码实现。
