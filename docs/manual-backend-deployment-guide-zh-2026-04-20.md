# VoiceLinkServer1 后端手工部署文档

## 1. 文档目的

本文档只讲一件事：

- **如何手工部署 `VoiceLinkServer1` 这个后端**

不包含：

- 官网静态页部署
- 管理后台静态页部署
- 客户端打包发布

如果你要看整项目部署，请看：

- [complete-project-deployment-guide-zh-2026-04-20.md](D:\git\gitlab\VoiceLinkServer1\docs\complete-project-deployment-guide-zh-2026-04-20.md)

---

## 2. 适用场景

这份文档适用于以下两种场景：

1. 你要在一台 Linux 服务器上手工部署新后端
2. 你要把旧 SQL 导入后，让新后端接管新体系业务

本文默认你使用：

- Linux 服务器
- Docker
- Docker Compose Plugin

---

## 3. 部署前必须先确认的 3 件事

### 3.1 新后端和旧数据库的关系

当前新后端的数据库策略是：

- 旧表保留历史：
  - `user`
  - `payment_order`
  - `request_log`
- 新业务写入新表：
  - `v2_user`
  - `v2_payment_order`
  - `v2_request_audit_log`
  - 以及其他 `v2_*` 表

### 3.2 旧 SQL 导入顺序

如果你要导入旧业务 SQL，推荐顺序是：

1. 先启动 MariaDB
2. 导入旧 SQL
3. 再启动新后端

### 3.3 不建议双活共库

可以：

- 旧 SQL 导入到同一个数据库里
- 然后只运行新后端

不建议：

- 老后端和新后端长期同时在线
- 两边同时写同一个业务库

原因是新后端在迁移、绑定、旧支付兼容链路里仍然会碰旧表。

---

## 4. 推荐部署模式

推荐你使用下面这套结构：

```text
/opt/voicelink-backend/
├─ compose.yaml
├─ .env
├─ app/                     # VoiceLinkServer1 仓库内容
├─ mysql/                   # MariaDB 数据目录
├─ redis/                   # Redis 数据目录
└─ data/                    # 后端挂载数据目录
```

说明：

- `app/` 里放后端仓库
- `compose.yaml` 放在 `/opt/voicelink-backend/`
- `mysql/`、`redis/`、`data/` 都在宿主机持久化

---

## 5. 服务器准备

先安装基础软件：

```bash
apt update
apt install -y git curl
```

确认 Docker 可用：

```bash
docker --version
docker compose version
```

创建目录：

```bash
mkdir -p /opt/voicelink-backend
mkdir -p /opt/voicelink-backend/mysql
mkdir -p /opt/voicelink-backend/redis
mkdir -p /opt/voicelink-backend/data
```

---

## 6. 获取后端代码

### 方式 A：服务器直接拉代码

```bash
cd /opt/voicelink-backend
git clone <你的仓库地址> app
```

### 方式 B：本地打包上传

你也可以在本地把 `VoiceLinkServer1` 上传到：

- `/opt/voicelink-backend/app`

只要最终目录里能看到这些文件即可：

```text
/opt/voicelink-backend/app/
├─ Dockerfile
├─ run.py
├─ src/
└─ compose.yaml
```

---

## 7. 推荐的后端部署 compose

建议不要直接拿仓库根目录的 `compose.yaml` 当生产入口文件。

原因：

- 仓库根 `compose.yaml` 更偏通用/开发用途
- 它默认不帮你把 MariaDB 一起封装进去

推荐在 `/opt/voicelink-backend/compose.yaml` 手工写一份生产部署 compose。

示例：

```yaml
services:
  mariadb:
    image: mariadb:10.11
    container_name: voicelink-mariadb
    restart: unless-stopped
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
      - --skip-name-resolve
    ports:
      - "3306:3306"
    environment:
      MARIADB_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MARIADB_DATABASE: ${MYSQL_DATABASE}
      MARIADB_USER: ${MYSQL_USER}
      MARIADB_PASSWORD: ${MYSQL_PASSWORD}
    volumes:
      - ./mysql:/var/lib/mysql
    healthcheck:
      test: ["CMD-SHELL", "mariadb-admin ping -h 127.0.0.1 -uroot -p$$MYSQL_ROOT_PASSWORD || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 30s
    networks:
      - backend_internal

  redis:
    image: redis:7-alpine
    container_name: voicelink-redis
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - ./redis:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 15
    networks:
      - backend_internal

  backend:
    build:
      context: ./app
    image: voicelink-backend:latest
    container_name: voicelink-backend
    restart: unless-stopped
    depends_on:
      mariadb:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8980:8980"
    volumes:
      - ./data:/usr/src/app/data
    environment:
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      SESSION_SECRET_KEY: ${SESSION_SECRET_KEY}
      SESSION_COOKIE_HTTPS_ONLY: ${SESSION_COOKIE_HTTPS_ONLY}
      SESSION_COOKIE_SAME_SITE: ${SESSION_COOKIE_SAME_SITE}
      SESSION_COOKIE_MAX_AGE: ${SESSION_COOKIE_MAX_AGE}

      SQL_PATH: ${SQL_PATH}
      LIMITER_REDIS_URL: ${LIMITER_REDIS_URL}
      PORTAL_PUBLIC_BASE_URL: ${PORTAL_PUBLIC_BASE_URL}

      API_V1_ENABLED: ${API_V1_ENABLED}
      API_V1_LEGACY_LOGIN_ENABLED: ${API_V1_LEGACY_LOGIN_ENABLED}
      API_V2_ENABLED: ${API_V2_ENABLED}
      API_V2_AUTH_ENABLED: ${API_V2_AUTH_ENABLED}
      API_V2_PORTAL_ENABLED: ${API_V2_PORTAL_ENABLED}
      API_V2_BILLING_ENABLED: ${API_V2_BILLING_ENABLED}
      API_V2_LEGACY_MIGRATION_ENABLED: ${API_V2_LEGACY_MIGRATION_ENABLED}

      DEFAULT_TEST_USER_ENABLED: ${DEFAULT_TEST_USER_ENABLED}
      DEFAULT_TEST_USER_UUID: ${DEFAULT_TEST_USER_UUID}
      DEFAULT_TEST_USER_EMAIL: ${DEFAULT_TEST_USER_EMAIL}
      DEFAULT_TEST_USER_ACTIVE: ${DEFAULT_TEST_USER_ACTIVE}
      DEFAULT_TEST_USER_IP_DAILY_QUOTA: ${DEFAULT_TEST_USER_IP_DAILY_QUOTA}
      DEFAULT_TEST_USER_IP_RATE_LIMIT: ${DEFAULT_TEST_USER_IP_RATE_LIMIT}
      DEFAULT_TEST_USER_RESOURCE_QUOTA: ${DEFAULT_TEST_USER_RESOURCE_QUOTA}
      DEFAULT_TEST_USER_USERNAME: ${DEFAULT_TEST_USER_USERNAME}
      DEFAULT_TEST_USER_PASSWORD: ${DEFAULT_TEST_USER_PASSWORD}

      SMTP_HOST: ${SMTP_HOST}
      SMTP_PORT: ${SMTP_PORT}
      SMTP_USERNAME: ${SMTP_USERNAME}
      SMTP_PASSWORD: ${SMTP_PASSWORD}
      SMTP_FROM_EMAIL: ${SMTP_FROM_EMAIL}
      SMTP_FROM_NAME: ${SMTP_FROM_NAME}
      SMTP_USE_SSL: ${SMTP_USE_SSL}
      EMAIL_CODE_LENGTH: ${EMAIL_CODE_LENGTH}
      EMAIL_CODE_TTL_MINUTES: ${EMAIL_CODE_TTL_MINUTES}

      AFDIAN_TOKEN: ${AFDIAN_TOKEN}
      AFDIAN_CUSTOM_ID_KEY: ${AFDIAN_CUSTOM_ID_KEY}
      AFDIAN_ORDER_CREATE_BASE_URL: ${AFDIAN_ORDER_CREATE_BASE_URL}
      AFDIAN_QUERY_ORDER_URL: ${AFDIAN_QUERY_ORDER_URL}

      WHISPER_HOST: ${WHISPER_HOST}
      WHISPER_PORT: ${WHISPER_PORT}
      WHISPER_BACKEND: ${WHISPER_BACKEND}
      WHISPER_TRT_HOST: ${WHISPER_TRT_HOST}
      WHISPER_TRT_PORT: ${WHISPER_TRT_PORT}
      SENSEVOICE_HOST: ${SENSEVOICE_HOST}
      SENSEVOICE_PORT: ${SENSEVOICE_PORT}

      ENABLE_WEB_TRANSLATORS: ${ENABLE_WEB_TRANSLATORS}
      TRANSLATOR_SERVICES_LIST: ${TRANSLATOR_SERVICES_LIST}
      GEMMA_TRANSLATE_ENABLED: ${GEMMA_TRANSLATE_ENABLED}
      GEMMA_TRANSLATE_URL: ${GEMMA_TRANSLATE_URL}

      LATEST_VERSION: ${LATEST_VERSION}
      PACKAGE_BASE_URL: ${PACKAGE_BASE_URL}
      PACKAGE_TYPE: ${PACKAGE_TYPE}
    networks:
      - backend_internal

networks:
  backend_internal:
    name: voicelink-backend-internal
```

---

## 8. 准备 `.env`

在：

- `/opt/voicelink-backend/.env`

创建环境变量文件。

下面是推荐模板。

请注意：

- 所有密钥都要替换
- 数据库密码如果写入 `SQL_PATH`，要做 URL 编码

```env
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

WHISPER_TRT_HOST=你的whisper-trt服务地址
WHISPER_TRT_PORT=18100

SENSEVOICE_HOST=你的sensevoice服务地址
SENSEVOICE_PORT=8800

ENABLE_WEB_TRANSLATORS=True
TRANSLATOR_SERVICES_LIST=bing,modernMt,cloudTranslation,caiyun,alibaba,youdao
GEMMA_TRANSLATE_ENABLED=True
GEMMA_TRANSLATE_URL=你的翻译服务地址:端口

LATEST_VERSION=v0.6.0-fix3
PACKAGE_BASE_URL=https://你的更新资源地址/VRCLS-windows-
PACKAGE_TYPE=.exe
```

---

## 9. 测试环境和正式环境的关键差异

### 9.1 如果你是 HTTP 测试环境

例如你直接访问：

- `http://192.168.2.53`

那么需要设置：

```env
PORTAL_PUBLIC_BASE_URL=http://192.168.2.53/app
SESSION_COOKIE_HTTPS_ONLY=False
```

否则官网登录后浏览器不会保存带 `Secure` 的 session cookie。

### 9.2 如果你是正式 HTTPS 环境

正式域名上线时，必须设置：

```env
PORTAL_PUBLIC_BASE_URL=https://你的域名/app
SESSION_COOKIE_HTTPS_ONLY=True
```

---

## 10. 首次部署：如果你要导入旧 SQL

### 10.1 先只启动数据库和 Redis

```bash
cd /opt/voicelink-backend
docker compose up -d mariadb redis
```

### 10.2 导入旧 SQL

假设旧 SQL 在：

- `/root/voicelinkvr_server_main.sql`

导入示例：

```bash
cat /root/voicelinkvr_server_main.sql | docker exec -i voicelink-mariadb mariadb -uroot -p'你的_ROOT_密码' voicelinkvr_server_main
```

### 10.3 检查旧表是否存在

```bash
docker exec -it voicelink-mariadb mariadb -uroot -p
```

进入后执行：

```sql
USE voicelinkvr_server_main;
SHOW TABLES LIKE 'user';
SHOW TABLES LIKE 'payment_order';
SHOW TABLES LIKE 'request_log';
```

### 10.4 再启动后端

```bash
docker compose build backend
docker compose up -d backend
```

---

## 11. 正常启动后端

如果你不需要先导入旧 SQL，可以直接：

```bash
cd /opt/voicelink-backend
docker compose build backend
docker compose up -d mariadb redis backend
```

检查容器：

```bash
docker ps
```

查看后端日志：

```bash
docker logs -f voicelink-backend
```

你应该看到类似：

- `Application startup`
- `Schema upgrade completed`
- `Application startup complete`

---

## 12. 启动后会发生什么

当前新后端启动时会：

1. 连接数据库
2. 创建 `v2_*` 表
3. 创建或维护默认测试用户

当前活动启动路径不会主动执行旧 `main.py` 里的那套旧表升级逻辑，而是走新的 `run_schema_upgrade()`。

但是要注意：

- 这不代表新后端完全不会碰旧表
- 在迁移绑定、旧兼容链路和旧支付激活链路里，它仍会读写旧表

---

## 13. 部署后必须验证的项目

### 13.1 基础接口

未登录鉴权检查：

```bash
curl -i http://127.0.0.1:8980/v2/auth/me
```

预期：

- `401 Unauthorized`

### 13.2 数据库表检查

进入数据库后确认：

```sql
SHOW TABLES LIKE 'v2_user';
SHOW TABLES LIKE 'v2_payment_order';
SHOW TABLES LIKE 'v2_request_audit_log';
SHOW TABLES LIKE 'v2_user_identity_link';
```

### 13.3 注册与登录

至少要验证：

1. 新用户发送注册验证码
2. 新用户注册
3. 新用户登录
4. 登录后访问受保护接口

### 13.4 旧账号绑定迁移

如果你导入了旧 SQL，还应验证：

1. 新用户绑定旧账号
2. 旧 `user.is_active` 变为 `0`
3. `v2_user.legacy_user_id` 被写入
4. `v2_user_identity_link` 被写入
5. 旧订单被克隆到 `v2_payment_order`

---

## 14. 更新后端的标准流程

后端更新建议按下面步骤做：

```bash
cd /opt/voicelink-backend/app
git pull

cd /opt/voicelink-backend
docker compose build backend
docker compose up -d backend
```

更新后检查：

```bash
docker logs --tail 100 voicelink-backend
```

---

## 15. 回滚建议

建议回滚顺序：

1. 先保留当前数据库快照
2. 回滚后端代码或镜像
3. 重建后端容器
4. 观察日志

如果只是代码回滚：

```bash
cd /opt/voicelink-backend/app
git checkout <旧版本提交号>

cd /opt/voicelink-backend
docker compose build backend
docker compose up -d backend
```

如果问题涉及数据库行为，再考虑回滚数据库。

---

## 16. 常见问题

### 16.1 为什么登录接口 200，但后续接口全 401？

通常是因为你在 HTTP 环境下仍然配置了：

```env
SESSION_COOKIE_HTTPS_ONLY=True
```

浏览器会丢弃 `Secure` cookie。

### 16.2 为什么状态页会显示拿到 HTML，而不是 JSON？

这不是后端问题，是 nginx 没有正确处理：

- `/status-probe/api/status/public`
- `/status-probe/api/status/refresh`

### 16.3 新后端能和老后端同时跑吗？

不建议长期双活共用一个业务库。

可以：

- 旧 SQL 导入后，只运行新后端

不建议：

- 老后端和新后端同时在线并同时写同一个库

### 16.4 后端启动后会不会改坏旧表？

当前活动启动路径主要创建 `v2_*` 表，不是继续把旧表当新业务主表。

但是：

- 新后端在旧兼容业务流程中仍然会碰旧 `user` 和旧 `payment_order`

所以正式部署前仍然建议先做数据库备份。

---

## 17. 最简部署顺序总结

如果你想最简单地部署这个后端，可以只记下面 8 步：

1. 准备 Linux 服务器和 Docker
2. 创建 `/opt/voicelink-backend/`
3. 上传后端代码到 `/opt/voicelink-backend/app`
4. 在 `/opt/voicelink-backend/` 写 `compose.yaml`
5. 在 `/opt/voicelink-backend/` 写 `.env`
6. 启动 `mariadb` 和 `redis`
7. 如果有旧 SQL，先导入
8. 构建并启动 `backend`

命令汇总：

```bash
cd /opt/voicelink-backend
docker compose up -d mariadb redis

# 如需导入旧 SQL，先导入

docker compose build backend
docker compose up -d backend
docker logs -f voicelink-backend
```

如果你后面需要，我还可以继续给你补两份东西：

1. 一份可直接复制的生产 `.env.example`
2. 一份“后端部署检查清单”简版文档
