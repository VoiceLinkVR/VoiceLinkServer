# VoiceLinkVR / VRCLS 整个项目完整部署文档

## 1. 文档说明

本文档用于说明整个项目的完整部署方式，覆盖以下仓库和组件：

- 后端服务：`D:\git\gitlab\VoiceLinkServer1`
- 官网、用户中心、管理后台、状态页：`D:\git\gitlab\vrclswebsite`
- Windows 客户端：`D:\git\gitlab\VRCLS-CSharp`

本文档目标：

- 给出一份可以直接照着执行的中文部署说明
- 明确测试环境和正式环境的差异
- 明确旧数据库导入和新后端 `v2_*` 表的关系
- 明确官网、后台、状态页和客户端各自如何构建与发布

配套文档：

- 上线验证与测试报告：
  - [53-fullstack-migration-validation-and-launch-readiness-2026-04-20.md](D:\git\gitlab\VoiceLinkServer1\docs\53-fullstack-migration-validation-and-launch-readiness-2026-04-20.md)
- nginx 专项说明：
  - [53-nginx-configuration-guide-2026-04-20.md](D:\git\gitlab\vrclswebsite\docs\53-nginx-configuration-guide-2026-04-20.md)

---

## 2. 项目整体结构

### 2.1 组件划分

整个项目建议分为 4 个部署对象：

1. **MariaDB**
   - 保存旧业务历史表和新 `v2_*` 表
2. **Redis**
   - 用于限流、会话相关运行时能力
3. **后端 API 服务**
   - 提供 `v1`、`v2`、用户中心、管理后台接口
4. **前端静态站点**
   - 官网首页 `/`
   - 用户中心 `/app/`
   - 管理后台 `/admin/`
   - 状态页 `/status/`

客户端 `VRCLS-CSharp` **不是服务器常驻服务**，它的发布目标是 Windows 用户机器，用于连接后端 API。

### 2.2 推荐拓扑

正式部署建议拓扑如下：

```text
用户浏览器 / 客户端
        |
        v
     Nginx / 反向代理
        |
        +--> /            -> 官网静态页
        +--> /app/        -> 用户中心静态页
        +--> /admin/      -> 管理后台静态页
        +--> /status/     -> 状态页静态页
        +--> /v1/api/     -> 后端 API
        +--> /v2/         -> 后端 API
        |
        v
   VoiceLink 后端容器
        |
        +--> MariaDB
        +--> Redis
        +--> 外部语音识别/翻译服务 或 compose profile 内部服务
```

---

## 3. 运行环境要求

### 3.1 服务器要求

建议至少准备：

- Linux x86_64 服务器
- Docker
- Docker Compose Plugin
- `git`
- `curl`
- 可选：`rsync`

建议开放或内部使用的端口：

- `80` / `443`：前端站点与反向代理
- `8980`：后端 API
- `3306`：MariaDB，仅内网或临时导入时使用
- `6379`：Redis，建议仅容器网络内部使用

### 3.2 正式环境要求

正式上线建议具备：

- 固定域名
- HTTPS 证书
- 正确的反向代理头
- SMTP 邮箱
- 爱发电配置
- 对应语音识别、翻译服务地址

### 3.3 客户端构建环境

客户端发布机建议为 Windows 10/11，并安装：

- .NET 8 SDK
- PowerShell
- 可选：Inno Setup（如果需要做安装包）

---

## 4. 推荐服务器目录结构

建议在 Linux 服务器上使用如下目录：

```text
/opt/vrcls-prod-backend/
├─ compose.yaml
├─ .env
├─ app/                         # 后端源码
├─ data/
│  ├─ site_config_overrides.json
│  ├─ plan_catalog.json
│  └─ update/
├─ mysql/                       # MariaDB 数据目录
└─ redis/                       # Redis 持久化目录

/opt/vrcls-prod-frontend/
├─ dist-sites/                  # 官网/用户中心/后台/状态页静态资源
│  ├─ index.html
│  ├─ app/
│  ├─ admin/
│  ├─ status/
│  └─ assets/
└─ nginx.conf                   # 静态站点与 API 转发配置
```

---

## 5. 部署前必须明确的数据库策略

### 5.1 当前数据库策略

当前新后端的数据库策略是：

- **旧业务表保留原样**
  - `user`
  - `payment_order`
  - `request_log`
- **新后端活动业务写入 `v2_*` 表**
  - `v2_user`
  - `v2_payment_order`
  - `v2_request_audit_log`
  - `v2_user_identity_link`
  - `v2_user_subscription_state`
  - `v2_user_addon_wallet`
  - `v2_announcement_entry`
  - `v2_faq_entry`
  - `v2_site_config`
  - 以及其他 `v2_*` 聚合表

### 5.2 旧库导入后的行为

如果你先导入旧 SQL，再启动新后端：

- 新后端会在同一个数据库里创建/维护 `v2_*` 表
- 不会把旧表当成新的主业务表继续扩展使用
- 旧用户不会自动批量迁成新用户
- 旧用户是在**注册新账号后，绑定旧账号时**完成迁移

### 5.3 旧账号绑定时会发生什么

当新用户注册并绑定旧账号时：

1. 在 `v2_user` 上记录 `legacy_user_id`
2. 在 `v2_user_identity_link` 写入绑定关系
3. 旧 `user.is_active` 直接置为 `0`
4. 旧订单会克隆到 `v2_payment_order`
5. 旧订单原记录继续保留在旧 `payment_order`

---

## 6. 第一步：准备数据库

### 6.1 准备旧业务 SQL

如果你要从旧数据库迁移，旧业务 SQL 文件一般是：

- `D:\git\gitlab\VoiceLinkServer1\sqlback\voicelinkvr_server_main.sql`

如果你已经从别处导出了正式 SQL，则以你的正式 SQL 为准。

### 6.2 先启动数据库和 Redis

进入服务器后端目录：

```bash
cd /opt/vrcls-prod-backend
docker compose up -d mariadb redis
```

确认容器正常：

```bash
docker ps
```

### 6.3 导入旧数据库

如果是首次部署并需要迁移旧历史业务数据，建议顺序如下：

1. 先只启动 `mariadb` 和 `redis`
2. 导入旧 SQL
3. 再启动后端

导入命令示例：

```bash
cat /path/to/voicelinkvr_server_main.sql | docker exec -i vrcls-prod-mariadb mariadb -uroot -p'你的_ROOT_密码' voicelinkvr_server_main
```

如果你使用的是宿主机安装的 MariaDB，也可以直接用：

```bash
mariadb -uroot -p voicelinkvr_server_main < /path/to/voicelinkvr_server_main.sql
```

### 6.4 导入完成后的检查

建议检查旧表是否已存在：

```sql
SHOW TABLES LIKE 'user';
SHOW TABLES LIKE 'payment_order';
SHOW TABLES LIKE 'request_log';
```

---

## 7. 第二步：部署后端

## 7.1 后端仓库说明

后端仓库路径：

- `D:\git\gitlab\VoiceLinkServer1`

主部署文件：

- `compose.yaml`
- `Dockerfile`

### 7.2 同步后端源码到服务器

推荐两种方式，任选其一：

#### 方式 A：服务器直接 `git clone / git pull`

```bash
cd /opt/vrcls-prod-backend
git clone <你的仓库地址> app
```

后续更新：

```bash
cd /opt/vrcls-prod-backend/app
git pull
```

#### 方式 B：本地打包上传

你也可以把本地仓库压缩后上传到：

- `/opt/vrcls-prod-backend/app`

只要保证服务器上的 `app/` 目录就是后端源码目录即可。

### 7.3 准备 `.env`

在服务器：

- `/opt/vrcls-prod-backend/.env`

创建后端环境变量文件。

下面是一份**推荐模板**，请把占位符替换成你自己的真实值：

```env
COMPOSE_PROJECT_NAME=vrcls-prod-backend
VOICELINK_IMAGE=voicelink-prod:latest
VOICELINK_HOST_PORT=8980

JWT_SECRET_KEY=请替换为强随机密钥
SESSION_SECRET_KEY=请替换为强随机密钥
SESSION_COOKIE_HTTPS_ONLY=True
SESSION_COOKIE_SAME_SITE=lax
SESSION_COOKIE_MAX_AGE=2592000

MYSQL_ROOT_PASSWORD=请替换
MYSQL_DATABASE=voicelinkvr_server_main
MYSQL_USER=voicelinkvr
MYSQL_PASSWORD=请替换
SQL_PATH=mysql+pymysql://voicelinkvr:URL编码后的密码@mariadb:3306/voicelinkvr_server_main?charset=utf8mb4

LIMITER_REDIS_URL=redis://redis:6379/0

PORTAL_PUBLIC_BASE_URL=https://你的域名/app

API_V1_ENABLED=True
API_V1_LEGACY_LOGIN_ENABLED=True
API_V2_ENABLED=True
API_V2_AUTH_ENABLED=True
API_V2_PORTAL_ENABLED=True
API_V2_BILLING_ENABLED=True
API_V2_LEGACY_MIGRATION_ENABLED=True

DEFAULT_TEST_USER_ENABLED=True
DEFAULT_TEST_USER_UUID=00000000-0000-0000-0000-000000000001
DEFAULT_TEST_USER_EMAIL=test@your-domain.example
DEFAULT_TEST_USER_ACTIVE=True
DEFAULT_TEST_USER_IP_DAILY_QUOTA=1000
DEFAULT_TEST_USER_IP_RATE_LIMIT=10/minute
DEFAULT_TEST_USER_RESOURCE_QUOTA=0
DEFAULT_TEST_USER_USERNAME=testuser
DEFAULT_TEST_USER_PASSWORD=请替换

SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_FROM_EMAIL=no-reply@your-domain.example
SMTP_FROM_NAME=VoiceLinkVR
SMTP_USE_SSL=True
EMAIL_CODE_LENGTH=6
EMAIL_CODE_TTL_MINUTES=10

AFDIAN_TOKEN=请替换
AFDIAN_CUSTOM_ID_KEY=请替换为强随机密钥
AFDIAN_ORDER_CREATE_BASE_URL=https://ifdian.net/order/create
AFDIAN_QUERY_ORDER_URL=https://ifdian.net/api/open/query-order

WHISPER_HOST=你的语音识别服务地址
WHISPER_PORT=8000
WHISPER_BACKEND=faster-whisper
WHISPER_TRT_HOST=你的 whisper-trt 地址
WHISPER_TRT_PORT=18100
SENSEVOICE_HOST=你的 sensevoice 地址
SENSEVOICE_PORT=8800

ENABLE_WEB_TRANSLATORS=True
TRANSLATOR_SERVICES_LIST=bing,modernMt,cloudTranslation,caiyun,alibaba,youdao
GEMMA_TRANSLATE_ENABLED=True
GEMMA_TRANSLATE_URL=你的翻译服务地址:端口

LATEST_VERSION=v0.6.0-fix3
PACKAGE_BASE_URL=https://你的更新资源地址/VRCLS-windows-
PACKAGE_TYPE=.exe
```

### 7.4 测试环境和正式环境的差异

#### 测试环境 HTTP

如果你当前是直接用 `http://IP地址` 做测试，例如：

- `http://192.168.2.53`

那么必须设置：

```env
PORTAL_PUBLIC_BASE_URL=http://192.168.2.53/app
SESSION_COOKIE_HTTPS_ONLY=False
```

否则官网登录后，浏览器不会保存 `Secure` cookie。

#### 正式环境 HTTPS

正式上线到域名后，请务必切回：

```env
PORTAL_PUBLIC_BASE_URL=https://你的域名/app
SESSION_COOKIE_HTTPS_ONLY=True
```

### 7.5 启动后端

```bash
cd /opt/vrcls-prod-backend
docker compose build backend
docker compose up -d backend
```

如果数据库和 Redis 也还没起，可以直接：

```bash
docker compose up -d mariadb redis backend
```

### 7.6 后端启动后应发生什么

后端首次启动时会：

- 连接 MariaDB
- 检查并创建 `v2_*` 表
- 保留旧表 `user/payment_order/request_log`
- 创建默认测试用户（如果启用）

检查后端日志：

```bash
docker logs -f vrcls-prod-backend
```

关键成功标志通常包括：

- `Application startup complete`
- `Schema upgrade completed`

---

## 8. 第三步：部署官网 / 用户中心 / 管理后台 / 状态页

### 8.1 前端仓库说明

前端仓库路径：

- `D:\git\gitlab\vrclswebsite`

它包含 4 个站点输出：

- 根站首页：`/`
- 用户中心：`/app/`
- 管理后台：`/admin/`
- 状态页：`/status/`

### 8.2 本地构建命令

在 Windows PowerShell 中执行：

```powershell
cd D:\git\gitlab\vrclswebsite
powershell -ExecutionPolicy Bypass -File .\scripts\build-all.ps1
```

构建产物输出到：

- `D:\git\gitlab\vrclswebsite\dist-sites`

该目录会包含：

```text
dist-sites/
├─ index.html
├─ app/
├─ admin/
├─ status/
└─ assets/
```

### 8.3 上传静态资源到服务器

上传到服务器：

- `/opt/vrcls-prod-frontend/dist-sites`

示例：

```powershell
scp -r D:\git\gitlab\vrclswebsite\dist-sites\* root@你的服务器:/opt/vrcls-prod-frontend/dist-sites/
```

### 8.4 上传 nginx 配置

仓库内正式配置文件：

- `D:\git\gitlab\vrclswebsite\deploy\prod-nginx.conf`

上传到服务器：

- `/opt/vrcls-prod-frontend/nginx.conf`

示例：

```powershell
scp D:\git\gitlab\vrclswebsite\deploy\prod-nginx.conf root@你的服务器:/opt/vrcls-prod-frontend/nginx.conf
```

### 8.5 启动或更新前端容器

如果你用独立 nginx 容器来托管静态站点，建议用下面的方式：

```bash
docker rm -f vrcls-prod-frontend || true

docker run -d \
  --name vrcls-prod-frontend \
  --restart unless-stopped \
  -p 80:80 \
  -v /opt/vrcls-prod-frontend/dist-sites:/usr/share/nginx/html:ro \
  -v /opt/vrcls-prod-frontend/nginx.conf:/etc/nginx/conf.d/default.conf:ro \
  nginx:1.27.5-alpine
```

如果前端容器已经存在，只更新资源后可以直接：

```bash
docker restart vrcls-prod-frontend
```

### 8.6 重要说明

`/status-probe/api/status/public` 和 `/status-probe/api/status/refresh` 必须在 nginx 中存在**精确匹配**，否则状态页会拿到 HTML 而不是 JSON。

这两条配置在 `prod-nginx.conf` 中已经包含。

---

## 9. 第四步：配置 nginx / 反向代理

如果你直接用前端 nginx 容器对外暴露 `80/443`，则前面这一步已经足够。

如果你有自己的宿主机 nginx 或外层反代，请保证：

- `/v1/api/` 反代到后端
- `/v2/` 反代到后端
- `/status-probe/api/status/public` 返回 JSON
- `/status-probe/api/status/refresh` 返回 JSON
- `/admin/` 和 `/v2/adminapi/` 建议限制局域网访问

请优先参考专项文档：

- [53-nginx-configuration-guide-2026-04-20.md](D:\git\gitlab\vrclswebsite\docs\53-nginx-configuration-guide-2026-04-20.md)

---

## 10. 第五步：客户端构建与发布

### 10.1 客户端仓库

客户端仓库路径：

- `D:\git\gitlab\VRCLS-CSharp`

解决方案文件：

- `D:\git\gitlab\VRCLS-CSharp\VRCLS.sln`

### 10.2 普通发布

客户端仓库根目录中已有快捷入口：

```powershell
cd D:\git\gitlab\VRCLS-CSharp
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

这个脚本会实际转调：

- `scripts\publish_with_premodels.ps1 -Configuration Release -Runtime win-x64 -Zip`

### 10.3 带预置模型发布

也可以直接执行：

```powershell
cd D:\git\gitlab\VRCLS-CSharp
powershell -ExecutionPolicy Bypass -File .\scripts\publish_with_premodels.ps1 -Configuration Release -Runtime win-x64 -Zip
```

默认输出目录：

- `publish_with_premodels`

如果带 `-Zip`，还会在仓库根目录输出 zip 包。

### 10.4 客户端安装包

如果你后续要做安装包或更新清单，请看：

- `scripts\build-installer.ps1`
- `scripts\build-velopack-release.ps1`

客户端不是部署到服务器常驻运行，而是：

- 构建
- 打包
- 上传到更新分发地址
- 由后端 `LATEST_VERSION / PACKAGE_BASE_URL / PACKAGE_TYPE` 提供给客户端检查更新

---

## 11. 第六步：旧数据迁移流程

推荐的正式迁移顺序：

1. 新建干净的 MariaDB 实例或数据库目录
2. 只启动 `mariadb` 和 `redis`
3. 导入旧 SQL
4. 启动新后端
5. 检查 `v2_*` 表已创建
6. 构建并部署新官网和新后台
7. 用户开始注册新账号
8. 用户在新账号中绑定旧账号
9. 旧账号在旧 `user` 表中被禁用
10. 旧订单在绑定时迁移到 `v2_payment_order`

### 11.1 迁移后需要确认的点

确认以下几件事：

- 旧表数据还在
- `v2_user` 已创建
- `v2_payment_order` 已创建
- 新注册用户只写入 `v2_user`
- 绑定旧账号后：
  - 旧 `user.is_active=0`
  - `v2_user.legacy_user_id` 已写入
  - `v2_user_identity_link` 已写入
  - 旧订单已克隆到 `v2_payment_order`

---

## 12. 第七步：部署后验证清单

### 12.1 基础服务验证

```bash
docker ps
docker logs --tail 100 vrcls-prod-backend
docker logs --tail 100 vrcls-prod-frontend
```

### 12.2 页面验证

打开并确认：

- `https://你的域名/`
- `https://你的域名/app/`
- `https://你的域名/admin/`
- `https://你的域名/status/`

### 12.3 接口验证

未登录时：

```bash
curl -i https://你的域名/v2/auth/me
```

预期：

- `401` 是正常的，说明鉴权保护生效

状态页 JSON：

```bash
curl -I https://你的域名/status-probe/api/status/public
```

预期：

- `Content-Type: application/json`

### 12.4 登录验证

验证 3 条登录链路：

1. 新用户注册后登录
2. 已注册用户直接登录
3. 管理员后台登录

### 12.5 迁移验证

验证：

1. 新用户注册
2. 绑定旧账号
3. 旧账号禁用
4. 旧订单迁移

---

## 13. 第八步：正式上线时的关键切换项

正式上线前，至少确认这几项：

### 13.1 会话与域名

```env
PORTAL_PUBLIC_BASE_URL=https://你的正式域名/app
SESSION_COOKIE_HTTPS_ONLY=True
```

### 13.2 管理后台访问策略

建议保留：

- `/admin/` 局域网限制
- `/v2/adminapi/` 局域网限制

如果要公网访问后台，请自行增加：

- IP 白名单
- VPN
- 额外认证层
- WAF

### 13.3 数据库暴露策略

正式环境不建议把下面这些端口直接暴露到公网：

- `3306`
- `6379`
- `8980`

建议仅暴露：

- `80`
- `443`

---

## 14. 常见问题

### 14.1 为什么测试环境登录正常，正式环境又要改回 `SESSION_COOKIE_HTTPS_ONLY=True`？

因为测试环境通常是：

- `http://IP地址`

正式环境通常是：

- `https://正式域名`

HTTP 环境下如果 cookie 带 `Secure`，浏览器不会保存。
HTTPS 环境下则应该启用 `Secure`，这是正确的安全策略。

### 14.2 为什么状态页会出现“拿到 HTML 而不是 JSON”？

因为 nginx 没有正确处理：

- `/status-probe/api/status/public`
- `/status-probe/api/status/refresh`

这些路径如果没有精确匹配，可能被站点首页的 `try_files` 吞掉。

### 14.3 为什么管理后台有时会 504？

如果后台首屏同时并发请求多个大接口，而当前测试环境或数据库状态较差，就可能超时。

当前代码已经改为：

- 管理后台首屏串行加载

因此推荐使用最新构建后的后台静态资源。

### 14.4 新后端会不会把旧库直接改坏？

当前设计目标是：

- 新业务走 `v2_*`
- 旧库保留历史
- 旧用户绑定时才发生迁移性写入

但任何正式切库前，仍然建议先做数据库备份。

---

## 15. 回滚建议

如果上线后需要回滚，建议顺序如下：

1. 保留当前数据库快照
2. 回滚前端静态资源目录：
   - `/opt/vrcls-prod-frontend/dist-sites`
   - `/opt/vrcls-prod-frontend/nginx.conf`
3. 回滚后端代码或镜像版本
4. 重启后端容器
5. 如果是严重问题，再回滚数据库到部署前快照

不建议在没有备份的情况下直接覆盖当前数据库。

---

## 16. 推荐的完整部署顺序

如果你要从零到上线，建议使用这个顺序：

1. 准备服务器、Docker、目录结构
2. 准备 MariaDB 和 Redis
3. 导入旧 SQL
4. 同步后端源码
5. 配置后端 `.env`
6. 启动后端并确认 `v2_*` 表创建成功
7. 本地构建前端 `dist-sites`
8. 上传静态资源和 nginx 配置
9. 启动或重启前端 nginx 容器
10. 配置正式外层 nginx / HTTPS
11. 验证首页、用户中心、后台、状态页
12. 验证注册、登录、绑定旧账号
13. 验证客户端更新与登录
14. 最后再切正式流量

---

## 17. 结语

如果你只想记住最关键的 5 件事，可以只记这 5 条：

1. **旧 SQL 先导入，再启动新后端。**
2. **新后端业务写入 `v2_*`，旧表保留历史。**
3. **测试环境 HTTP 要用 `SESSION_COOKIE_HTTPS_ONLY=False`，正式 HTTPS 要改回 `True`。**
4. **前端部署本质上就是把 `dist-sites` 和 `nginx.conf` 放到服务器，再重启前端 nginx 容器。**
5. **状态页必须保证 `/status-probe/api/status/public` 真返回 JSON。**
