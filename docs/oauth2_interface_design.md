# OAuth2 接口设计稿（未来扩展）

> 当前版本不实施完整 OAuth2。本文件仅作为未来扩展设计保留。
> 当前版本采用“统一认证中心”方案：官网用 Session，客户端用 Access Token + Refresh Token。

## 1. 目标

本设计用于未来建立标准授权体系，覆盖：

- 官网 Web 登录
- 微信登录后统一发放平台会话
- 后续桌面客户端登录
- 受保护 API 的标准授权访问

如果未来进入 OAuth2 阶段，建议最小实现：

- Authorization Code + PKCE
- Refresh Token
- Web Session
- UserInfo
- Token Revoke

---

## 2. 何时启用本设计

建议仅在出现以下任一条件时再实施本文件：

- 需要第三方应用接入
- 需要开放 API 给外部开发者
- 需要插件/开发者平台
- 需要真正的授权码模式与 consent
- 需要多个独立客户端团队共享标准登录协议

当前版本不满足以上条件，因此不建议优先实施。

---

## 3. 域名与职责

建议按域名拆分：

- `account.example.com`：认证与 OAuth2
- `api.example.com`：资源 API
- `admin.example.com`：后台

认证中心负责：

- 登录
- 注册
- 微信回调
- OAuth2 授权
- token 下发与刷新

资源服务负责：

- 验证 access token
- 校验 scope
- 返回业务数据

---

## 4. 角色定义

### 3.1 用户

- 邮箱登录用户
- 微信登录用户
- 历史用户名密码迁移用户

### 3.2 OAuth Client

建议分两类：

#### `public`

- 官网前端
- 桌面客户端
- 原生客户端

特点：

- 不持有 `client_secret`
- 必须走 PKCE

#### `confidential`

- 内部服务
- 后端到后端调用

特点：

- 持有 `client_secret`

---

## 5. 推荐 scopes

建议第一阶段定义这些 scope：

| scope | 用途 |
| --- | --- |
| `openid` | 基础登录身份 |
| `profile` | 用户资料 |
| `email` | 读取邮箱资料 |
| `account:read` | 用户中心账户信息 |
| `usage:read` | 用量查询 |
| `orders:read` | 订单查询 |
| `billing:write` | 创建支付意图 |
| `api:transcribe` | 语音转写 |
| `api:translate` | 翻译 |
| `api:tts` | TTS |
| `admin:*` | 后台权限，不对普通 client 开放 |

建议官网默认申请：

- `openid profile email account:read usage:read orders:read billing:write`

---

## 6. 登录与授权形态

## 5.1 官网 Web 登录

建议使用：

- 服务端 session 作为官网会话
- OAuth2 code flow 作为统一授权模型

用户体验上：

- 官网访问时优先使用 Web session
- 前端需要访问资源 API 时，通过后端或 BFF 交换 access token

## 5.2 桌面客户端登录

建议使用：

- Authorization Code + PKCE

流程：

1. 客户端打开浏览器
2. 浏览器跳转授权页
3. 用户完成登录
4. 回调到客户端注册 URI
5. 客户端用 code + verifier 换 token

## 5.3 微信登录

微信登录不是单独 token 体系，而是账号接入方式。

流程：

1. 微信网页授权成功
2. 身份中心通过部署在公网服务器上的 `wechat-connector` 调用微信官方接口
3. 身份中心绑定或识别 `user_id`
4. 继续进入 OAuth2 授权页或直接建立站内 session

说明：

- 之所以引入 `wechat-connector`，是因为微信服务端接口通常受开发者 IP 白名单限制
- 固定出口 IP 应统一落在公网服务器

---

## 6. 接口列表

## 7.1 账号接口

### `POST /auth/register/email`

用途：

- 邮箱注册

请求：

```json
{
  "email": "user@example.com",
  "password": "******",
  "display_name": "demo"
}
```

响应：

```json
{
  "user_id": "u_123",
  "email": "user@example.com",
  "email_verification_required": true
}
```

### `POST /auth/login/password`

用途：

- 邮箱或历史用户名密码登录

请求：

```json
{
  "login": "user@example.com",
  "password": "******"
}
```

响应：

```json
{
  "session_created": true,
  "user": {
    "id": "u_123",
    "display_name": "demo"
  }
}
```

说明：

- 如果是浏览器环境，建议直接建立 session cookie。

### `POST /auth/logout`

用途：

- 注销当前会话

### `POST /auth/verify-email/request`

用途：

- 发送邮箱验证邮件

### `POST /auth/verify-email/confirm`

用途：

- 确认邮箱验证 token

### `POST /auth/forgot-password`

用途：

- 发起找回密码

### `POST /auth/reset-password`

用途：

- 提交新密码

---

## 7.2 微信接口

### `GET /auth/wechat/start`

用途：

- 发起微信网页登录

请求参数：

- `return_to`
- `bind_mode`
- `state`

行为：

- 服务端生成 state
- 跳转微信授权地址

### `GET /auth/wechat/callback`

用途：

- 微信授权回调

请求参数：

- `code`
- `state`

处理结果：

- 查找已绑定账号
- 已绑定则建立 session
- 未绑定则进入绑定引导页

### `POST /auth/wechat/bind`

用途：

- 将当前微信身份绑定到已登录账号

### `POST /auth/wechat/unbind`

用途：

- 解绑微信身份

限制：

- 解绑后必须保留至少一种其他可登录方式

---

## 7.3 OAuth2 标准接口

### `GET /oauth/authorize`

用途：

- 发起授权码流程

请求参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `response_type` | 是 | 固定为 `code` |
| `client_id` | 是 | OAuth client |
| `redirect_uri` | 是 | 回调地址 |
| `scope` | 是 | 空格分隔 scopes |
| `state` | 是 | 防 CSRF |
| `code_challenge` | 是 | PKCE |
| `code_challenge_method` | 是 | `S256` |

处理流程：

1. 校验 client
2. 校验 redirect_uri
3. 检查用户 session
4. 无 session 则跳转登录页
5. 登录后显示授权确认页
6. 生成授权码
7. 302 回跳 `redirect_uri`

成功回调示例：

```text
myapp://oauth/callback?code=abc123&state=xyz789
```

### `POST /oauth/token`

用途：

- 交换 access token / refresh token

支持 grant：

- `authorization_code`
- `refresh_token`
- `client_credentials` 可选

#### `grant_type=authorization_code`

请求：

```json
{
  "grant_type": "authorization_code",
  "client_id": "portal-web",
  "code": "abc123",
  "redirect_uri": "https://portal.example.com/callback",
  "code_verifier": "verifier"
}
```

响应：

```json
{
  "access_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "rt_123",
  "scope": "openid profile account:read usage:read"
}
```

#### `grant_type=refresh_token`

请求：

```json
{
  "grant_type": "refresh_token",
  "client_id": "portal-web",
  "refresh_token": "rt_123"
}
```

说明：

- 建议 refresh token 轮换
- 旧 refresh token 立即失效

### `POST /oauth/revoke`

用途：

- 撤销 refresh token 或 access token

请求：

```json
{
  "token": "rt_123",
  "token_type_hint": "refresh_token"
}
```

### `GET /oauth/userinfo`

用途：

- 获取登录用户基础信息

请求头：

```text
Authorization: Bearer <access_token>
```

响应：

```json
{
  "sub": "u_123",
  "display_name": "demo",
  "email": "user@example.com",
  "email_verified": true
}
```

---

## 7. Token 设计建议

## 7.1 Access Token

建议：

- 采用 JWT
- 有效期短，建议 1 小时
- 必带 `sub / aud / exp / iat / jti / scope / client_id`

示例 claims：

```json
{
  "iss": "https://account.example.com",
  "sub": "u_123",
  "aud": "api.example.com",
  "client_id": "portal-web",
  "scope": "openid profile account:read usage:read",
  "jti": "atk_123",
  "iat": 1770000000,
  "exp": 1770003600
}
```

## 7.2 Refresh Token

建议：

- 不使用 JWT
- 使用随机字符串
- 服务端存哈希和元数据
- 每次刷新都旋转

## 7.3 Session Cookie

建议：

- 官网使用 HttpOnly Cookie
- `SameSite=Lax` 或按回调链路调整
- 管理后台 session 与官网 session 分开存储

---

## 8. 授权与资源访问关系

资源服务校验逻辑建议：

1. 校验 token 签名
2. 校验 `aud`
3. 校验 `exp`
4. 校验 token 是否已撤销
5. 校验 scope
6. 注入 `current_user`

资源 API 与 scope 示例：

| 接口 | 建议 scope |
| --- | --- |
| `/api/account/status` | `account:read` |
| `/api/account/usage` | `usage:read` |
| `/api/whisper/transcriptions` | `api:transcribe` |
| `/api/func/webtranslate` | `api:translate` |
| `/api/func/tts` | `api:tts` |

---

## 9. 与现有接口的兼容方案

## 9.1 旧 `/api/login`

兼容期建议保留。

过渡方案：

1. 老客户端继续使用 `/api/login`
2. 新官网和新客户端只使用 OAuth2
3. 服务端内部逐步把旧用户身份映射为 `user_id`
4. 最终废弃旧接口

## 9.2 管理后台

后台不建议直接使用普通 OAuth client。

建议：

- 保留独立 `admin` session
- 后续再决定是否接入同一身份中心

---

## 10. 错误码建议

建议统一错误结构：

```json
{
  "error": "invalid_grant",
  "error_description": "authorization code is invalid or expired"
}
```

常见错误：

- `invalid_request`
- `unauthorized_client`
- `access_denied`
- `unsupported_response_type`
- `invalid_scope`
- `server_error`
- `temporarily_unavailable`
- `invalid_grant`
- `invalid_client`

---

## 11. 安全建议

- 所有 public client 强制 PKCE
- 授权码一次性消费
- refresh token 强制轮换
- 记录登录审计日志
- 微信登录 state 必须服务端存储并校验
- 邮箱验证、找回密码 token 只存哈希
- 不允许支付链路继续带密码语义

---

## 12. 推荐落地顺序

### 第一阶段

- `POST /auth/register/email`
- `POST /auth/login/password`
- `POST /auth/logout`
- `GET /oauth/authorize`
- `POST /oauth/token`
- `GET /oauth/userinfo`

### 第二阶段

- `POST /auth/verify-email/request`
- `POST /auth/verify-email/confirm`
- `POST /auth/forgot-password`
- `POST /auth/reset-password`
- `POST /oauth/revoke`

### 第三阶段

- `GET /auth/wechat/start`
- `GET /auth/wechat/callback`
- `POST /auth/wechat/bind`
- `POST /auth/wechat/unbind`

---

## 13. 结论

OAuth2 这一层的核心不是“换个登录接口”，而是把平台身份、官网会话、客户端授权和资源访问统一起来。

最小可用闭环应是：

1. 邮箱登录
2. Web session
3. Authorization Code + PKCE
4. Access Token + Refresh Token
5. Resource API scope 校验

在此基础上再叠加微信登录与账号绑定，风险最低。
