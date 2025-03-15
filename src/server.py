import functools
from flask import Flask, make_response,render_template, request, redirect, url_for, flash, session, abort, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity,verify_jwt_in_request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import QueuePool, case
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
import requests
import json
import os
import uuid
import time
from datetime import datetime
import baidu_translate as fanyi
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
from io import BytesIO
import wave
whisper_to_baidu = {
    'af': 'afr',       # 阿非利堪斯语
    'am': 'amh',       # 阿姆哈拉语
    'ar': 'ara',       # 阿拉伯语
    'as': 'asm',       # 阿萨姆语
    'az': 'aze',       # 阿塞拜疆语
    'ba': 'bak',       # 巴什基尔语
    'be': 'bel',       # 白俄罗斯语
    'bg': 'bul',       # 保加利亚语
    'bn': 'ben',       # 孟加拉语
    'bo': None,       # 藏语（百度 API 中没有对应的代码）
    'br': 'bre',       # 布列塔尼语
    'bs': 'bos',       # 波斯尼亚语
    'ca': 'cat',       # 加泰罗尼亚语
    'cs': 'cs',        # 捷克语
    'cy': 'gle',       # 威尔士语
    'da': 'dan',       # 丹麦语
    'de': 'de',        # 德语
    'el': 'el',        # 希腊语
    'en': 'en',        # 英语
    'es': 'spa',       # 西班牙语
    'et': 'est',       # 爱沙尼亚语
    'eu': 'baq',       # 巴斯克语
    'fa': 'per',       # 波斯语
    'fi': 'fin',       # 芬兰语
    'fo': 'fao',       # 法罗语
    'fr': 'fra',       # 法语
    'gl': 'glg',       # 加利西亚语
    'gu': 'guj',       # 古吉拉特语
    'ha': 'hau',       # 豪萨语
    'haw': 'haw',      # 夏威夷语
    'he': 'heb',       # 希伯来语
    'hi': 'hi',        # 印地语
    'hr': 'hrv',       # 克罗地亚语
    'ht': 'hat',       # 海地克里奥尔语
    'hu': 'hu',        # 匈牙利语
    'hy': 'arm',       # 亚美尼亚语
    'id': 'id',        # 印尼语
    'is': 'ice',       # 冰岛语
    'it': 'it',        # 意大利语
    'ja': 'jp',        # 日语
    'jw': 'jav',       # 爪哇语
    'ka': 'geo',       # 格鲁吉亚语
    'kk': None,       # 哈萨克语（百度 API 中没有对应的代码）
    'km': 'hkm',       # 高棉语
    'kn': 'kan',       # 卡纳达语
    'ko': 'kor',       # 韩语
    'la': 'lat',       # 拉丁语
    'lb': 'ltz',       # 卢森堡语
    'ln': 'lin',       # 林加拉语
    'lo': 'lao',       # 老挝语
    'lt': 'lit',       # 立陶宛语
    'lv': 'lav',       # 拉脱维亚语
    'mg': 'mg',        # 马达加斯加语
    'mi': None,       # 毛利语（百度 API 中没有对应的代码）
    'mk': 'mac',       # 马其顿语
    'ml': 'mal',       # 马拉雅拉姆语
    'mn': None,       # 蒙古语（百度 API 中没有对应的代码）
    'mr': 'mar',       # 马拉地语
    'ms': 'may',       # 马来语
    'mt': 'mlt',       # 马耳他语
    'my': 'bur',       # 缅甸语
    'ne': 'nep',       # 尼泊尔语
    'nl': 'nl',        # 荷兰语
    'nn': 'nno',       # 新挪威语
    'no': 'nor',       # 挪威语
    'oc': 'oci',       # 奥克语
    'pa': 'pan',       # 旁遮普语
    'pl': 'pl',        # 波兰语
    'ps': 'pus',       # 普什图语
    'pt': 'pt',        # 葡萄牙语
    'ro': 'rom',       # 罗马尼亚语
    'ru': 'ru',        # 俄语
    'sa': 'san',       # 梵语
    'sd': 'snd',       # 信德语
    'si': 'sin',       # 僧伽罗语
    'sk': 'sk',        # 斯洛伐克语
    'sl': 'slo',       # 斯洛文尼亚语
    'sn': 'sna',       # 修纳语
    'so': 'som',       # 索马里语
    'sq': 'alb',       # 阿尔巴尼亚语
    'sr': 'srp',       # 塞尔维亚语
    'su': 'sun',       # 巽他语
    'sv': 'swe',       # 瑞典语
    'sw': 'swa',       # 斯瓦希里语
    'ta': 'tam',       # 泰米尔语
    'te': 'tel',       # 泰卢固语
    'tg': 'tgk',       # 塔吉克语
    'th': 'th',        # 泰语
    'tk': 'tuk',       # 土库曼语
    'tl': 'tgl',       # 他加禄语
    'tr': 'tr',        # 土耳其语
    'tt': 'tat',       # 鞑靼语
    'uk': 'ukr',       # 乌克兰语
    'ur': 'urd',       # 乌尔都语
    'uz': None,       # 乌兹别克语（百度 API 中没有对应的代码）
    'vi': 'vie',       # 越南语
    'yi': 'yid',       # 意第绪语
    'yo': 'yor',       # 约鲁巴语
    'yue': 'yue',     # 粤语
    'zh': 'zh',        # 简体中文
    'zt': 'cht'        # 繁体中文
}
libretranslate_to_baidu = {
    'ar': 'ara',       # 阿拉伯语
    'az': 'aze',       # 阿塞拜疆语
    'bg': 'bul',       # 保加利亚语
    'bn': 'ben',       # 孟加拉语
    'ca': 'cat',       # 加泰罗尼亚语
    'cs': 'cs',        # 捷克语
    'da': 'dan',       # 丹麦语
    'de': 'de',        # 德语
    'el': 'el',        # 希腊语
    'en': 'en',        # 英语
    'eo': None,       # 世界语（百度 API 中没有对应的代码）
    'es': 'spa',       # 西班牙语
    'et': 'est',       # 爱沙尼亚语
    'eu': 'baq',       # 巴斯克语
    'fa': 'per',       # 波斯语
    'fi': 'fin',       # 芬兰语
    'fr': 'fra',       # 法语
    'ga': 'gle',       # 爱尔兰语
    'gl': 'glg',       # 加利西亚语
    'he': 'heb',       # 希伯来语
    'hi': 'hi',        # 印地语
    'hu': 'hu',        # 匈牙利语
    'id': 'id',        # 印尼语
    'it': 'it',        # 意大利语
    'ja': 'jp',        # 日语
    'ko': 'kor',       # 韩语
    'lt': 'lit',       # 立陶宛语
    'lv': 'lav',       # 拉脱维亚语
    'ms': 'may',       # 马来语
    'nb': 'nob',       # 挪威语（书面挪威语）
    'nl': 'nl',        # 荷兰语
    'pl': 'pl',        # 波兰语
    'pt': 'pt',        # 葡萄牙语
    'ro': 'rom',       # 罗马尼亚语
    'ru': 'ru',        # 俄语
    'sk': 'sk',        # 斯洛伐克语
    'sl': 'slo',       # 斯洛文尼亚语
    'sq': 'alb',       # 阿尔巴尼亚语
    'sr': 'srp',       # 塞尔维亚语
    'sv': 'swe',       # 瑞典语
    'th': 'th',        # 泰语
    'tl': 'tgl',       # 塔加洛语
    'tr': 'tr',        # 土耳其语
    'uk': 'ukr',       # 乌克兰语
    'ur': 'urd',       # 乌尔都语
    'vi': 'vie',       # 越南语
    'zh': 'zh',        # 中文
    'zt': 'cht'        # 繁体中文
}

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
sqlitePath=os.getenv("SQL_PATH")
filter_web_url=os.getenv("FILTER_WEB_URL")
limit_enable=os.getenv("LIMIT_ENABLE")
pubilc_test_username=os.getenv("LIMIT_PUBLIC_TEST_USER")
sqlalchemy_pool_size=os.getenv("SQLALCHEMY_POOL_SIZE")
sqlalchemy_max_overflow=os.getenv("SQLALCHEMY_MAX_OVERFLOW")
enable_baiduapi=os.getenv("ENABLE_BAIDU_API")

limit_enable= False if  limit_enable is None or limit_enable =="" else True
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
app.config['SQLALCHEMY_DATABASE_URI'] = sqlitePath if sqlitePath is not None else 'sqlite:///users.db'  # 使用 SQLite 数据库
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# app.config['SQLALCHEMY_POOL_SIZE'] = int(sqlalchemy_pool_size) if sqlalchemy_pool_size else 20  # 设置连接池大小
# app.config['SQLALCHEMY_MAX_OVERFLOW'] = int(sqlalchemy_max_overflow) if sqlalchemy_max_overflow else 40  # 设置最大溢出连接数
# app.config['SQLALCHEMY_POOL_SIZE'] = 30  # 设置连接池大小
# app.config['SQLALCHEMY_MAX_OVERFLOW'] = 60  # 设置最大溢出连接数
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'poolclass': QueuePool,  # 强制使用 QueuePool
    'pool_size': 0,
    'max_overflow': -1
}
app.config['JWT_SECRET_KEY'] = 'wVLAF_13N6XL_QmP.DjkKsV' if jwt_secret_key is None else jwt_secret_key  # JWT 秘钥
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = 604800 if jwt_access_token_expires is None else int(jwt_access_token_expires)
app.config['SECRET_KEY'] = 'wVddLAF_13dsdddN6XL_QmP.DjkKsV' if flask_secret_key is None else flask_secret_key
app.config['SQLALCHEMY_POOL_RECYCLE'] = 3600  # 单位：秒（1小时）

if limit_enable:
    from flask_limiter.errors import RateLimitExceeded
    SHARED_LIMIT_SCOPE = "shared_limit_scope"
    def limit_key_func():
        XRealIp = request.headers.get('X-Real-Ip')
        x_forwarded_for = request.headers.get('X-Forwarded-For')
        if XRealIp:
            ip=XRealIp.split(',')[0].strip()
        elif x_forwarded_for:
            ip=x_forwarded_for.split(',')[0].strip()
        else:
            ip=request.remote_addr
        app.logger.info(ip)
        return ip
    limiter=Limiter(app=app,key_func=limit_key_func,default_limits=["400/hour"],storage_uri="memory://")
    # 自定义错误处理器
    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit_exceeded(e):
        return jsonify({
        "error": "Too many request",
        "limit": str(e.limit.limit),  # 如 "2 per 1 day"
    }), 430 # 返回 403 状态码
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
    srcConfig2=srcConfig1


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
from sqlalchemy import text
def check_and_update_db():
    with app.app_context():
        try:
            # 检查字段是否存在
            db.session.execute(text("SELECT status FROM request_log LIMIT 1"))
        except Exception as e:
            if 'no such column' in str(e):
                app.logger.info("检测到缺失字段，开始执行数据库升级...")
                db.session.execute(text("ALTER TABLE request_log ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending'"))
                db.session.commit()
                app.logger.info("数据库升级完成")

# 在应用初始化后调用
check_and_update_db()
# 用户模型
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    limit_rule= db.Column(db.String(100), nullable=True)
class RequestLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=True)
    ip = db.Column(db.String(45), nullable=False)
    endpoint = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    duration = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # 新增状态字段
# 创建数据库
# @app.before_request
# def create_tables():
#     db.create_all()
# 删除@app.before_request装饰器，改为在应用启动时调用
with app.app_context():
    db.create_all()
# app.logger.info(f"SQLALCHEMY_POOL_SIZE: {app.config['SQLALCHEMY_POOL_SIZE']}")
# app.logger.info(f"SQLALCHEMY_MAX_OVERFLOW: {app.config['SQLALCHEMY_MAX_OVERFLOW']}")
# 动态流量限制装饰器
# def dynamic_limit(fn):
#     @jwt_required()  # 确保 JWT 验证通过
#     @functools.wraps(fn)
#     def wrapper(*args, **kwargs):
        
#         if limit_enable:
#             current_user = get_jwt_identity()  # 获取当前用户的身份（通常是用户名）
#             user=User.query.filter_by(username=current_user).first()
#             if user:
#                 ip=limit_key_func()
#                 app.logger.info(f"limit rule,use: {current_user},{user.limit_rule},ip:{ip}")
                
#                 if current_user == pubilc_test_username:
#                     # 使用动态限制
#                     with limiter.limit(user.limit_rule,scope="testuser_ips"):
#                         return fn(*args, **kwargs)
#                 else:
#                     with limiter.limit(user.limit_rule,key_func=lambda:current_user,scope=SHARED_LIMIT_SCOPE):
#                         return fn(*args, **kwargs)
#             else:
#                 # 使用默认限制（或者返回一个错误）
#                 # 这里我们选择使用默认限制，因为 Limiter 会自动处理超出限制的情况
#                 app.logger.info("default rule")
#                 with limiter.limit("500/day;400/hour",scope=SHARED_LIMIT_SCOPE):  # None 表示使用默认限制
#                     return fn(*args, **kwargs)
#         else:
#             return fn(*args, **kwargs)
#     return wrapper
def get_wav_duration_from_filestorage(filestorage):
    # 重置文件指针到起始位置（重要！）
    filestorage.stream.seek(0)
    
    # 将文件内容读取到内存字节流
    in_memory_file = BytesIO(filestorage.read())
    filestorage.stream.seek(0)
    # 使用 wave 模块读取
    with wave.open(in_memory_file, 'rb') as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
        return frames / float(rate)  # 返回秒数
# 动态流量限制装饰器
def dynamic_limit(fn):
    @jwt_required()  # 确保 JWT 验证通过
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        
        if not limit_enable:return fn(*args, **kwargs)
        audio_file = request.files['file']
        try:
            duration = get_wav_duration_from_filestorage(audio_file)
        except wave.Error:
            app.logger.info("Invalid WAV file")
        current_user = get_jwt_identity()  # 获取当前用户的身份（通常是用户名）
        user=User.query.filter_by(username=current_user).first()
        if user:
            if duration <6:
                rule=str(user.limit_rule).replace("8/minute","16/minute").replace("4/minute","8/minute")
            else:
                rule=user.limit_rule
            app.logger.info(f"limit rule,use: {current_user},{rule}")
            
            if current_user == pubilc_test_username:
                # 使用动态限制
                with limiter.limit(user.limit_rule,scope=SHARED_LIMIT_SCOPE):
                    return fn(*args, **kwargs)
            else:
                limit_key_func()
                with limiter.limit(
                    user.limit_rule,
                    key_func=lambda:current_user,
                    scope=f"user:{current_user}",
                    deduct_when=lambda r: (
                        r.status_code == 200 
                    )
                ): return fn(*args, **kwargs)
                    


        else:
            # 使用默认限制（或者返回一个错误）
            # 这里我们选择使用默认限制，因为 Limiter 会自动处理超出限制的情况
            app.logger.info("default rule")
            with limiter.limit("500/day;400/hour",scope=SHARED_LIMIT_SCOPE):  # None 表示使用默认限制
                return fn(*args, **kwargs)

    return wrapper

def log_request(func):
    @jwt_required()
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        st = time.time()
        current_user = get_jwt_identity()  # 获取当前用户的身份（通常是用户名）
        XRealIp = request.headers.get('X-Real-Ip')
        x_forwarded_for = request.headers.get('X-Forwarded-For')
        if XRealIp:
            ip=XRealIp.split(',')[0].strip()
        elif x_forwarded_for:
            ip=x_forwarded_for.split(',')[0].strip()
        else:
            ip=request.remote_addr
        log_data = {'status': 'failed'}
        
        try:
            # 统一返回值处理
            result = func(*args, **kwargs)
            response = make_response(result)
            log_data['duration'] = time.time() - st
            
            # 状态码安全获取
            try:
                status_code = getattr(response, 'status_code', 500)
                log_data['status'] = 'success' if 200 <= status_code < 400 else 'failed'
            except Exception as e:
                app.logger.error(f"状态码获取失败: {str(e)}")

            return response
        except RateLimitExceeded as e:
            jsonify(), 430 
            # 构建标准限流响应
            response = jsonify({"error": "Too many request","limit": str(e.limit.limit),})
            response.status_code = 430
            log_data['status'] = 'rate_limited'
            return response
        finally:
            # 字段容错处理
            log = RequestLog(
                username=current_user,
                ip=ip,
                endpoint=request.path,
                duration=log_data.get('duration', 0),
                status=log_data.get('status', 'failed')
            )
            try:
                db.session.add(log)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"日志提交失败: {str(e)}")


    return wrapper

def reset_limits():
    """每天北京时间8点重置内存计数器"""
    with app.app_context():
        # 通过私有方法重置存储（注意：这是基于当前实现细节）
        limiter.storage.reset()
        print("计数器已重置于北京时间8点")

# # 配置定时任务
# scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Shanghai'))
# scheduler.add_job(
#     func=reset_limits,
#     trigger='cron',
#     hour=8,
#     minute=0,
#     second=0
# )
# scheduler.start()

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

@app.route('/ui/stats')
def stats_ui():
    if 'user_id' not in session:
        return redirect(url_for('logout_ui'))
    
    user = User.query.get(session['user_id'])
    if not user or not user.is_admin:
        return redirect(url_for('logout_ui'))

    # 获取所有可用小时选项
    hours = db.session.query(
        db.func.strftime('%Y-%m-%d %H:00', 
                        db.func.datetime(RequestLog.timestamp, '+8 hours')
                        ).label('hour')
    ).distinct().order_by(db.desc('hour')).all()

    # 获取选中小时（默认为最新小时）
    selected_hour = request.args.get('hour', hours[0].hour if hours else None)

    # 小时统计查询
    hourly_query = db.session.query(
        db.func.strftime('%Y-%m-%d %H:00', 
                        db.func.datetime(RequestLog.timestamp, '+8 hours')
                        ).label('hour'),
        RequestLog.username,
        RequestLog.ip,
        RequestLog.endpoint,
        db.func.count().label('count')
    )

    if selected_hour:
        hourly_query = hourly_query.having(
            db.func.strftime('%Y-%m-%d %H:00', 
                           db.func.datetime(RequestLog.timestamp, '+8 hours')
                           ) == selected_hour
        )

    hourly_stats = hourly_query.group_by('hour', RequestLog.username, RequestLog.ip, RequestLog.endpoint).all()

    # 计算调用次数总和
    total_count = sum(stat.count for stat in hourly_stats)

    # 耗时分布统计
    duration_query = db.session.query(
        db.case(
            (RequestLog.duration < 3, '0-3s'),
            (RequestLog.duration < 10, '3-10s'),
            (RequestLog.duration < 20, '10-20s'),
            (RequestLog.duration < 30, '20-30s'),
            (RequestLog.duration < 60, '30-60s'),
            (RequestLog.duration < 90, '60-90s'),
            else_='90s+'
        ).label('duration_range'),
        db.func.count().label('count')
    )

    if selected_hour:
        duration_query = duration_query.filter(
            db.func.strftime('%Y-%m-%d %H:00', 
                           db.func.datetime(RequestLog.timestamp, '+8 hours')
                           ) == selected_hour
        )

    duration_stats = duration_query.group_by('duration_range').all()

    # 对耗时区间进行排序
    sorted_duration_stats = sorted(duration_stats, key=lambda x: (
        int(x.duration_range.split('-')[0].rstrip('s')) if x.duration_range != '90s+' else 90
    ))

    return render_template('stats.html',
                         hours=[h.hour for h in hours],
                         selected_hour=selected_hour,
                         hourly_stats=hourly_stats,
                         duration_stats=sorted_duration_stats,
                         total_count=total_count)


# 用户管理页面
@app.route('/ui/manage_users', methods=['GET', 'POST'])
def manage_users_ui():
    if 'user_id' not in session:
        return redirect(url_for('logout_ui'))
 
    if request.method == 'POST':
        new_username = request.form['new_username']
        new_password = request.form['new_password']
        new_is_admin = request.form.get('new_is_admin', 'false') == 'on'
        is_update = request.form.get('is_update', 'false') == 'on'
        new_limit_rule = request.form['new_limit_rule']
        if is_update :
            user_base=User.query.filter_by(username=new_username).first()
            user_base.is_admin=new_is_admin
            if new_password is not None and new_password != "":user_base.password=new_password
            if new_limit_rule is not None and new_limit_rule != "":user_base.limit_rule=new_limit_rule
                
        else:
            if new_password is None or new_password =="":flash('Please enter password')
            new_limit_rule = new_limit_rule if new_limit_rule is not None and new_limit_rule != "" else "10000/day;1000/hour"
            hashed_password = generate_password_hash(new_password)
            new_user = User(username=new_username, password=hashed_password, is_admin=new_is_admin,limit_rule=new_limit_rule)
            db.session.add(new_user)
        db.session.commit()
        flash('User added/updated successfully')
 
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
@log_request
@dynamic_limit
def whisper_transcriptions():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/whisper/transcriptions user:{current_user} id:{id}")
    file=request.files['file']

    audiofile=file.stream.read()
    res=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='zh')
    text=res.text
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
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
@log_request
@dynamic_limit
def whisper_translations():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/whisper/translations user:{current_user} id:{id}")
    file=request.files['file']

    audiofile=file.stream.read()
    res=whisperclient.audio.translations.create(model=model, file=audiofile,language='zh')
    text=res.text
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
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
@log_request
@dynamic_limit
def libreTranslate():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/libreTranslate user:{current_user} id:{id}")
    data = request.get_json()
    source = data.get('source')
    target = data.get('target')
    text = data.get('text')
    res=translate_local(text,source,target)
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': res}), 200

# 翻译
@app.route('/api/func/translateToEnglish', methods=['POST'])
@log_request
@dynamic_limit
def translate():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/func/translateToEnglish user:{current_user} id:{id}")
    file=request.files['file']
    audiofile=file.stream.read()

    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='zh')
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    if(text.text in errorFilter["errorResultDict"]) or any(errorKey in text.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    if enable_baiduapi :
        translatedText=fanyi.translate_text(text.text, from_=fanyi.Lang.ZH,to=fanyi.Lang.EN)
    else:
        translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
    return jsonify({'text': text.text,'translatedText': translatedText if enable_baiduapi else translatedText.text}), 200

# 多语言翻译
@app.route('/api/func/translateToOtherLanguage', methods=['POST'])
@log_request
@dynamic_limit
def translateToOtherLanguage():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/func/translateToOtherLanguage user:{current_user} id:{id}")
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
    
    if enable_baiduapi and libretranslate_to_baidu[targetLanguage]:
        transText=fanyi.translate_text(text.text, from_=fanyi.Lang.ZH,to=libretranslate_to_baidu[targetLanguage])
    else:
        translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
        if targetLanguage =='en':transText=translatedText.text
        else:transText=translate_local(translatedText.text,"en",targetLanguage)
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': text.text,'translatedText':transText}), 200

# 多语言翻译
@app.route('/api/func/multitranslateToOtherLanguage', methods=['POST'])
@log_request
@dynamic_limit
def multitranslateToOtherLanguage():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/func/multitranslateToOtherLanguage user:{current_user} id:{id}")
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
    filterText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language="zh")
    if(filterText.text in errorFilter["errorResultDict"]) or any(errorKey in filterText.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=sourceLanguage)
    if enable_baiduapi and whisper_to_baidu[sourceLanguage] and libretranslate_to_baidu[targetLanguage]:
         transText=fanyi.translate_text(text.text, from_=whisper_to_baidu[sourceLanguage],to=libretranslate_to_baidu[targetLanguage])
    else:
        translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
        if targetLanguage =='en':transText=translatedText.text
        else:transText=translate_local(translatedText.text,"en",targetLanguage)
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': text.text,'translatedText':transText}), 200

# 多语言翻译
@app.route('/api/func/doubleTransciption', methods=['POST'])
@log_request
@dynamic_limit
def doubleTransciption():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/func/doubleTranscibe user:{current_user} id:{id}")
    file=request.files['file']
    params=request.form.to_dict()
    targetLanguage=params["targetLanguage"]
    sourceLanguage=params["sourceLanguage"]
    app.logger.info(f"targetLanguage:{targetLanguage}, sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
         return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    if targetLanguage not in whisperSupportedLanguageList:
        return jsonify({'message':f"targetLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    audiofile=file.stream.read()
    filterText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language="zh")
    if(filterText.text in errorFilter["errorResultDict"]) or any(errorKey in filterText.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=sourceLanguage)
    transText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=targetLanguage)
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': text.text,'translatedText':transText.text,'zhText':filterText.text}), 200

# 多语言翻译
@app.route('/api/whisper/multitranscription', methods=['POST'])
@log_request
@dynamic_limit
def multitranscription():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/api/whisper/multitranscription user:{current_user} id:{id}")
    file=request.files['file']
    params=request.form.to_dict()
    sourceLanguage=params["sourceLanguage"]
    app.logger.info(f"sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
        return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    audiofile=file.stream.read()

    filterText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language="zh")
    if(filterText.text in errorFilter["errorResultDict"]) or any(errorKey in filterText.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    if sourceLanguage!='zh':text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=sourceLanguage)
    else: text=filterText
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': text.text}), 200



if __name__ == '__main__':
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
    app.run(debug=True,host='0.0.0.0',port=8980)

