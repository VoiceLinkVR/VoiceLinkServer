# VoiceLinkVR-server
(简体中文|[english](README.md))

This is a Docker server program that calls faster whisper and libreTranslation

It can be used together with other clients of VoiceLinkVR or independently as a server

Integrated a very simple user control interface, the project relies on faster whisper server and libreTranslation

##Deployment method

This program is only recommended to run in Docker compose mode

Please download this repository first

CD to project directory

If the GPU of the machine supports CUDA12.2 or above, please run:
```
docker-compose -f docker-compose-cuda.yml up -d
```
If you only have a CPU or above, please run:
```
docker-compose -f docker-compose-cpu.yml up -d
```
After everything is ready to run, please visit:` http:// {Server IP}: 8980/ui/login`

The username and password entered during the first login will be used as the default administrator's account and password. Please keep them safe

If you forget your administrator account and password, please modify the database file yourself. In Docker, this file can be found in `/usr/src/app/data/db/users.db '`


##Service Interface Document

###Management interface
-Management interface entrance:/ ui/login
-Management interface homepage:/ ui/manage_users
-Exit the management interface/ui/logout
-Delete user/ui/deleteUser in the management interface

###Control API
Except for the administrator registration interface, which does not require a token when there are no users after startup, tokens need to be added for all other interfaces
####Registration administrator interface

This interface will automatically designate the first user as the administrator when there is no user information, and the token will be verified at other times

method: POST
url: /manageapi/registerAdmin  
Pass in parameters:
```json
{
"username":"",
"password":""
}
```
Response format:
```json
{"message": "User created successfully"}
```

####Change user password interface
method: POST
url:/manageapi/changePassword
Pass in parameters:
```json
{
"username":"",
"password":""
}
```
Response format:
```json
{"message": "User created successfully"}
```
####Register User Interface
method: POST
url:/manageapi/register
Pass in parameters:
```json
{
"username":"",
"password":""
}
```
Response format:
```json
{"message": "User created successfully"}
```
####Delete user interface
method: POST
url:/manageapi/deleteUser
Pass in parameters:
```json
{
"username":"",
"password":""
}
```
Response format:
```json
{"message": "User created successfully"}
```
###Call API
####Login to obtain token
url:/api/login
```json
{
"username":"",
"password":""
}
```
Response format:
```json
//Success
{"message": "Login successful", "access_token": ""}
//Failed
{"message": "Invalid credentials"}

```
####Obtain speech recognition results (in Chinese)
method: POST
url:/api/whisper/transcriptions
Pass in parameters:
```
//From data format
'file': {binary file}
```

Response format:
```json
{"text": ""}
```
####Text translation interface
method: POST
url:/api/libreTranslate
Pass in parameters:
```json
{
"source":"",
"target":"",
"text":""

}
```
Response format:
```json
{"text": ""}
```
####Voice translation interface (English)
method: POST
url:/api/func/translateToEnglish
Pass in parameters:
```
//From data format
'file': {binary file}
```
Response format:
```json
{"text": "","translatedText":""}
```
####Register User Interface
method: POST
url:/api/func/translateToOtherLanguage
Pass in parameters:
```
//From data format
files["file"]: {Binary File}
data={"targetLanguage":""}
//Please refer to the/language interface of your deployed libreTranslation for the supported target language formats
```
Response format:
```json
{"text": "","translatedText":""}
```

# VoiceLinkVR-server
这是一个调用faster-whisper和libreTranslate的docker服务端程序

既可以配合VoiceLinkVR的其他客户端一起使用，也可以独立作为服务端使用

集成了一个非常简单的用户控制界面，项目依赖于faster-whisper-server 与libreTranslate

## 部署方式

本程序只推荐使用docker compose 方式运行

请先下载本仓库

cd至项目目录中

如果机器的GPU支持cuda12.2以上请运行：
```
docker-compose -f docker-compose-cuda.yml up -d
```
如果仅拥有cpu以上请运行：
```
docker-compose -f docker-compose-cpu.yml up -d
```
等待一切运行就绪后请访问：`http://{服务器ip}:8980/ui/login` 

首次登陆时输入的用户名和密码将作为默认管理员的账户和密码，请妥善保管

如果忘记管理员账户和密码请自行修改数据库文件，在docker中该文件位于`/usr/src/app/data/db/users.db`


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
{"message": "User created successfully"}
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
{"message": "User created successfully"}
```
#### 注册用户接口
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
#### 删除用户接口
方法：POST
url:/manageapi/deleteUser
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
### 调用api
#### 登陆获取token 
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
{"message": "Login successful", "access_token": ""}
//失败
{"message": "Invalid credentials"}

```
#### 获取语音识别结果(中文)
方法：POST
url:/api/whisper/transcriptions
传入参数：
```
//from-data格式
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
//from-data格式
'file':{文件二进制}
```
响应格式：
```json
{"text": "","translatedText":""}
```
#### 注册用户接口
方法：POST
url:/api/func/translateToOtherLanguage
传入参数：
```
//from-data格式
files["file"]:{文件二进制}
data={"targetLanguage":""}
//支持的目标语言格式请查看自己部署libreTranslate的/language 接口
```
响应格式：
```json
{"text": "","translatedText":""}
```