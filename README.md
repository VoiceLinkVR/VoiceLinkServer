# VoiceLinkVR-server
([简体中文](README-zh.md)|english)

This is a Docker server program that calls faster whisper and libreTranslation

It can be used together with other clients of VoiceLinkVR, such as [VRCLS](https://github.com/VoiceLinkVR/VRCLS), or independently as a server

Integrated a very simple user control interface, the project relies on faster whisper server and libreTranslation

## Deployment method

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
After everything is ready to run, please visit:` http:// {Server IP}: 8980/ui/login`

The username and password entered during the first login will be used as the default administrator's account and password. Please keep them safe

If you forget your administrator account and password, please modify the database file yourself. In Docker, this file can be found in `/usr/src/app/data/db/users.db '`


## Service Interface Document

### Management interface
-Management interface entrance:/ ui/login
-Management interface homepage:/ ui/manage_users
-Exit the management interface/ui/logout
-Delete user/ui/deleteUser in the management interface

### Control API
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

#### Change user password interface
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
#### Register User Interface
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
#### Delete user interface
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
### Call API
#### Login to obtain token
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
#### Obtain speech recognition results (in Chinese)
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
#### Voice translation interface (English)
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
#### Register User Interface
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
