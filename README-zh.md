# VoiceLinkVR-server
(简体中文|[english](README.md))

这是一个调用faster-whisper和libreTranslate的docker服务端程序

既可以配合VoiceLinkVR的其他客户端如VRCHAT-OSC客户端[VRCLS](https://github.com/VoiceLinkVR/VRCLS)一起使用，也可以独立作为服务端使用

集成了一个非常简单的用户控制界面，项目依赖于[faster-whisper-server（现在叫speeches）](https://github.com/speaches-ai/speaches/tree/v0.6.0-rc.1) 与libreTranslate

## 部署方式

本程序只推荐使用docker compose 方式运行

请先下载本仓库

cd至项目目录中

如果机器的GPU支持cuda12.2以上请运行(国内的朋友)：
```
docker-compose -f docker-compose-cuda-cn.yml up -d
```
如果机器的GPU支持cuda12.4.1以上且性能足够(双cuda)请运行(国内的朋友)：
```
docker-compose -f docker-compose-cuda-all-cn.yml up -d
```
如果仅拥有cpu以上请运行(国内的朋友)：
```
docker-compose -f docker-compose-cpu-cn.yml up -d
```

海外用户：
```
# GPU版本
docker-compose -f docker-compose-cuda.yml up -d
# CPU版本
docker-compose -f docker-compose-cpu.yml up -d
```

等待一切运行就绪后请访问：`http://{服务器ip}:8980/ui/login`

首次登陆时输入的用户名和密码将作为默认管理员的账户和密码，请妥善保管

如果忘记管理员账户和密码请自行修改数据库文件，在docker中该文件位于`/usr/src/app/data/db/users.db`

程序保留了原有两个包的后端，分别在8000端口与5000端口


## 服务接口文档

### 管理界面
- 管理界面入口：/ui/login
- 管理界面主页：/ui/manage_users
- 管理界面退出 /ui/logout
- 管理界面删除用户 /ui/deleteUser

### 控制api
除管理员注册接口在启动后无用户时不需要token外，都需要增加token

#### 注册管理员接口

该接口在无用户信息时会自动将第一个用户作为管理员，其他时间将验证token

方法：POST
url: /manageapi/registerAdmin
传入参数：
```json
{
    "username":"",
    "password":""
}
```
响应格式：
```json
{"message": "AdminUser created successfully"}
```

#### 更换用户密码接口
方法：POST
url:/manageapi/changePassword
传入参数：
```json
{
    "username":"",
    "password":""
}
```
响应格式：
```json
{"message": "user:{username}, Password changed successfully"}
```

#### 注册用户接口（简单版）
方法：POST
url:/manageapi/register
传入参数：
```json
{
    "username":"",
    "password":""
}
```
响应格式：
```json
{"message": "User created successfully"}
```

#### 添加用户接口（完整版）
方法：POST
url:/manageapi/addUser
传入参数：
```json
{
    "username": "",
    "password": "",
    "is_admin": false,
    "is_active": true,
    "limit_rule": "10000/day;1000/hour",
    "expiration_date": "2025-12-31T23:59:59"
}
```
字段说明：
- `username`: 用户名（必填，3-50字符）
- `password`: 密码（必填，最少6字符）
- `is_admin`: 是否为管理员（可选，默认false）
- `is_active`: 是否激活（可选，默认true）
- `limit_rule`: 限速规则（可选）
- `expiration_date`: 过期时间（可选，ISO格式）

响应格式：
```json
{"message": "User created successfully", "user": {"username": ""}}
```

#### 更新用户接口
方法：POST
url:/manageapi/updateUser
传入参数：
```json
{
    "username": "",
    "password": "",
    "is_admin": false,
    "is_active": true,
    "limit_rule": "10000/day;1000/hour",
    "expiration_date": "2025-12-31T23:59:59"
}
```
字段说明：
- `username`: 用户名（必填，用于定位要更新的用户）
- `password`: 新密码（可选，不传则不修改）
- `is_admin`: 是否为管理员（可选，不传则不修改）
- `is_active`: 是否激活（可选，不传则不修改）
- `limit_rule`: 限速规则（可选，不传则不修改）
- `expiration_date`: 过期时间（可选，不传则不修改）

响应格式：
```json
{"message": "user:{username}, updated successfully"}
```

#### 删除用户接口
方法：POST
url:/manageapi/deleteUser
传入参数：
```json
{
    "username":""
}
```
响应格式：
```json
{"message": "User deleted successfully"}
```

### 调用api

#### 登陆获取token
方法：POST
url:/api/login
```json
{
    "username":"",
    "password":""
}
```
响应格式：
```json
//成功
{"message": "Login successful", "access_token": "", "token_type": "bearer"}
//失败
{"detail": "Invalid credentials"}
```

#### 获取语音识别结果(中文)
方法：POST
url:/api/whisper/transcriptions
传入参数：
```
//form-data格式
'file':{文件二进制}
```

响应格式：
```json
{"text": ""}
```

#### 文字翻译接口
方法：POST
url:/api/libreTranslate
传入参数：
```json
{
    "source":"",
    "target":"",
    "text":""
}
```
响应格式：
```json
{"text": ""}
```

#### 语音翻译接口(英语)
方法：POST
url:/api/func/translateToEnglish
传入参数：
```
//form-data格式
'file':{文件二进制}
```
响应格式：
```json
{"text": "","translatedText":""}
```

#### 语音翻译接口(其他语言)
方法：POST
url:/api/func/translateToOtherLanguage
传入参数：
```
//form-data格式
files["file"]:{文件二进制}
data={"targetLanguage":""}
//支持的目标语言格式请查看自己部署libreTranslate的/language 接口
```
响应格式：
```json
{"text": "","translatedText":""}
```

## 环境变量说明

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| WHISPER_HOST | Whisper服务地址 | localhost |
| WHISPER_PORT | Whisper服务端口 | 8000 |
| LIBRETRANSLATE_HOST | LibreTranslate服务地址 | localhost |
| LIBRETRANSLATE_PORT | LibreTranslate服务端口 | 5000 |
| SENSEVOICE_HOST | SenseVoice服务地址 | localhost |
| SENSEVOICE_PORT | SenseVoice服务端口 | 8800 |
| LIMIT_ENABLE | 是否启用限速 | False |
| LIMITER_REDIS_URL | Redis连接URL | - |
| TRANSLATOR_SERVICES_LIST | 翻译服务列表 | bing,modernMt,cloudTranslation |
| UPDATE_PUBLIC_BASE_URL | 更新资源对外访问基础URL（可选） | - |
| UPDATE_STATIC_ROOT | 热更新静态资源根目录 | src/data/update/files |
| UPDATE_MANIFEST_PATH | 应用更新manifest路径 | src/data/update/update_manifest.json |
| MODEL_MANIFEST_PATH | 模型目录manifest路径 | src/data/update/models_manifest.json |
| TRANSLATION_PROFILE_MANIFEST_PATH | 翻译能力profile manifest路径 | src/data/update/translation_profile_manifest.json |
| TRANSLATOR_RUNTIME_MANIFEST_PATH | 翻译运行时manifest路径 | src/data/update/translator_runtime_manifest.json |
| TRANSLATION_CAPABILITIES_CACHE_SECONDS | 翻译能力缓存秒数 | 1800 |
| TRANSLATION_CAPABILITY_TIMEOUT | 单引擎语言探测超时（秒） | 4.0 |
| ENABLE_WEB_TRANSLATORS | 启用在线翻译服务 | True |
| JWT_SECRET_KEY | JWT密钥（生产环境请修改） | voicelinkvr-secret-key |
| SQL_PATH | 数据库连接字符串 | sqlite:///data/db/users.db |
