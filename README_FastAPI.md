# VoiceLinkVR FastAPI 服务端使用文档

## 项目概述

本项目是基于FastAPI框架重构的VoiceLinkVR语音转录服务，提供语音识别、翻译、用户管理等功能。相比原Flask版本，具有更好的性能、自动API文档和现代Python特性支持。

## 主要特性

- 🚀 **高性能**: 基于FastAPI异步框架和Uvicorn服务器
- 🔐 **JWT认证**: 安全的用户认证和授权系统
- 📊 **速率限制**: 基于Redis的API速率限制
- 📚 **自动文档**: 自动生成Swagger UI和ReDoc API文档
- 👥 **用户管理**: 完整的用户注册、登录、权限管理
- 🎨 **管理界面**: 基于Jinja2的管理后台界面
- 🗣️ **语音转录**: 支持Whisper和SenseVoice语音识别
- 🌐 **翻译服务**: 集成多种翻译服务
- 📁 **文件上传**: 支持音频文件上传处理

## 技术栈

- **框架**: FastAPI + Uvicorn
- **数据库**: SQLAlchemy + SQLite/MySQL
- **认证**: python-jose + passlib
- **速率限制**: slowapi + Redis
- **模板**: Jinja2
- **依赖注入**: FastAPI原生支持

## 快速开始

### 环境要求

- Python 3.8+
- Redis (可选，用于速率限制)
- SQLite/MySQL数据库

### 安装依赖

```bash
cd src
pip install -r requirements.txt
```

### 环境变量配置

创建 `.env` 文件并配置以下参数：

```env
# Whisper配置
WHISPER_HOST=127.0.0.1
WHISPER_PORT=8000
WHISPER_BACKEND=faster-whisper
WHISPER_TRT_HOST=127.0.0.1
WHISPER_TRT_PORT=8000
WHISPER_APIKEY=something
WHISPER_MODEL=Systran/faster-whisper-large-v3

# SenseVoice配置
SENSEVOICE_HOST=127.0.0.1
SENSEVOICE_PORT=8800

# JWT配置
JWT_SECRET_KEY=wVLAF_13N6XL_QmP.DjkKsV
JWT_ACCESS_TOKEN_EXPIRES_MINUTES=10080
JWT_ALGORITHM=HS256

# 安全配置
FLASK_SECRET_KEY=wVddLAF_13dsdddN6XL_QmP.DjkKsV

# 数据库配置
SQL_PATH=sqlite:///./data/db/users.db
SQLALCHEMY_DATABASE_URL=sqlite:///./data/db/users.db

# 速率限制配置
LIMIT_ENABLE=true
LIMITER_REDIS_URL=redis://localhost:6379/0
LIMIT_PUBLIC_TEST_USER=

# 翻译配置
ENABLE_WEB_TRANSLATORS=false
TRANSLATOR_SERVICE=alibaba

# TTS配置
TTS_URL=
TTS_TOKEN=

# 版本配置
LATEST_VERSION=
PACKAGE_BASE_URL=
PACKAGE_TYPE=

# 过滤配置
FILTER_WEB_URL=https://raw.githubusercontent.com/VoiceLinkVR/VoiceLinkServer/refs/heads/main/src/filter.json
```

### 启动服务

#### 开发模式
```bash
# 从项目根目录运行
python run.py
```

#### 生产模式
```bash
# 使用uvicorn直接启动
uvicorn src.main:app --host 0.0.0.0 --port 8980 --workers 4
```

#### Docker部署
```bash
# 构建镜像
docker build -t voicelink-fastapi .

# 运行容器
docker run -d -p 8980:8980 --name voicelink-server voicelink-fastapi
```

## API文档

启动服务后，可以通过以下地址访问API文档：

- **Swagger UI**: http://localhost:8980/docs
- **ReDoc**: http://localhost:8980/redoc

## API接口说明

### 认证相关

#### 用户登录
```http
POST /api/login
Content-Type: application/x-www-form-urlencoded

username=admin&password=password
```

**响应示例：**
```json
{
  "message": "Login successful",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

#### 获取当前用户信息
```http
GET /api/user/me
Authorization: Bearer <token>
```

### 语音转录

#### Whisper转录
```http
POST /api/whisper/transcriptions
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <音频文件>
```

### 管理接口

#### 获取用户列表
```http
GET /manageapi/users
Authorization: Bearer <token>
```

#### 创建用户
```http
POST /manageapi/users
Authorization: Bearer <token>
Content-Type: application/json

{
  "username": "newuser",
  "password": "password123",
  "is_admin": false
}
```

#### 更新用户信息
```http
PUT /manageapi/users/{user_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "is_active": true,
  "is_admin": false,
  "limit_rule": "100/hour"
}
```

#### 删除用户
```http
DELETE /manageapi/users/{user_id}
Authorization: Bearer <token>
```

## 管理界面

### 访问地址

- **登录页面**: http://localhost:8980/ui/login
- **用户管理**: http://localhost:8980/ui/manage_users
- **统计页面**: http://localhost:8980/ui/stats

### 界面功能

1. **登录界面**: 管理员用户登录
2. **用户管理**:
   - 查看所有用户
   - 创建新用户
   - 编辑用户信息
   - 启用/禁用用户
   - 设置管理员权限
3. **统计页面**: 系统使用统计

## 数据库模型

### User模型
- `id`: 用户ID
- `username`: 用户名
- `password`: 加密密码
- `is_admin`: 是否管理员
- `limit_rule`: 速率限制规则
- `expiration_date`: 过期时间
- `is_active`: 是否激活

### RequestLog模型
- `id`: 日志ID
- `username`: 用户名
- `ip`: IP地址
- `endpoint`: 请求端点
- `timestamp`: 时间戳
- `duration`: 请求时长
- `status`: 状态

## 速率限制

系统支持基于Redis的速率限制配置，可在环境变量中设置：

```env
LIMIT_ENABLE=true
LIMITER_REDIS_URL=redis://localhost:6379/0
```

默认限制为每小时400次请求，管理员可以在用户管理中为特定用户设置自定义限制规则。

## 安全特性

1. **JWT认证**: 使用HS256算法签名
2. **密码加密**: 使用bcrypt哈希
3. **速率限制**: 防止API滥用
4. **CORS支持**: 可配置跨域访问
5. **输入验证**: Pydantic模型验证

## 开发指南

### 项目结构
```
src/
├── core/           # 核心配置和依赖
├── db/             # 数据库模型
├── routers/        # 路由处理
├── schemas/        # Pydantic模型
├── templates/      # HTML模板
├── static/         # 静态文件
├── main.py         # 应用入口
└── requirements.txt # 依赖包
```

### 添加新API

1. 在 `schemas/` 中创建Pydantic模型
2. 在 `routers/` 中创建路由函数
3. 使用依赖注入获取数据库会话和用户信息
4. 在 `main.py` 中注册路由

### 数据库迁移

系统会自动创建数据库表结构，如需手动操作：
```python
from src.db.base import Base, engine
Base.metadata.create_all(bind=engine)
```

## 故障排除

### 常见问题

1. **端口占用**: 确保8980端口未被占用
2. **Redis连接**: 检查Redis服务是否运行
3. **数据库权限**: 确保数据库文件有写入权限
4. **依赖冲突**: 使用虚拟环境安装依赖

### 日志查看

系统会在 `src/data/logs/` 目录下生成日志文件：
- `server.log`: 主要运行日志
- `debug.log`: 调试信息
- `error.log`: 错误信息

## 性能优化

1. **Worker数量**: 根据CPU核心数调整worker数量
2. **数据库连接池**: 配置SQLAlchemy连接池参数
3. **Redis优化**: 使用Redis集群提高性能
4. **CDN加速**: 静态资源使用CDN

## 版本历史

- v1.0.0: 从Flask迁移到FastAPI
- 新增异步支持、自动文档、依赖注入等特性

## 支持

如有问题，请查看日志文件或提交Issue获取帮助。
