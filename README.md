# VoiceLinkVR-server
这是一个调用faster-whisper和libreTranslate的docker服务端程序

既可以配合VoiceLinkVR的其他客户端一起使用，也可以独立作为服务端使用

集成了一个非常简单的用户控制界面

## 部署方式

本程序推荐使用docker方式运行

请先下载本仓库

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