# VoiceLinkVR-server
([简体中文](README-zh.md)|english)

This is a Docker server program that calls faster whisper and libreTranslation

It can be used together with other clients of VoiceLinkVR, such as [VRCLS](https://github.com/VoiceLinkVR/VRCLS), or independently as a server

Integrated a very simple user control interface, the project relies on [faster-whisper-server (now called speaches)](https://github.com/speaches-ai/speaches/tree/v0.6.0-rc.1) and libreTranslate

## Deployment

This program is only recommended to run in Docker compose mode

Please download this repository first

CD to project directory

If the GPU of the machine supports CUDA12.2 or above, please run:
```
docker-compose -f docker-compose-cuda.yml up -d
```
If you only have a CPU, please run:
```
docker-compose -f docker-compose-cpu.yml up -d
```

For users in China, use the `-cn` suffix versions:
```
# GPU version
docker-compose -f docker-compose-cuda-cn.yml up -d
# CPU version
docker-compose -f docker-compose-cpu-cn.yml up -d
# Full GPU version (requires CUDA 12.4.1+)
docker-compose -f docker-compose-cuda-all-cn.yml up -d
```

After everything is ready to run, please visit: `http://{Server IP}:8980/ui/login`

The username and password entered during the first login will be used as the default administrator's account and password. Please keep them safe

If you forget your administrator account and password, please modify the database file yourself. In Docker, this file can be found in `/usr/src/app/data/db/users.db`


## Service Interface Document

### Management Interface
- Management interface entrance: /ui/login
- Management interface homepage: /ui/manage_users
- Exit the management interface: /ui/logout
- Delete user in the management interface: /ui/deleteUser

### Control API
Except for the administrator registration interface, which does not require a token when there are no users after startup, tokens need to be added for all other interfaces

#### Register Administrator Interface

This interface will automatically designate the first user as the administrator when there is no user information, and the token will be verified at other times

Method: POST
URL: /manageapi/registerAdmin
Parameters:
```json
{
    "username":"",
    "password":""
}
```
Response:
```json
{"message": "AdminUser created successfully"}
```

#### Change User Password Interface
Method: POST
URL: /manageapi/changePassword
Parameters:
```json
{
    "username":"",
    "password":""
}
```
Response:
```json
{"message": "user:{username}, Password changed successfully"}
```

#### Register User Interface (Simple)
Method: POST
URL: /manageapi/register
Parameters:
```json
{
    "username":"",
    "password":""
}
```
Response:
```json
{"message": "User created successfully"}
```

#### Add User Interface (Full)
Method: POST
URL: /manageapi/addUser
Parameters:
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
Field Description:
- `username`: Username (required, 3-50 characters)
- `password`: Password (required, minimum 6 characters)
- `is_admin`: Is administrator (optional, default false)
- `is_active`: Is active (optional, default true)
- `limit_rule`: Rate limit rule (optional)
- `expiration_date`: Expiration date (optional, ISO format)

Response:
```json
{"message": "User created successfully", "user": {"username": ""}}
```

#### Update User Interface
Method: POST
URL: /manageapi/updateUser
Parameters:
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
Field Description:
- `username`: Username (required, used to locate the user to update)
- `password`: New password (optional, not modified if not provided)
- `is_admin`: Is administrator (optional, not modified if not provided)
- `is_active`: Is active (optional, not modified if not provided)
- `limit_rule`: Rate limit rule (optional, not modified if not provided)
- `expiration_date`: Expiration date (optional, not modified if not provided)

Response:
```json
{"message": "user:{username}, updated successfully"}
```

#### Delete User Interface
Method: POST
URL: /manageapi/deleteUser
Parameters:
```json
{
    "username":""
}
```
Response:
```json
{"message": "User deleted successfully"}
```

### Call API

#### Login to Obtain Token
Method: POST
URL: /api/login
```json
{
    "username":"",
    "password":""
}
```
Response:
```json
// Success
{"message": "Login successful", "access_token": "", "token_type": "bearer"}
// Failed
{"detail": "Invalid credentials"}
```

#### Get Speech Recognition Results (Chinese)
Method: POST
URL: /api/whisper/transcriptions
Parameters:
```
// form-data format
'file': {binary file}
```

Response:
```json
{"text": ""}
```

#### Text Translation Interface
Method: POST
URL: /api/libreTranslate
Parameters:
```json
{
    "source":"",
    "target":"",
    "text":""
}
```
Response:
```json
{"text": ""}
```

#### Voice Translation Interface (English)
Method: POST
URL: /api/func/translateToEnglish
Parameters:
```
// form-data format
'file': {binary file}
```
Response:
```json
{"text": "","translatedText":""}
```

#### Voice Translation Interface (Other Languages)
Method: POST
URL: /api/func/translateToOtherLanguage
Parameters:
```
// form-data format
files["file"]: {binary file}
data={"targetLanguage":""}
// Please refer to the /language interface of your deployed libreTranslate for the supported target language formats
```
Response:
```json
{"text": "","translatedText":""}
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| WHISPER_HOST | Whisper service address | localhost |
| WHISPER_PORT | Whisper service port | 8000 |
| LIBRETRANSLATE_HOST | LibreTranslate service address | localhost |
| LIBRETRANSLATE_PORT | LibreTranslate service port | 5000 |
| SENSEVOICE_HOST | SenseVoice service address | localhost |
| SENSEVOICE_PORT | SenseVoice service port | 8800 |
| LIMIT_ENABLE | Enable rate limiting | False |
| LIMITER_REDIS_URL | Redis connection URL | - |
| TRANSLATOR_SERVICES_LIST | Translation services list | bing,modernMt,cloudTranslation |
| ENABLE_WEB_TRANSLATORS | Enable online translation services | True |
| JWT_SECRET_KEY | JWT secret key (change in production) | voicelinkvr-secret-key |
| SQL_PATH | Database connection string | sqlite:///data/db/users.db |
