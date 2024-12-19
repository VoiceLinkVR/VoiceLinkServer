from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity,verify_jwt_in_request
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
import requests
import json
import os

# 获取环境参数
whisper_host=os.getenv("WHISPER_HOST")
whisper_prot=os.getenv("WHISPER_PORT")
whisper_apiKey=os.getenv("WHISPER_APIKEY") #当前faster-whisper-server/latest中无效
libreTranslate_host=os.getenv("LIBRETRANSLATE_HOST")
libreTranslate_port=os.getenv("LIBRETRANSLATE_PORT")
libreTranslate_apiKey=os.getenv("LIBRETRANSLATE_APIKEY")
jwt_secret_key=os.getenv("JWT_SECRET_KEY")
jwt_access_token_expires=os.getenv("JWT_ACCESS_TOKEN_EXPIRES")
whisper_model=os.getenv("WHISPER_MODEL")
sqlitePath=os.getenv("SQLITE_PATH")

# whisper config
if whisper_host is not None and whisper_prot is not None:whisper_url=f'http://{whisper_host}:{whisper_prot}/v1/'
else: whisper_url='http://127.0.0.1:8000/v1/'
whisper_apiKey= "something" if  whisper_apiKey is None else whisper_apiKey
model="Systran/faster-whisper-large-v3"if whisper_model is None else whisper_model
whisperclient = OpenAI(api_key=whisper_apiKey, base_url=whisper_url)


# libreTranslate config
supportedLanguagesList=[]
libreTranslate_apiKey= "" if  libreTranslate_apiKey is None else libreTranslate_apiKey
if libreTranslate_host is not None and libreTranslate_host is not None:localTransBaseUrl=f'http://{libreTranslate_host}:{libreTranslate_host}/'
else: localTransBaseUrl="http://127.0.0.1:5000/"
localTransUrl=localTransBaseUrl+"translate"
localLanguageUrl=localTransBaseUrl+"languages"

# 应用和数据库配置
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{sqlitePath if sqlitePath is not None else ''}users.db'  # 使用 SQLite 数据库
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'wVLAF_13N6XL_QmP.DjkKsV' if jwt_secret_key is None else jwt_secret_key  # JWT 秘钥
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = 604800 if jwt_access_token_expires is None else int(jwt_access_token_expires)
db = SQLAlchemy(app)
jwt = JWTManager(app)




#过滤规则检查 filter.json check
with open('filter.json', 'r',encoding='utf-8') as src ,open('data/filterConfig/filter.json', 'r') as dest:
    try:
        srcConfig=json.load(src)
        destConfig=json.load(dest)
    except requests.exceptions.JSONDecodeError as e:
        app.logger.warning("过滤配置文件异常,详情："+str(e.strerror))
        exit(1)
configDiff=list(set(srcConfig.keys())-set(destConfig.keys()))
if configDiff != [] :
    app.logger.info(" filter in filter.json updated ||filter.json文件更新 :"+str(configDiff))
    for newConfig in configDiff:
        destConfig[newConfig]=srcConfig[newConfig]
    with open('data/filterConfig/filter.json', 'w', encoding="utf-8") as file:
        file.write(json.dumps(destConfig,ensure_ascii=False, indent=4))
for filter in srcConfig.keys():
    filterDiff=[rule for rule in srcConfig[filter] if rule not in destConfig[filter]]
    if filterDiff != []:
        app.logger.info("rules in filter.json column updated ||filter.json文件更新 规则更新,增加："+str(filterDiff))
        for newFilter in filterDiff:
            destConfig[filter].append(newFilter)
        with open('data/filterConfig/filter.json', 'w', encoding="utf-8") as file:
            file.write(json.dumps(destConfig,ensure_ascii=False, indent=4))
errorFilter=destConfig

# 用户模型
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)

class AdminUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)
# 创建数据库
@app.before_request
def create_tables():
    db.create_all()

@app.route('/registerAdmin', methods=['POST'])
def registerAdmin():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if AdminUser.query.count() != 0:
        verify_jwt_in_request()
        current_user = get_jwt_identity()
        if not AdminUser.query.filter_by(username=current_user).first():
            return jsonify({'message': 'permission denied'}), 400
        elif not username or not password:
            return jsonify({'message': 'Missing username or password'}), 400
        elif AdminUser.query.filter_by(username=username).first():
                return jsonify({'message': 'Username already exists'}), 400

    hashed_password = generate_password_hash(password)
    new_adminuser = AdminUser(username=username, password=hashed_password)
    db.session.add(new_adminuser)
    if not User.query.filter_by(username=username).first():
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
   
    
    db.session.commit()
    return jsonify({'message': 'AdminUser created successfully'}), 201



@app.route('/changePassword', methods=['POST'])
@jwt_required()
def changePassword():
    current_user = get_jwt_identity()
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if not AdminUser.query.filter_by(username=current_user).first():
        return jsonify({'message': 'permission denied'}), 400
    hashed_password = generate_password_hash(password)
    new_user = User(username=username, password=hashed_password)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'message': f'user:{username}, Password changed successfully'}), 201



# 注册接口
@app.route('/register', methods=['POST'])
@jwt_required()
def register():
    
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    current_user = get_jwt_identity()
    app.logger.info(f"/register {username},damin: {current_user}")
    if not username or not password:
        return jsonify({'message': 'Missing username or password'}), 400
    if not AdminUser.query.filter_by(username=current_user).first():
        return jsonify({'message': 'permission denied'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'message': 'Username already exists'}), 400

    hashed_password = generate_password_hash(password)
    new_user = User(username=username, password=hashed_password)
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'User created successfully'}), 201

# 登录接口
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    app.logger.info(f"/login {username}")
    if not username or not password:
        return jsonify({'message': 'Missing username or password'}), 400

    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password, password):
        access_token = create_access_token(identity=username)
        return jsonify({'message': 'Login successful', 'access_token': access_token}), 200
    else:
        return jsonify({'message': 'Invalid credentials'}), 401

# 语音识别
@app.route('/whisper/transcriptions', methods=['POST'])
@jwt_required()
def whisper_transcriptions():
    current_user = get_jwt_identity()
    app.logger.info(f"/whisper/transcriptions user:{current_user}")
    file=request.files['file']

    audiofile=file.stream.read()
    res=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='zh')
    text=res.text
    if(text in errorFilter["errorResultDict"]) or any(errorKey in text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    return jsonify({'text': text}), 200


def translate_local(text,source,target)-> str: 
    url = localTransUrl
    params = {
		"q": text,
		"source": source,
		"target": target,
		"format": "text",
		"alternatives": 3,
		"api_key": libreTranslate_apiKey
	}
    try:
        response = requests.post(url, params=params)
        result = response.json()
    except Exception as e:
         app.logger.info(f"翻译API响应错误: {str(e)}")
 
    # 添加错误处理和日志记录
    if 'translatedText' in result:
        return result['translatedText']
    else:
        # 打印错误信息和完整的API响应
        app.logger.info(f"翻译API响应错误: {result}")
        return text  # 如果翻译失败，返回原文
# 翻译
@app.route('/libreTranslate', methods=['POST'])
@jwt_required()
def libreTranslate():
    current_user = get_jwt_identity()
    app.logger.info(f"/libreTranslate user:{current_user}")
    data = request.get_json()
    source = data.get('source')
    target = data.get('target')
    text = data.get('text')
    res=translate_local(text,source,target)
    return jsonify({'text': res}), 200

# 翻译
@app.route('/func/translateToEnglish', methods=['POST'])
@jwt_required()
def translate():
    current_user = get_jwt_identity()
    app.logger.info(f"/func/translateToEnglish user:{current_user}")
    file=request.files['file']
    audiofile=file.stream.read()
    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='zh')
    if(text.text in errorFilter["errorResultDict"]) or any(errorKey in text.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
    return jsonify({'text': text.text,'translatedText':translatedText.text}), 200

# 多语言翻译
@app.route('/func/translateToOtherLanguage', methods=['POST'])
@jwt_required()
def translateDouble():
    current_user = get_jwt_identity()
    app.logger.info(f"/func/translateToOtherLanguage user:{current_user}")
    global supportedLanguagesList
    if supportedLanguagesList == []:
        res=requests.get(localLanguageUrl)
        datas=res.json()
        for data in datas:
            if data["code"]=='en':
                supportedLanguagesList=data["targets"]
                break
    file=request.files['file']
    params=request.form.to_dict()
    targetLanguage=params["targetLanguage"]
    app.logger.info(f"targetLanguage:{targetLanguage}")
    if targetLanguage not in supportedLanguagesList:
        return jsonify({'message':f"targetLanguage error,please use following languages:{str(supportedLanguagesList)}"}), 401
    audiofile=file.stream.read()
    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='zh')
    if(text.text in errorFilter["errorResultDict"]) or any(errorKey in text.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
    if targetLanguage =='en':transText=translatedText.text
    else:transText=translate_local(translatedText.text,"en",targetLanguage)
    return jsonify({'text': text.text,'translatedText':transText}), 200


if __name__ == '__main__':
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
    db = SQLAlchemy(app)
    app.run(debug=True,host='0.0.0.0',port=8980)

