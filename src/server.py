from flask import Flask,render_template, request, redirect, url_for, flash, session, abort, jsonify
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
flask_secret_key=os.getenv("FLASK_SECRET_KEY")
jwt_access_token_expires=os.getenv("JWT_ACCESS_TOKEN_EXPIRES")
whisper_model=os.getenv("WHISPER_MODEL")
sqlitePath=os.getenv("SQLITE_PATH")
filter_web_url=os.getenv("FILTER_WEB_URL")

# whisper config
if whisper_host is not None and whisper_prot is not None:whisper_url=f'http://{whisper_host}:{whisper_prot}/v1/'
else: whisper_url='http://127.0.0.1:8000/v1/'
whisper_apiKey= "something" if  whisper_apiKey is None else whisper_apiKey
model="Systran/faster-whisper-large-v3"if whisper_model is None else whisper_model
whisperclient = OpenAI(api_key=whisper_apiKey, base_url=whisper_url)
whisperSupportedLanguageList=["af","am","ar","as","az","ba","be","bg","bn","bo","br","bs","ca","cs","cy","da","de","el","en","es"
                              ,"et","eu","fa","fi","fo","fr","gl","gu","ha","haw","he","hi","hr","ht","hu","hy","id","is","it",
                              "ja","jw","ka","kk","km","kn","ko","la","lb","ln","lo","lt","lv","mg","mi","mk","ml","mn","mr","ms",
                              "mt","my","ne","nl","nn","no","oc","pa","pl","ps","pt","ro","ru","sa","sd","si","sk","sl","sn","so","sq",
                              "sr", "su", "sv","sw","ta", "te","tg","th","tk","tl","tr","tt","uk","ur","uz","vi","yi","yo","yue","zh"]

# libreTranslate config
supportedLanguagesList=[]
libreTranslate_apiKey= "" if  libreTranslate_apiKey is None else libreTranslate_apiKey
if libreTranslate_host is not None and libreTranslate_host is not None:localTransBaseUrl=f'http://{libreTranslate_host}:{libreTranslate_port}/'
else: localTransBaseUrl="http://127.0.0.1:5000/"
localTransUrl=localTransBaseUrl+"translate"
localLanguageUrl=localTransBaseUrl+"languages"

# 应用和数据库配置
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{sqlitePath if sqlitePath is not None else ''}users.db'  # 使用 SQLite 数据库
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'wVLAF_13N6XL_QmP.DjkKsV' if jwt_secret_key is None else jwt_secret_key  # JWT 秘钥
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = 604800 if jwt_access_token_expires is None else int(jwt_access_token_expires)
app.config['SECRET_KEY'] = 'wVddLAF_13dsdddN6XL_QmP.DjkKsV' if flask_secret_key is None else flask_secret_key
db = SQLAlchemy(app)
jwt = JWTManager(app)

with open('filter.json', 'r',encoding='utf-8') as src:
    try:
        srcConfig1=json.load(src)  
    except requests.exceptions.JSONDecodeError as e:
        app.logger.warning("local filter.json error||源过滤配置文件异常,详情："+str(e.strerror))
        exit(1)

try:
    responce= requests.get("https://raw.githubusercontent.com/VoiceLinkVR/VoiceLinkServer/refs/heads/main/src/filter.json" if filter_web_url is None else filter_web_url) 
    srcConfig2= responce.json()
except Exception as e:
    app.logger.warning("failed to update filter.json through web || 通过网络获取源过滤配置文件失败,详情："+str(e.strerror))


for srcConfig in [srcConfig1,srcConfig2]:
    #过滤规则检查 filter.json check
    with open('data/filterConfig/filter.json', 'r',encoding='utf-8') as dest:
        try:
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
    is_admin = db.Column(db.Boolean, default=False)

# 创建数据库
@app.before_request
def create_tables():
    db.create_all()
# 登录表单处理
@app.route('/ui/login', methods=['GET', 'POST'])
def login_ui():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(is_admin=True).count() == 0:
            hashed_password = generate_password_hash(password)
            new_user = User(username=username, password=hashed_password, is_admin=True)
            db.session.add(new_user)
            db.session.commit()
            session['user_id'] = new_user.id
            return redirect(url_for('manage_users_ui'))
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password) and user.is_admin:
            session['user_id'] = user.id
            return redirect(url_for('manage_users_ui'))
        
        else:
            flash('Invalid username or password')
    return render_template('login.html')
 
# 用户管理页面
@app.route('/ui/manage_users', methods=['GET', 'POST'])
def manage_users_ui():
    if 'user_id' not in session:
        return redirect(url_for('logout_ui'))
 
    if request.method == 'POST':
        new_username = request.form['new_username']
        new_password = request.form['new_password']
        new_is_admin = request.form.get('new_is_admin', 'false') == 'true'
 
        hashed_password = generate_password_hash(new_password)
        new_user = User(username=new_username, password=hashed_password, is_admin=new_is_admin)
        db.session.add(new_user)
        db.session.commit()
        flash('User added successfully')
 
    users = User.query.all()
    return render_template('manage_users.html', users=users)

# 注销
@app.route('/ui/logout')
def logout_ui():
    session.pop('user_id', None)
    return redirect(url_for('login_ui'))
@app.route('/ui/deleteUser', methods=['POST'])
def delete_user_ui():
    if 'user_id' not in session:
        return redirect(url_for('logout_ui'))
    app.logger.info(request.form['id'])
    db.session.delete(User.query.filter_by(id= int(request.form['id'])).first())
    db.session.commit()
    return redirect(url_for('manage_users_ui'))

@app.route('/manageapi/registerAdmin', methods=['POST'])
def registerAdmin():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    adminTag=False
    adminNum=User.query.filter_by(is_admin=True).count()
    if adminNum != 0:
        verify_jwt_in_request()
        current_user = get_jwt_identity()
        if not User.query.filter_by(username=current_user,is_admin=True).first():
            return jsonify({'message': 'permission denied'}), 400
        elif not username or not password:
            return jsonify({'message': 'Missing username or password'}), 400
        elif User.query.filter_by(username=username,is_admin=True).first():
                return jsonify({'message': 'Username already exists'}), 400
    else: adminTag=True
    hashed_password = generate_password_hash(password)
    new_adminuser = User(username=username, password=hashed_password,is_admin=adminTag)
    db.session.add(new_adminuser)
    db.session.commit()
    return jsonify({'message': 'AdminUser created successfully'}), 201



@app.route('/manageapi/changePassword', methods=['POST'])
@jwt_required()
def changePassword():
    current_user = get_jwt_identity()
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if not User.query.filter_by(username=current_user,is_admin=True).first():
        return jsonify({'message': 'permission denied'}), 400
    hashed_password = generate_password_hash(password)
    new_user = User(username=username, password=hashed_password)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'message': f'user:{username}, Password changed successfully'}), 201



# 注册接口
@app.route('/manageapi/register', methods=['POST'])
@jwt_required()
def register():
    
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    current_user = get_jwt_identity()
    app.logger.info(f"/register {username},damin: {current_user}")
    if not username or not password:
        return jsonify({'message': 'Missing username or password'}), 400
    if not User.query.filter_by(username=current_user,is_admin=True).first():
        return jsonify({'message': 'permission denied'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'message': 'Username already exists'}), 400

    hashed_password = generate_password_hash(password)
    new_user = User(username=username, password=hashed_password)
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'User created successfully'}), 201

# 删除接口
@app.route('/manageapi/deleteUser', methods=['POST'])
@jwt_required()
def deleteUser():
    
    data = request.get_json()
    username = data.get('username')
    current_user = get_jwt_identity()
    app.logger.info(f"/register {username},damin: {current_user}")
    if not username :
        return jsonify({'message': 'Missing username or password'}), 400

    if not User.query.filter_by(username=username).first():
        return jsonify({'message': 'Username not exist'}), 400
    
    User.query.filter_by(username=username).delete()

    return jsonify({'message': 'User deleted successfully'}), 201


# 登录接口
@app.route('/api/login', methods=['POST'])
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
@app.route('/api/whisper/transcriptions', methods=['POST'])
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

def init_supportedLanguagesList():
    global supportedLanguagesList
    if supportedLanguagesList == []:
        res=requests.get(localLanguageUrl)
        datas=res.json()
        for data in datas:
            if data["code"]=='en':
                supportedLanguagesList=data["targets"]
                break


# 语音识别
@app.route('/api/whisper/translations', methods=['POST'])
@jwt_required()
def whisper_transcriptions():
    current_user = get_jwt_identity()
    app.logger.info(f"/whisper/translations user:{current_user}")
    file=request.files['file']

    audiofile=file.stream.read()
    res=whisperclient.audio.translations.create(model=model, file=audiofile,language='zh')
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
@app.route('/api/libreTranslate', methods=['POST'])
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
@app.route('/api/func/translateToEnglish', methods=['POST'])
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
@app.route('/api/func/translateToOtherLanguage', methods=['POST'])
@jwt_required()
def translateDouble():
    current_user = get_jwt_identity()
    app.logger.info(f"/func/translateToOtherLanguage user:{current_user}")
    global supportedLanguagesList
    init_supportedLanguagesList()
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

# 多语言翻译
@app.route('/api/func/multitranslateToOtherLanguage', methods=['POST'])
@jwt_required()
def translateDouble():
    current_user = get_jwt_identity()
    app.logger.info(f"/func/multitranslateToOtherLanguage user:{current_user}")
    global supportedLanguagesList
    init_supportedLanguagesList()
    file=request.files['file']
    params=request.form.to_dict()
    targetLanguage=params["targetLanguage"]
    sourceLanguage=params["sourceLanguage"]
    app.logger.info(f"targetLanguage:{targetLanguage}, sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
         return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    if targetLanguage not in supportedLanguagesList:
        return jsonify({'message':f"targetLanguage error,please use following languages:{str(supportedLanguagesList)}"}), 401
    audiofile=file.stream.read()
    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=sourceLanguage)
    if(text.text in errorFilter["errorResultDict"]) or any(errorKey in text.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
    if targetLanguage =='en':transText=translatedText.text
    else:transText=translate_local(translatedText.text,"en",targetLanguage)
    return jsonify({'text': text.text,'translatedText':transText}), 200


# 多语言翻译
@app.route('/api/whisper/multitranscription', methods=['POST'])
@jwt_required()
def translateDouble():
    current_user = get_jwt_identity()
    app.logger.info(f"/api/whisper/multitranscription user:{current_user}")
    global supportedLanguagesList
    init_supportedLanguagesList()
    file=request.files['file']
    params=request.form.to_dict()
    sourceLanguage=params["sourceLanguage"]
    app.logger.info(f"sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
        return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    audiofile=file.stream.read()
    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='sourceLanguage')
    if(text.text in errorFilter["errorResultDict"]) or any(errorKey in text.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    return jsonify({'text': text.text}), 200



if __name__ == '__main__':
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
    app.run(debug=True,host='0.0.0.0',port=8980)

