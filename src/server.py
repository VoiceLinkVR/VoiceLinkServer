import functools
import pymysql
import logging
import logging.handlers
from flask import Flask, make_response,render_template, request, redirect, url_for, flash, session, abort, jsonify, Response
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
from datetime import datetime,timedelta
import translators
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
from io import BytesIO
import wave
import html
import traceback
import base64
from pydantic import BaseModel
import re
import struct
import opuslib
import emoji

# 鑾峰彇鐜鍙傛暟
whisper_host=os.getenv("WHISPER_HOST")
whisper_prot=os.getenv("WHISPER_PORT")
sensevoice_host=os.getenv("SENSEVOICE_HOST")
sensevoice_prot=os.getenv("SENSEVOICE_PORT")
whisper_apiKey=os.getenv("WHISPER_APIKEY") #褰撳墠faster-whisper-server/latest涓棤鏁?libreTranslate_host=os.getenv("LIBRETRANSLATE_HOST")
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
enable_web_translators=os.getenv("ENABLE_WEB_TRANSLATORS")
translator_Service=os.getenv("TRANSLATOR_SERVICE")
redisUrl=os.getenv("LIMITER_REDIS_URL")
ttsUrl=os.getenv("TTS_URL")
ttsToken=os.getenv("TTS_TOKEN")
latestVersion=os.getenv("LATEST_VERSION")
packageBaseURL=os.getenv("PACKAGE_BASE_URL")
packageType=os.getenv("PACKAGE_TYPE")
limit_enable= False if  limit_enable is None or limit_enable =="" else True
# whisper config
if whisper_host is not None and whisper_prot is not None:whisper_url=f'http://{whisper_host}:{whisper_prot}/v1/'
else: whisper_url='http://127.0.0.1:8000/v1/'
if sensevoice_host is not None and sensevoice_prot is not None:sensevoice_url=f'http://{sensevoice_host}:{sensevoice_prot}/v1/audio/transcriptions'
else: sensevoice_url='http://127.0.0.1:8800/v1/audio/transcriptions'
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

# 閰嶇疆璇︾粏鏃ュ織绯荤粺
def setup_logging():
    """閰嶇疆璇︾粏鏃ュ織绯荤粺"""
    # 鍒涘缓logs鐩綍锛堝鏋滀笉瀛樺湪锛?    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    # 閰嶇疆鏃ュ織鏍煎紡
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    detailed_format = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s'
    
    # 鍒涘缓logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # 鎺у埗鍙板鐞嗗櫒
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(log_format)
    console_handler.setFormatter(console_formatter)
    
    # 鏂囦欢澶勭悊鍣?- 涓昏鏃ュ織
    main_log_file = os.path.join(log_dir, 'server.log')
    main_handler = logging.handlers.RotatingFileHandler(
        main_log_file, maxBytes=50*1024*1024, backupCount=10, encoding='utf-8'
    )
    main_handler.setLevel(logging.INFO)
    main_formatter = logging.Formatter(detailed_format)
    main_handler.setFormatter(main_formatter)
    
    # 鏂囦欢澶勭悊鍣?- Debug鏃ュ織
    debug_log_file = os.path.join(log_dir, 'debug.log')
    debug_handler = logging.handlers.RotatingFileHandler(
        debug_log_file, maxBytes=50*1024*1024, backupCount=5, encoding='utf-8'
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_formatter = logging.Formatter(detailed_format)
    debug_handler.setFormatter(debug_formatter)
    
    # 閿欒鏃ュ織澶勭悊鍣?    error_log_file = os.path.join(log_dir, 'error.log')
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_file, maxBytes=20*1024*1024, backupCount=5, encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_formatter = logging.Formatter(detailed_format)
    error_handler.setFormatter(error_formatter)
    
    # 娣诲姞澶勭悊鍣ㄥ埌logger
    logger.addHandler(console_handler)
    logger.addHandler(main_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(error_handler)
    
    return logger

# 鍒濆鍖栨棩蹇楃郴缁?logger = setup_logging()

# 搴旂敤鍜屾暟鎹簱閰嶇疆
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = sqlitePath if sqlitePath is not None else 'sqlite:///users.db'  # 浣跨敤 SQLite 鏁版嵁搴?app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# app.config['SQLALCHEMY_POOL_SIZE'] = int(sqlalchemy_pool_size) if sqlalchemy_pool_size else 20  # 璁剧疆杩炴帴姹犲ぇ灏?# app.config['SQLALCHEMY_MAX_OVERFLOW'] = int(sqlalchemy_max_overflow) if sqlalchemy_max_overflow else 40  # 璁剧疆鏈€澶ф孩鍑鸿繛鎺ユ暟
# app.config['SQLALCHEMY_POOL_SIZE'] = 30  # 璁剧疆杩炴帴姹犲ぇ灏?# app.config['SQLALCHEMY_MAX_OVERFLOW'] = 60  # 璁剧疆鏈€澶ф孩鍑鸿繛鎺ユ暟
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'poolclass': QueuePool,  # 寮哄埗浣跨敤 QueuePool
    'pool_size': 0,
    'max_overflow': -1
}
app.config['JWT_SECRET_KEY'] = 'wVLAF_13N6XL_QmP.DjkKsV' if jwt_secret_key is None else jwt_secret_key  # JWT 绉橀挜
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = 604800 if jwt_access_token_expires is None else int(jwt_access_token_expires)
app.config['SECRET_KEY'] = 'wVddLAF_13dsdddN6XL_QmP.DjkKsV' if flask_secret_key is None else flask_secret_key
app.config['SQLALCHEMY_POOL_RECYCLE'] = 3600  # 鍗曚綅锛氱锛?灏忔椂锛?
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
        logger.info(ip)
        return ip
    limiter=Limiter(
        app=app,
        key_func=limit_key_func,
        default_limits=["400/hour"],
        storage_uri="memory://" if redisUrl is None else redisUrl)
    # 鑷畾涔夐敊璇鐞嗗櫒
    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit_exceeded(e):
        return jsonify({
        "error": "Too many request",
        "limit": str(e.limit.limit),  # 濡?"2 per 1 day"
    }), 430 # 杩斿洖 403 鐘舵€佺爜
db = SQLAlchemy(app)
jwt = JWTManager(app)


def do_translate(text,from_,to):
    tol=transalte_zt.get(translator_Service,'zt') if to=='zt' else to
    return html.unescape(translators.translate_text(text,translator=translator_Service,from_language=from_,to_language=tol))

glm_client = OpenAI(
    api_key="4c3d963619884fc69e3a02c581925691.mgFDmvFyfDWmGvub",
    base_url="https://open.bigmodel.cn/api/paas/v4/"
) 
def openai_translate(text,t1,t2,t3):
    c1=f'2. 灏嗘敹鍒扮殑鏂囧瓧缈昏瘧鎴恵codeTochinese[t2]}銆? if t2!="none" else ''
    c2=f'3. 灏嗘敹鍒扮殑鏂囧瓧缈昏瘧鎴恵codeTochinese[t3]}銆? if t3!="none" else ''
    o1=f',"translatedText2":{codeTochinese[t2]}璇戞枃' if t2!="none" else ''
    o2=f',"translatedText3":{codeTochinese[t3]}璇戞枃' if t3!="none" else ''
    contenttext=f'''浣犳槸缈昏瘧鍔╂墜銆備綘鐨勪换鍔℃槸锛?1. 灏嗘敹鍒扮殑鏂囧瓧缈昏瘧鎴恵codeTochinese[t1]}銆?{c1}
{c2}

璇蜂弗鏍兼寜鐓у涓嬫牸寮忎粎杈撳嚭JSON锛屼笉瑕佽緭鍑篜ython浠ｇ爜鎴栧叾浠栦俊鎭紝JSON瀛楁浣跨敤椤垮彿銆愩€併€戝尯闅旓細'''+'{'+f'"text":鏀跺埌鐨勬枃瀛?"translatedText":{codeTochinese[t1]}璇戞枃{o1}{o2}'+'}'
    logger.info("llm send:"+contenttext)

    completion = glm_client.chat.completions.create(
        model="glm-4-flash-250414",  
        messages=[    
            {"role": "system", "content":contenttext},    
            {"role": "user", "content": text} 
        ],
        top_p=0.7,
        temperature=0.9,
        response_format = {'type': 'json_object'},
    ) 
    data=completion.choices[0].message.content
    logger.info("llm return:"+data)
    return data


with open('filter.json', 'r',encoding='utf-8') as src:
    try:
        srcConfig1=json.load(src)  
    except requests.exceptions.JSONDecodeError as e:
        logger.warning("local filter.json error||婧愯繃婊ら厤缃枃浠跺紓甯?璇︽儏锛?+str(e.strerror))
        exit(1)

try:
    responce= requests.get("https://raw.githubusercontent.com/VoiceLinkVR/VoiceLinkServer/refs/heads/main/src/filter.json" if filter_web_url is None else filter_web_url) 
    srcConfig2= responce.json()
except Exception as e:
    logger.warning("failed to update filter.json through web || 閫氳繃缃戠粶鑾峰彇婧愯繃婊ら厤缃枃浠跺け璐?璇︽儏锛?+str(e.strerror))
    srcConfig2=srcConfig1


for srcConfig in [srcConfig1,srcConfig2]:
    #杩囨护瑙勫垯妫€鏌?filter.json check
    with open('data/filterConfig/filter.json', 'r',encoding='utf-8') as dest:
        try:
            destConfig=json.load(dest)
        except requests.exceptions.JSONDecodeError as e:
            logger.warning("杩囨护閰嶇疆鏂囦欢寮傚父,璇︽儏锛?+str(e.strerror))
            exit(1)
    configDiff=list(set(srcConfig.keys())-set(destConfig.keys()))
    if configDiff != [] :
        logger.info(" filter in filter.json updated ||filter.json鏂囦欢鏇存柊 :"+str(configDiff))
        for newConfig in configDiff:
            destConfig[newConfig]=srcConfig[newConfig]
        with open('data/filterConfig/filter.json', 'w', encoding="utf-8") as file:
            file.write(json.dumps(destConfig,ensure_ascii=False, indent=4))
    for filter in srcConfig.keys():
        filterDiff=[rule for rule in srcConfig[filter] if rule not in destConfig[filter]]
        if filterDiff != []:
            logger.info("rules in filter.json column updated ||filter.json鏂囦欢鏇存柊 瑙勫垯鏇存柊,澧炲姞锛?+str(filterDiff))
            for newFilter in filterDiff:
                destConfig[filter].append(newFilter)
            with open('data/filterConfig/filter.json', 'w', encoding="utf-8") as file:
                file.write(json.dumps(destConfig,ensure_ascii=False, indent=4))
errorFilter=destConfig
from sqlalchemy import text
from sqlalchemy import inspect

def check_and_update_db():
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            # 妫€鏌ser琛?            if 'user' in inspector.get_table_names():
                user_columns = [col['name'] for col in inspector.get_columns('user')]
                # 娣诲姞缂哄け瀛楁
                if 'expiration_date' not in user_columns:
                    db.session.execute(text('ALTER TABLE user ADD COLUMN expiration_date DATETIME'))
                    logger.info("Added expiration_date to user table")
                if 'is_active' not in user_columns:
                    db.session.execute(text('ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1'))
                    logger.info("Added is_active to user table")

            # 妫€鏌equest_log琛?            if 'request_log' in inspector.get_table_names():
                log_columns = [col['name'] for col in inspector.get_columns('request_log')]
                if 'status' not in log_columns:
                    db.session.execute(text('ALTER TABLE request_log ADD COLUMN status VARCHAR(20) DEFAULT "pending"'))
                    app.session.execute(text('UPDATE request_log SET status = "success" WHERE status IS NULL'))
                    logger.info("Added status to request_log table")

            db.session.commit()
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"鏁版嵁搴撳崌绾уけ璐? {str(e)}")
        finally:
            db.session.close()

# 鍦ㄥ簲鐢ㄥ垵濮嬪寲鍚庤皟鐢?check_and_update_db()

# 鏁版嵁搴撴搷浣滄棩蹇楄楗板櫒
def log_db_operation(operation_type):
    """鏁版嵁搴撴搷浣滄棩蹇楄楗板櫒"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            logger.debug(f"[DB-{operation_type}] 寮€濮嬫搷浣? {func.__name__} 鍙傛暟: {str(args)[:200]}...")
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                logger.debug(f"[DB-{operation_type}] 鎴愬姛: {func.__name__} 鑰楁椂: {duration:.3f}s")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"[DB-{operation_type}] 澶辫触: {func.__name__} 鑰楁椂: {duration:.3f}s 閿欒: {str(e)}")
                logger.exception("鏁版嵁搴撴搷浣滃紓甯?")
                raise
        return wrapper
    return decorator

# 鎵╁睍SQLAlchemy鏌ヨ绫讳互娣诲姞鏃ュ織
class LoggingSQLAlchemy(SQLAlchemy):
    def apply_pool_defaults(self, app, options):
        super().apply_pool_defaults(app, options)
        # 娣诲姞杩炴帴姹犳棩蹇?        options['echo'] = True  # 鍚敤SQL鏃ュ織
        options['echo_pool'] = True  # 鍚敤杩炴帴姹犳棩蹇?
# 浣跨敤甯︽棩蹇楃殑SQLAlchemy
db = LoggingSQLAlchemy(app)
jwt = JWTManager(app)

# 鍦ㄥ簲鐢ㄥ垵濮嬪寲鍚庤皟鐢?check_and_update_db()
logger.info("鏁版嵁搴撳垵濮嬪寲瀹屾垚")

def check_user_expiration():
    with app.app_context():
        logger.info("寮€濮嬫鏌ョ敤鎴疯繃鏈熺姸鎬?..")
        now = datetime.now(pytz.utc)
        expired_users = User.query.filter(
            User.expiration_date <= now,
            User.is_active == True
        ).all()
        
        for user in expired_users:
            user.is_active = False
            logger.info(f"鐢ㄦ埛 {user.username} 宸茶繃鏈燂紝鑷姩绂佺敤")
        
        db.session.commit()
        logger.info(f"鐢ㄦ埛杩囨湡妫€鏌ュ畬鎴愶紝鍏卞鐞?{len(expired_users)} 涓繃鏈熺敤鎴?)

# 閰嶇疆瀹氭椂浠诲姟
scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(
    func=check_user_expiration,
    trigger='cron',
    hour=0,  # 姣忓ぉUTC鏃堕棿0鐐规墽琛?    minute=0,
    second=0
)
# scheduler.start()
# 鐢ㄦ埛妯″瀷
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    limit_rule= db.Column(db.String(100), nullable=True)
    expiration_date = db.Column(db.DateTime, nullable=True)  # 鏂板鏈夋晥鏈熷瓧娈?    is_active = db.Column(db.Boolean, default=True)  # 鏂板婵€娲荤姸鎬佸瓧娈?class RequestLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=True)
    ip = db.Column(db.String(45), nullable=False)
    endpoint = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    duration = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # 鏂板鐘舵€佸瓧娈?# 鍒涘缓鏁版嵁搴?# @app.before_request
# def create_tables():
#     db.create_all()
# 鍒犻櫎@app.before_request瑁呴グ鍣紝鏀逛负鍦ㄥ簲鐢ㄥ惎鍔ㄦ椂璋冪敤
with app.app_context():
    db.create_all()
# logger.info(f"SQLALCHEMY_POOL_SIZE: {app.config['SQLALCHEMY_POOL_SIZE']}")
# logger.info(f"SQLALCHEMY_MAX_OVERFLOW: {app.config['SQLALCHEMY_MAX_OVERFLOW']}")
# 鍔ㄦ€佹祦閲忛檺鍒惰楗板櫒
# def dynamic_limit(fn):
#     @jwt_required()  # 纭繚 JWT 楠岃瘉閫氳繃
#     @functools.wraps(fn)
#     def wrapper(*args, **kwargs):
        
#         if limit_enable:
#             current_user = get_jwt_identity()  # 鑾峰彇褰撳墠鐢ㄦ埛鐨勮韩浠斤紙閫氬父鏄敤鎴峰悕锛?#             user=User.query.filter_by(username=current_user).first()
#             if user:
#                 ip=limit_key_func()
#                 logger.info(f"limit rule,use: {current_user},{user.limit_rule},ip:{ip}")
                
#                 if current_user == pubilc_test_username:
#                     # 浣跨敤鍔ㄦ€侀檺鍒?#                     with limiter.limit(user.limit_rule,scope="testuser_ips"):
#                         return fn(*args, **kwargs)
#                 else:
#                     with limiter.limit(user.limit_rule,key_func=lambda:current_user,scope=SHARED_LIMIT_SCOPE):
#                         return fn(*args, **kwargs)
#             else:
#                 # 浣跨敤榛樿闄愬埗锛堟垨鑰呰繑鍥炰竴涓敊璇級
#                 # 杩欓噷鎴戜滑閫夋嫨浣跨敤榛樿闄愬埗锛屽洜涓?Limiter 浼氳嚜鍔ㄥ鐞嗚秴鍑洪檺鍒剁殑鎯呭喌
#                 logger.info("default rule")
#                 with limiter.limit("500/day;400/hour",scope=SHARED_LIMIT_SCOPE):  # None 琛ㄧず浣跨敤榛樿闄愬埗
#                     return fn(*args, **kwargs)
#         else:
#             return fn(*args, **kwargs)
#     return wrapper
def get_wav_duration_from_filestorage(filestorage):
    # 閲嶇疆鏂囦欢鎸囬拡鍒拌捣濮嬩綅缃紙閲嶈锛侊級
    filestorage.stream.seek(0)
    
    # 灏嗘枃浠跺唴瀹硅鍙栧埌鍐呭瓨瀛楄妭娴?    in_memory_file = BytesIO(filestorage.read())
    filestorage.stream.seek(0)
    # 浣跨敤 wave 妯″潡璇诲彇
    with wave.open(in_memory_file, 'rb') as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
        return frames / float(rate)  # 杩斿洖绉掓暟
# 鍔ㄦ€佹祦閲忛檺鍒惰楗板櫒
def dynamic_limit(fn):
    @jwt_required()  # 纭繚 JWT 楠岃瘉閫氳繃
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        
        if not limit_enable:return fn(*args, **kwargs)
        # duration=9999
        # if request.path!="/api/func/tts" and request.path!="/api/func/webtranslate":
        #     audio_file = request.files['file']
        #     try:
        #         duration = get_wav_duration_from_filestorage(audio_file)
        #     except wave.Error:
        #         logger.info("Invalid WAV file")
        current_user = get_jwt_identity()  # 鑾峰彇褰撳墠鐢ㄦ埛鐨勮韩浠斤紙閫氬父鏄敤鎴峰悕锛?        user=User.query.filter_by(username=current_user).first()
        if user:
            # if duration <6:
            #     rule=str(user.limit_rule).replace("8/minute","16/minute").replace("4/minute","8/minute")
            # else:
            rule=user.limit_rule
            logger.info(f"[LIMIT] 鐢ㄦ埛: {current_user}, 闄愬埗瑙勫垯: {rule}")
            
            if current_user == pubilc_test_username:
                # 浣跨敤鍔ㄦ€侀檺鍒?                with limiter.limit(user.limit_rule,scope=SHARED_LIMIT_SCOPE):
                    return fn(*args, **kwargs)
            else:
                limit_key_func()
                with limiter.limit(
                    user.limit_rule,
                    key_func=lambda:current_user+time.strftime("%Y-%m-%d", time.localtime()),
                    scope=f"user:{current_user}",
                    deduct_when=lambda r: (
                        r.status_code == 200 
                    )
                ): return fn(*args, **kwargs)
                    


        else:
            # 浣跨敤榛樿闄愬埗锛堟垨鑰呰繑鍥炰竴涓敊璇級
            # 杩欓噷鎴戜滑閫夋嫨浣跨敤榛樿闄愬埗锛屽洜涓?Limiter 浼氳嚜鍔ㄥ鐞嗚秴鍑洪檺鍒剁殑鎯呭喌
            logger.info("[LIMIT] 浣跨敤榛樿闄愭祦瑙勫垯")
            with limiter.limit("500/day;400/hour",scope=SHARED_LIMIT_SCOPE):  # None 琛ㄧず浣跨敤榛樿闄愬埗
                return fn(*args, **kwargs)

    return wrapper

def log_request(func):
    @jwt_required()
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        st = time.time()
        current_user = get_jwt_identity()  # 鑾峰彇褰撳墠鐢ㄦ埛鐨勮韩浠斤紙閫氬父鏄敤鎴峰悕锛?        XRealIp = request.headers.get('X-Real-Ip')
        x_forwarded_for = request.headers.get('X-Forwarded-For')
        if XRealIp:
            ip=XRealIp.split(',')[0].strip()
        elif x_forwarded_for:
            ip=x_forwarded_for.split(',')[0].strip()
        else:
            ip=request.remote_addr
        log_data = {'status': 'failed'}
        
        # 璁板綍璇锋眰寮€濮?        logger.info(f"[API-START] 鐢ㄦ埛: {current_user}, IP: {ip}, 鎺ュ彛: {request.path}, 鏂规硶: {request.method}")
        
        try:
            # 璁板綍璇锋眰鍙傛暟
            if request.method == 'POST':
                if request.files:
                    logger.debug(f"[API-PARAMS] 鏂囦欢鍙傛暟: {list(request.files.keys())}")
                if request.form:
                    logger.debug(f"[API-PARAMS] 琛ㄥ崟鍙傛暟: {dict(request.form)}")
                if request.json:
                    logger.debug(f"[API-PARAMS] JSON鍙傛暟: {request.json}")
            
            # 缁熶竴杩斿洖鍊煎鐞?            result = func(*args, **kwargs)
            response = make_response(result)
            log_data['duration'] = time.time() - st
            
            # 鐘舵€佺爜瀹夊叏鑾峰彇
            try:
                status_code = getattr(response, 'status_code', 500)
                log_data['status'] = 'success' if 200 <= status_code < 400 else 'failed'
                logger.info(f"[API-END] 鐢ㄦ埛: {current_user}, 鎺ュ彛: {request.path}, 鐘舵€佺爜: {status_code}, 鑰楁椂: {log_data['duration']:.3f}s")
            except Exception as e:
                logger.error(f"[API-ERROR] 鐘舵€佺爜鑾峰彇澶辫触: {str(e)}")

            return response
        except RateLimitExceeded as e:
            # 鏋勫缓鏍囧噯闄愭祦鍝嶅簲
            response = jsonify({"error": "Too many request","limit": str(e.limit.limit),})
            response.status_code = 430
            log_data['status'] = 'rate_limited'
            logger.warning(f"[API-LIMIT] 鐢ㄦ埛: {current_user} 瑙﹀彂闄愭祦, IP: {ip}, 闄愬埗: {str(e.limit.limit)}")
            return response
        except Exception as e:
            log_data['duration'] = time.time() - st
            log_data['status'] = 'error'
            logger.error(f"[API-ERROR] 鐢ㄦ埛: {current_user}, 鎺ュ彛: {request.path}, 閿欒: {str(e)}")
            logger.exception("API澶勭悊寮傚父:")
            raise
        finally:
            # 瀛楁瀹归敊澶勭悊
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
                logger.debug(f"[DB-LOG] 璇锋眰鏃ュ織璁板綍鎴愬姛: 鐢ㄦ埛={current_user}, 鎺ュ彛={request.path}")
            except Exception as e:
                db.session.rollback()
                logger.error(f"[DB-ERROR] 鏃ュ織鎻愪氦澶辫触: {str(e)}")


    return wrapper

# def reset_limits():
#     """姣忓ぉ鍖椾含鏃堕棿8鐐归噸缃唴瀛樿鏁板櫒"""
#     with app.app_context():
#         # 閫氳繃绉佹湁鏂规硶閲嶇疆瀛樺偍锛堟敞鎰忥細杩欐槸鍩轰簬褰撳墠瀹炵幇缁嗚妭锛?#         limiter.storage.reset()
#         logger.info("璁℃暟鍣ㄥ凡閲嶇疆浜庡寳浜椂闂?鐐?)

# scheduler.add_job(
#     func=reset_limits,
#     trigger='cron',
#     hour=0,
#     minute=0,
#     second=0
# )
scheduler.start()

# 鐧诲綍琛ㄥ崟澶勭悊
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
            logger.info(f"[ADMIN] 鍒涘缓绠＄悊鍛樼敤鎴? {username}")
            return redirect(url_for('manage_users_ui'))
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password) and user.is_admin:
            session['user_id'] = user.id
            logger.info(f"[ADMIN] 绠＄悊鍛樼櫥褰? {username}")
            return redirect(url_for('manage_users_ui'))
        
        else:
            logger.warning(f"[AUTH] 绠＄悊鍛樼櫥褰曞け璐? {username}")
            flash('Invalid username or password')
    return render_template('login.html')


@app.route('/ui/stats')
def stats_ui():
    if 'user_id' not in session:
        return redirect(url_for('logout_ui'))
    
    user = User.query.get(session['user_id'])
    if not user or not user.is_admin:
        return redirect(url_for('logout_ui'))

    # --- 鍏煎鎬т慨鏀?---
    
    dialect_name = db.engine.dialect.name

    if dialect_name == 'sqlite':
        timestamp_to_hour_str = db.func.strftime(
            '%Y-%m-%d %H:00', 
            db.func.datetime(RequestLog.timestamp, '+8 hours')
        )
    elif dialect_name == 'mysql':
        timestamp_with_tz = db.func.date_add(RequestLog.timestamp, text('INTERVAL 8 HOUR'))
        timestamp_to_hour_str = db.func.date_format(timestamp_with_tz, '%Y-%m-%d %H:00')
    else:
        timestamp_to_hour_str = db.func.strftime(
            '%Y-%m-%d %H:00', 
            db.func.datetime(RequestLog.timestamp, '+8 hours')
        )

    if dialect_name == 'sqlite':
        timestamp_to_date_obj = db.func.date(
            db.func.datetime(RequestLog.timestamp, '+8 hours')
        )
    elif dialect_name == 'mysql':
        timestamp_with_tz = db.func.date_add(RequestLog.timestamp, text('INTERVAL 8 HOUR'))
        timestamp_to_date_obj = db.func.date(timestamp_with_tz)
    else:
        timestamp_to_date_obj = db.func.date(
            db.func.datetime(RequestLog.timestamp, '+8 hours')
        )

    # --- 鍏煎鎬т慨鏀圭粨鏉?---


    hours_query = db.session.query(
        timestamp_to_hour_str.label('hour')
    ).distinct().order_by(db.desc('hour'))
    
    hours = hours_query.all()

    selected_hour = request.args.get('hour', hours[0].hour if hours else None)

    hourly_query = db.session.query(
        timestamp_to_hour_str.label('hour'),
        RequestLog.username,
        RequestLog.ip,
        RequestLog.endpoint,
        db.func.sum(db.case((RequestLog.status == 'success', 1), else_=0)).label('success_count'),
        db.func.sum(db.case((RequestLog.status == 'failed', 1), else_=0)).label('fail_count'),
        db.func.sum(db.case((RequestLog.status == 'rate_limited', 1), else_=0)).label('rate_limited_count'),
        db.func.count().label('total_count')
    )

    # **********************************************************
    # *********** 杩欓噷鏄叧閿殑淇 ***********
    # 灏嗚繃婊ゆ潯浠朵粠 HAVING 绉昏嚦 WHERE (filter)
    if selected_hour:
        hourly_query = hourly_query.filter(
            timestamp_to_hour_str == selected_hour
        )
    # **********************************************************

    hourly_stats = hourly_query.group_by('hour', RequestLog.username, RequestLog.ip, RequestLog.endpoint).all()
    
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
            timestamp_to_hour_str == selected_hour
        )

    duration_stats = duration_query.group_by('duration_range').all()

    sorted_duration_stats = sorted(duration_stats, key=lambda x: (
        int(x.duration_range.split('-')[0].rstrip('s')) if x.duration_range != '90s+' else 90
    ))

    date_filter_info = {
        'start_date': None,
        'end_date': None,
        'error': False
    }

    if selected_hour:
        try:
            end_date_str = selected_hour.split()[0]
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
            start_date = end_date - timedelta(days=6)
            
            date_filter_info['start_date'] = start_date.strftime("%Y-%m-%d")
            date_filter_info['end_date'] = end_date_str
        except Exception as e:
            date_filter_info['error'] = True
            print(f"鏃ユ湡瑙ｆ瀽閿欒: {str(e)}")

    daily_query = db.session.query(
        timestamp_to_date_obj.label('day'),
        db.func.sum(db.case((RequestLog.status == 'success', 1), else_=0)).label('daily_success'),
        db.func.sum(db.case((RequestLog.status == 'failed', 1), else_=0)).label('daily_fail'),
        db.func.sum(db.case((RequestLog.status == 'rate_limited', 1), else_=0)).label('daily_rate_limited'),
        db.func.count().label('daily_total')
    )

    if not date_filter_info['error'] and date_filter_info['start_date']:
        daily_query = daily_query.filter(
            timestamp_to_date_obj.between(
                date_filter_info['start_date'],
                date_filter_info['end_date']
            )
        )
    else:
        default_end = datetime.now()
        default_start = default_end - timedelta(days=6)
        daily_query = daily_query.filter(
            timestamp_to_date_obj.between(
                default_start.strftime("%Y-%m-%d"),
                default_end.strftime("%Y-%m-%d")
            )
        )

    daily_query = daily_query.group_by('day').order_by(db.desc('day'))
    daily_stats = daily_query.all()

    total_success = sum(stat.success_count for stat in hourly_stats)
    total_fail = sum(stat.fail_count for stat in hourly_stats)
    total_rate_limited = sum(stat.rate_limited_count for stat in hourly_stats)
    total_count = total_success + total_fail + total_rate_limited
    
    return render_template('stats.html',
                         date_filter_info=date_filter_info,
                         daily_stats=daily_stats,
                         total_success=total_success,
                         total_fail=total_fail,
                         total_rate_limited=total_rate_limited,
                         hours=[h.hour for h in hours],
                         selected_hour=selected_hour,
                         hourly_stats=hourly_stats,
                         duration_stats=sorted_duration_stats,
                         total_count=total_count)

# 鐢ㄦ埛绠＄悊椤甸潰
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
        expiration_date = datetime.strptime(request.form['expiration_date'], '%Y-%m-%d') if request.form['expiration_date'] else None
        is_active = request.form.get('is_active', 'false') == 'on'
        if is_update :
            user_base=User.query.filter_by(username=new_username).first()
            user_base.is_admin=new_is_admin
            if expiration_date is not None and expiration_date != "":user_base.expiration_date = expiration_date
            user_base.is_active = is_active
            if new_password is not None and new_password != "":user_base.password=new_password
            if new_limit_rule is not None and new_limit_rule != "":user_base.limit_rule=new_limit_rule
            logger.info(f"user update: {new_username}|{new_limit_rule}|{expiration_date}")  
        else:
            if new_password is None or new_password =="":flash('Please enter password')
            new_limit_rule = new_limit_rule if new_limit_rule is not None and new_limit_rule != "" else "10000/day;1000/hour"
            hashed_password = generate_password_hash(new_password)
            new_user = User(
                username=new_username, 
                password=hashed_password, 
                is_admin=new_is_admin,
                limit_rule=new_limit_rule,
                expiration_date=expiration_date,
                is_active=is_active
                )
            logger.info(f"user edit: {new_username}|{new_limit_rule}|{expiration_date}")
            db.session.add(new_user)
        db.session.commit()
        flash('User added/updated successfully')
    users = User.query.all()
    return render_template('manage_users.html', users=users)

# 娉ㄩ攢
@app.route('/ui/logout')
def logout_ui():
    session.pop('user_id', None)
    return redirect(url_for('login_ui'))
@app.route('/ui/deleteUser', methods=['POST'])
def delete_user_ui():
    if 'user_id' not in session:
        return redirect(url_for('logout_ui'))
    logger.info(request.form['id'])
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



# 娉ㄥ唽鎺ュ彛
@app.route('/manageapi/register', methods=['POST'])
@jwt_required()
def register():
    
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    current_user = get_jwt_identity()
    logger.info(f"/register {username},damin: {current_user}")
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

# 鍒犻櫎鎺ュ彛
@app.route('/manageapi/deleteUser', methods=['POST'])
@jwt_required()
def deleteUser():
    
    data = request.get_json()
    username = data.get('username')
    current_user = get_jwt_identity()
    logger.info(f"/register {username},damin: {current_user}")
    if not username :
        return jsonify({'message': 'Missing username or password'}), 400

    if not User.query.filter_by(username=username).first():
        return jsonify({'message': 'Username not exist'}), 400
    
    User.query.filter_by(username=username).delete()

    return jsonify({'message': 'User deleted successfully'}), 201

# 鐧诲綍鎺ュ彛
@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    logger.info(f"/login {username}")
    if not username or not password:
        return jsonify({'message': 'Missing username or password'}), 400

    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password, password):
        if not user.is_active:
            return jsonify({'message': '鐢ㄦ埛宸茶绂佺敤'}), 403
        access_token = create_access_token(identity=username)
        return jsonify({'message': 'Login successful', 'access_token': access_token}), 200
    else:
        return jsonify({'message': 'Invalid credentials'}), 401
# 鐧诲綍鎺ュ彛
@app.route('/api/latestVersionInfo', methods=['GET'])
def latestVersionInfo():
    logger.info(f"/latestVersionInfo")
    if latestVersion and packageBaseURL:
        return jsonify({'version': latestVersion, 'packgeURL': packageBaseURL+latestVersion+packageType}), 200
    else:
        return jsonify({'message': 'version not defined'}),460

# 璇煶璇嗗埆
@app.route('/api/whisper/transcriptions', methods=['POST'])
@log_request
@dynamic_limit
def whisper_transcriptions():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/whisper/transcriptions user:{current_user} id:{id}")
    file=request.files['file']

    audiofile=file.stream.read()
    res=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='zh')
    text=res.text
    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
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


# 璇煶璇嗗埆
@app.route('/api/whisper/translations', methods=['POST'])
@log_request
@dynamic_limit
def whisper_translations():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/whisper/translations user:{current_user} id:{id}")
    file=request.files['file']

    audiofile=file.stream.read()
    res=whisperclient.audio.translations.create(model=model, file=audiofile,language='zh')
    text=res.text
    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
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
         logger.info(f"缈昏瘧API鍝嶅簲閿欒: {str(e)}")
 
    # 娣诲姞閿欒澶勭悊鍜屾棩蹇楄褰?    if 'translatedText' in result:
        return result['translatedText']
    else:
        # 鎵撳嵃閿欒淇℃伅鍜屽畬鏁寸殑API鍝嶅簲
        logger.info(f"缈昏瘧API鍝嶅簲閿欒: {result}")
        return text  # 濡傛灉缈昏瘧澶辫触锛岃繑鍥炲師鏂?# 缈昏瘧
@app.route('/api/libreTranslate', methods=['POST'])
@log_request
@dynamic_limit
def libreTranslate():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/libreTranslate user:{current_user} id:{id}")
    data = request.get_json()
    source = data.get('source')
    target = data.get('target')
    text = data.get('text')
    res=translate_local(text,source,target)
    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': res}), 200

# 缈昏瘧
@app.route('/api/func/translateToEnglish', methods=['POST'])
@log_request
@dynamic_limit
def translate():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/func/translateToEnglish user:{current_user} id:{id}")
    file=request.files['file']
    audiofile=file.stream.read()

    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='zh')
    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
    if(text.text in errorFilter["errorResultDict"]) or any(errorKey in text.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    if enable_web_translators :
        translatedText=do_translate(text.text, from_='zh',to="en")
    else:
        translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
    return jsonify({'text': text.text,'translatedText': translatedText if enable_web_translators else translatedText.text}), 200

# 澶氳瑷€缈昏瘧
@app.route('/api/func/translateToOtherLanguage', methods=['POST'])
@log_request
@dynamic_limit
def translateToOtherLanguage():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/func/translateToOtherLanguage user:{current_user} id:{id}")
    global supportedLanguagesList
    init_supportedLanguagesList()
    file=request.files['file']
    params=request.form.to_dict()
    targetLanguage=params["targetLanguage"]
    logger.info(f"targetLanguage:{targetLanguage}")
    if targetLanguage not in supportedLanguagesList:
        return jsonify({'message':f"targetLanguage error,please use following languages:{str(supportedLanguagesList)}"}), 401
    audiofile=file.stream.read()
    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language='zh')
    if(text.text in errorFilter["errorResultDict"]) or any(errorKey in text.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    
    if enable_web_translators and libretranslate_to_baidu[targetLanguage]:
        transText=do_translate(text.text, from_='zh',to=targetLanguage)
    else:
        translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
        if targetLanguage =='en':transText=translatedText.text
        else:transText=translate_local(translatedText.text,"en",targetLanguage)
    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': text.text,'translatedText':transText}), 200

# 澶氳瑷€缈昏瘧
@app.route('/api/func/multitranslateToOtherLanguage', methods=['POST'])
@log_request
@dynamic_limit
def multitranslateToOtherLanguage():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/func/multitranslateToOtherLanguage user:{current_user} id:{id}")
    global supportedLanguagesList
    init_supportedLanguagesList()
    file=request.files['file']
    params=request.form.to_dict()
    targetLanguage=params["targetLanguage"]
    sourceLanguage=params["sourceLanguage"]
    targetLanguage2=params.get("targetLanguage2","none")
    targetLanguage3=params.get("targetLanguage3","none")
    emojiOutput=params.get("emojiOutput",'true')
    transText=''
    transText2=''
    transText3=''
    logger.info(f"targetLanguage:{targetLanguage}, sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
        return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    if targetLanguage not in supportedLanguagesList:
        return jsonify({'message':f"targetLanguage error,please use following languages:{str(supportedLanguagesList)}"}), 401
    
    if file.mimetype not in ['audio/wav','audio/opus']:
        return jsonify({'error': f'闈炴硶鏂囦欢绫诲瀷: {audio_file.mimetype}'}), 400
    audiofile=file.stream.read()
    if file.mimetype=='audio/opus':
        audiofile=packaged_opus_stream_to_wav_bytes(audiofile,16000)
    if sourceLanguage=='zh':
        response=requests.post(url=sensevoice_url,files={'file':audiofile})
        text = response.json()
        
        if emojiOutput=='true': text['text']=emoji.replace_emoji(text['text'], replace='')
        if  text['text']=='銆? or text['text']=='': return jsonify({'text': "",'message':"filtered"}), 200
        
    else:
        filterText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language="zh")
        if filterText.text == '': return jsonify({'text': "",'message':"filtered"}), 200
        if(filterText.text in errorFilter["errorResultDict"]) or any(errorKey in filterText.text for errorKey in errorFilter["errorKeyString"]):
            return jsonify({'text': "",'message':"filtered"}), 200
        text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=sourceLanguage)
    stext=text.text if sourceLanguage!='zh' else text['text']
    logger.info(id+': '+stext)
    
    if enable_web_translators:
        try:
            translateSourceLanguage=sourceLanguage
            transText=do_translate(stext, from_=translateSourceLanguage,to=targetLanguage)
            if targetLanguage2 != "none": transText2=do_translate(stext, from_=translateSourceLanguage,to=targetLanguage2)
            if targetLanguage3 != "none": transText3=do_translate(stext, from_=translateSourceLanguage,to=targetLanguage3)
        except Exception as e:
            logger.error(f"error:{traceback.format_exc()}")
        # return openai_translate(stext,targetLanguage,targetLanguage2,targetLanguage3), 200
    else:
        translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
        if targetLanguage =='en':transText=translatedText.text
        else:
            transText=translate_local(translatedText.text,"en",targetLanguage)
        if targetLanguage2 != "none": transText2=translate_local(translatedText.text,'en',targetLanguage2)
        if targetLanguage3 != "none": transText3=translate_local(translatedText.text,'en',targetLanguage3)
    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text':stext,'translatedText':transText,'translatedText2':transText2,'translatedText3':transText3}), 200




# 澶氳瑷€缈昏瘧

VLLM_SERVER_URL = "http://192.168.2.104:8005" # Or your vLLM server address
VLLMMODEL_NAME = "qwenOmni7" # Replace with your actual model name if different
VLLMAPI_KEY = "sk-VkxlIMJf6KLHpI8MN"


class TranslateTexts(BaseModel):
    text: str
    translatedText: str
    translatedText2: str
    translatedText3: str

json_schema = TranslateTexts.model_json_schema()
@app.route('/api/func/vllmTest', methods=['POST'])
@log_request
@dynamic_limit
def vllmTest():
    result=None
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/func/vllmTest user:{current_user} id:{id}")
    global supportedLanguagesList
    init_supportedLanguagesList()
    file=request.files['file']
    params=request.form.to_dict()
    targetLanguage=params["targetLanguage"]
    sourceLanguage=params["sourceLanguage"]
    targetLanguage2=params.get("targetLanguage2", 'none')
    targetLanguage3=params.get("targetLanguage3",'none')
    system_prompt_content = f"""浣犳槸涓€涓珮绾х殑璇煶澶勭悊鍔╂墜銆備綘鐨勪换鍔℃槸锛?1.棣栧厛灏嗛煶棰戝唴瀹硅浆褰曟垚鍏跺師濮嬭瑷€鐨勬枃鏈€?2. 灏嗚浆褰曠殑鏂囨湰缈昏瘧鎴恵codeTochinese[sourceLanguage]}銆?3. 灏嗚浆褰曠殑鏂囨湰缈昏瘧鎴恵codeTochinese[targetLanguage]}銆?"""
    seps=[codeTochinese[sourceLanguage]+':',codeTochinese[targetLanguage]+':']
    if targetLanguage2!= 'none':
        seps.append(codeTochinese[targetLanguage2]+':')
        system_prompt_content+=f"4. 灏嗚浆褰曠殑鏂囨湰缈昏瘧鎴恵codeTochinese[targetLanguage2]}\n"
    if targetLanguage3!='none':
        seps.append(codeTochinese[targetLanguage3]+':')
        system_prompt_content+=f"5. 灏嗚浆褰曠殑鏂囨湰缈昏瘧鎴恵codeTochinese[targetLanguage3]}\n"
    system_prompt_content+="璇锋寜鐓т互涓嬫牸寮忔竻鏅板湴缁勭粐浣犵殑杈撳嚭锛歕n"
    system_prompt_content+='{"鍘熸枃":"鍘熷璇█鏂囨湰",'
    system_prompt_content+=f'"{codeTochinese[sourceLanguage]}":"{codeTochinese[sourceLanguage]}鏂囨湰",'
    system_prompt_content+=f'"{codeTochinese[targetLanguage]}":"{codeTochinese[targetLanguage]}鏂囨湰"'
    if targetLanguage2!= 'none':system_prompt_content+=f',"{codeTochinese[targetLanguage2]}":"{codeTochinese[targetLanguage2]}鏂囨湰"'
    if targetLanguage3!= 'none':system_prompt_content+=f',"{codeTochinese[targetLanguage3]}":"{codeTochinese[targetLanguage3]}鏂囨湰"'
    system_prompt_content+='}\n'
    logger.info(f"targetLanguage:{targetLanguage}, sourceLanguage:{sourceLanguage}")

    binary_data=file.stream.read()
    base64_encoded_data = base64.b64encode(binary_data)
    base64_string = base64_encoded_data.decode('utf-8')

    try:
        client = OpenAI(
            api_key=VLLMAPI_KEY,
            base_url=f"{VLLM_SERVER_URL}/v1"
        )

        messages = []
        messages.append({"role": "system", "content": system_prompt_content})

        user_content = []
        user_content.append({"type": "text", "text": "璇峰鐞嗕笅闈㈢殑闊抽銆?})

        user_content.append({
            "type": "input_audio",
            "input_audio": {
                "data": base64_string,
                "format": 'wav'
            },
        })
        messages.append({"role": "user", "content": user_content})

        print(f"\nSending request to: {client.base_url}chat/completions")

        print("\nSending API request...")
        start_time = time.monotonic()

        chat_completion = client.chat.completions.create(
            model=VLLMMODEL_NAME,
            messages=messages, # Send the original, unmodified messages
            max_tokens=500,
            temperature=0,
        )

        end_time = time.monotonic()
        duration = end_time - start_time
        logger.info(f"API call completed in {duration:.2f} seconds.")

        # print("\n--- Model Response ---")
        message_content = None
        if chat_completion.choices and len(chat_completion.choices) > 0:
            message_content = chat_completion.choices[0].message.content
            remaining=message_content
            logger.info(message_content)
            try:
                datattt=json.loads(remaining)
                result={
                'text':datattt[codeTochinese[sourceLanguage]],
                'translatedText':datattt[codeTochinese[targetLanguage]],
                'translatedText2':datattt[codeTochinese[targetLanguage2]] if targetLanguage2!= 'none' else '',
                'translatedText3':datattt[codeTochinese[targetLanguage3]] if targetLanguage3!= 'none' else ''
                }
            except :
                result={
                'text':'',
                'translatedText':'',
                'translatedText2':'',
                'translatedText3':''
                }

            # text1=message_content.split()
            # values = ['']*4
            # lang_dict = {}
            # translations_map = {}
            # for line in remaining.splitlines():
            #     if ':' in line:  #纭繚琛屼腑鏈夊啋鍙峰彲浠ュ垎鍓?            #         # split(':', 1) 鍙湪绗竴涓啋鍙峰鍒嗗壊
            #         parts = line.split(':', 1)
            #         key_from_s = parts[0].strip() # 鎻愬彇閿苟鍘婚櫎棣栧熬绌烘牸
            #         value_from_s = parts[1]       # 鎻愬彇鍊硷紝淇濈暀鍐掑彿鍚庣殑鎵€鏈夊唴瀹癸紙鍖呮嫭娼滃湪鐨勫墠瀵肩┖鏍硷級
            #         translations_map[key_from_s] = value_from_s

            # 2. 鎸?keys 鍒楄〃椤哄簭鎻愬彇鍊?            # ordered_translations = []
            # for key in keys:
            #     if key in translations_map:
            #         ordered_translations.append(translations_map[key])
            #     else:
                    # 濡傛灉鏌愪釜key鍦╯涓壘涓嶅埌锛屽彲浠ユ牴鎹渶瑕佸喅瀹氬浣曞鐞?                    # 渚嬪锛屾坊鍔犱竴涓狽one鎴栬€呯┖瀛楃涓诧紝鎴栬€呮姏鍑洪敊璇?                    # print(f"璀﹀憡: 鏈湪瀛楃涓瞫涓壘鍒伴敭 '{key}'")
                    # pass # 褰撳墠闇€姹備笅锛屽亣璁緆eys涓殑閿兘鑳藉湪s涓壘鍒?
            et=time.time()
        else:
            print("No valid choice found in response:")
            print(chat_completion)
            return jsonify({'text':"server Error: No valid choice found in response:"}), 501
        
       

    except Exception as e:
        print(f"\n--- Error ---")
        error_message = f"An API call error occurred: {e}"
        print(error_message)
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status code: {e.response.status_code}")
            try:
                error_message += f" - Response: {e.response.json()}"
                print(f"Response content: {e.response.json()}")
            except ValueError: # If response content is not JSON
                error_message += f" - Response: {e.response.text}"
                print(f"Response content: {e.response.text}")
        return jsonify({"duration": time.monotonic() - start_time if 'start_time' in locals() else 0, "content": None, "error": error_message}),500
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': result.get("text"),
    'translatedText':result.get("translatedText"),
    'translatedText2':result.get("translatedText2",''),
    'translatedText3':result.get("translatedText3",'')}), 200
# 澶氳瑷€缈昏瘧
@app.route('/api/func/doubleTransciption', methods=['POST'])
@log_request
@dynamic_limit
def doubleTransciption():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/func/doubleTranscibe user:{current_user} id:{id}")
    file=request.files['file']
    params=request.form.to_dict()
    targetLanguage=params["targetLanguage"]
    sourceLanguage=params["sourceLanguage"]
    logger.info(f"targetLanguage:{targetLanguage}, sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
         return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    if targetLanguage not in whisperSupportedLanguageList:
        return jsonify({'message':f"targetLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    if file.mimetype not in ['audio/wav','audio/opus']:
        return jsonify({'error': f'闈炴硶鏂囦欢绫诲瀷: {audio_file.mimetype}'}), 400
    audiofile=file.stream.read()
    if file.mimetype=='audio/opus':
        audiofile=packaged_opus_stream_to_wav_bytes(audiofile,16000)
    filterText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language="zh")
    if(filterText.text in errorFilter["errorResultDict"]) or any(errorKey in filterText.text for errorKey in errorFilter["errorKeyString"]):
        return jsonify({'text': "",'message':"filtered"}), 200
    text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=sourceLanguage)
    transText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=targetLanguage)
    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': text.text,'translatedText':transText.text,'zhText':filterText.text}), 200


# 澶氳瑷€缈昏瘧
@app.route('/api/whisper/multitranscription', methods=['POST'])
@log_request
@dynamic_limit
def multitranscription():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/api/whisper/multitranscription user:{current_user} id:{id}")
    file=request.files['file']
    params=request.form.to_dict()
    sourceLanguage=params["sourceLanguage"]
    emojiOutput=params.get("emojiOutput",'true')
    logger.info(f"sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
        return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    if file.mimetype not in ['audio/wav','audio/opus']:
        return jsonify({'error': f'闈炴硶鏂囦欢绫诲瀷: {audio_file.mimetype}'}), 400
    audiofile=file.stream.read()
    if file.mimetype=='audio/opus':
        audiofile=packaged_opus_stream_to_wav_bytes(audiofile,16000)
    if sourceLanguage!='zh':
        filterText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language="zh")
        if(filterText.text in errorFilter["errorResultDict"]) or any(errorKey in filterText.text for errorKey in errorFilter["errorKeyString"]):
            return jsonify({'text': "",'message':"filtered"}), 200
        text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=sourceLanguage)
    else:
        response=requests.post(url=sensevoice_url,files={'file':audiofile})
        text = response.json()
        if emojiOutput=='true': text['text']=emoji.replace_emoji(text['text'], replace='')
        if  text['text']=='銆? or text['text']=='': return jsonify({'text': "",'message':"filtered"}), 200

    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': text['text'] if sourceLanguage=='zh' else text.text}), 200


@app.route('/api/func/tts', methods=['POST'])
@log_request
@dynamic_limit
def tts_proxy():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/api/func/tts user:{current_user} id:{id}")
    try:
        # 鑾峰彇璇锋眰鍙傛暟
        data = request.get_json()
        text = data.get('input')
        voice = data.get('voice')
        speed = data.get('speed')
        logger.info(f"{text},{voice},{speed}")
        # 楠岃瘉蹇呰鍙傛暟
        if not text or not voice or not speed:
            
            return jsonify({"error": "Missing required parameters: input or voice or speed"}), 400
        # if not voice:return jsonify({"error": "Unsupported language"}), 400

        # 鏋勯€犺姹傚弬鏁?        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "speed": speed
        }
        logger.info(f"{ttsUrl}:{ttsToken}")
        # 鍙戣捣杞彂璇锋眰
        response = requests.post(
            ttsUrl,
            json=payload,
            headers={ 
                "Authorization": f"Bearer {ttsToken}"
            },
            stream=True
        )

        # 娴佸紡杩斿洖鍝嶅簲
        def generate():
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    yield chunk
        et=time.time()
        logger.info(f"user:{current_user} id:{id} time:{et-st}")
        return Response(
            generate(),
            content_type=response.headers['Content-Type'],
            status=response.status_code
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/func/webtranslate', methods=['POST'])
@log_request
@dynamic_limit
def web_translate():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    logger.info(f"/api/func/web_translate user:{current_user} id:{id}")
    # 鑾峰彇璇锋眰鍙傛暟
    data = request.get_json()
    text = data.get('text')
    targetLanguage=data.get("targetLanguage")
    sourceLanguage=data.get("sourceLanguage")
    targetLanguage2=data.get("targetLanguage2","none")
    targetLanguage3=data.get("targetLanguage3","none")
    transText=''
    transText2=''
    transText3=''
    # 楠岃瘉蹇呰鍙傛暟
    if not text or not targetLanguage or not sourceLanguage:
        
        return jsonify({"error": "Missing required parameters: input or voice or speed"}), 400
    # if not voice:return jsonify({"error": "Unsupported language"}), 400
    transText=do_translate(text,sourceLanguage,targetLanguage)
    if targetLanguage2 != "none": transText2=do_translate(text, from_=sourceLanguage,to=targetLanguage2)
    if targetLanguage3 != "none": transText3=do_translate(text, from_=sourceLanguage,to=targetLanguage3)
        
    et=time.time()
    logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text':text,'translatedText':transText,'translatedText2':transText2,'translatedText3':transText3}), 200

def packaged_opus_stream_to_wav_bytes(
    packaged_opus_data: bytes,
    sample_rate: int,
    channels: int,
    frame_duration: int = 20, # 蹇呴』涓庣紪鐮佹椂浣跨敤鐨勫抚鏃堕暱鍖归厤
    sample_width: int = 2     # WAV 鏂囦欢涓瘡涓牱鏈殑瀛楄妭鏁?(渚嬪 2 瀵瑰簲 16-bit)
) -> bytes:
    """
    灏嗗甫闀垮害鍓嶇紑鐨?Opus 鍖呭瓧鑺傛祦瑙ｇ爜涓?WAV 闊抽鏁版嵁瀛楄妭娴併€?
    鍙傛暟:
        packaged_opus_data (bytes): 鍖呭惈澶氫釜[闀垮害+Opus鍖匽搴忓垪鐨勫崟涓€瀛楄妭娴併€?        sample_rate (int): Opus 鏁版嵁鐨勯噰鏍风巼銆?        channels (int): Opus 鏁版嵁鐨勯€氶亾鏁般€?        frame_duration (int): Opus 缂栫爜鏃朵娇鐢ㄧ殑甯ф椂闀匡紝鍗曚綅姣銆?                                姝ゅ弬鏁扮敤浜庤绠楄В鐮佸櫒鏈熸湜鐨勬瘡甯ф牱鏈暟銆?        sample_width (int): WAV 鏂囦欢涓瘡涓牱鏈殑瀛楄妭鏁?(閫氬父涓?2)銆?
    杩斿洖:
        bytes: WAV 鏍煎紡鐨勯煶棰戞暟鎹€傚鏋滆В鐮佸け璐ユ垨鏃犳湁鏁堟暟鎹紝鍙兘杩斿洖绌哄瓧鑺備覆鎴栭儴鍒嗚В鐮佹暟鎹€?    """
    if not packaged_opus_data:
        return b''
    if channels not in [1, 2]:
        raise ValueError("閫氶亾鏁板繀椤绘槸 1 鎴?2")
    if sample_rate not in [8000, 12000, 16000, 24000, 48000]:
        raise ValueError("涓嶆敮鎸佺殑閲囨牱鐜囥€?)

    try:
        decoder = opuslib.Decoder(sample_rate, channels)
    except opuslib.OpusError as e:
        raise RuntimeError(f"鍒涘缓 Opus 瑙ｇ爜鍣ㄥけ璐? {e}")

    # 瑙ｇ爜鍣ㄦ湡鏈涚殑姣忓抚锛堟瘡涓狾pus鍖呰В鐮佸悗锛夌殑姣忛€氶亾鏍锋湰鏁?    samples_per_packet_frame = int(sample_rate * frame_duration / 1000)

    all_decoded_pcm = bytearray()
    offset = 0
    while offset < len(packaged_opus_data):
        if offset + 4 > len(packaged_opus_data):
            print(f"璀﹀憡: 鏁版嵁涓嶈冻浠ヨ鍙栧寘闀垮害锛屽墿浣欏瓧鑺? {len(packaged_opus_data) - offset}")
            break
        
        try:
            packet_len = struct.unpack('>I', packaged_opus_data[offset:offset+4])[0]
        except struct.error as e:
            print(f"璀﹀憡: 瑙ｆ瀽鍖呴暱搴﹀け璐? {e}. 鍋忕Щ閲? {offset}")
            break # 鏃犳硶缁х画瑙ｆ瀽
            
        offset += 4

        if offset + packet_len > len(packaged_opus_data):
            print(f"璀﹀憡: 鏁版嵁涓嶈冻浠ヨ鍙栧畬鏁寸殑 Opus 鍖呫€傛湡鏈涢暱搴? {packet_len}, 鍓╀綑瀛楄妭: {len(packaged_opus_data) - offset}")
            break
        
        opus_packet = packaged_opus_data[offset:offset+packet_len]
        offset += packet_len

        if not opus_packet:
            print("璀﹀憡: 璇诲彇鍒颁竴涓┖鐨?Opus 鍖咃紝璺宠繃銆?)
            continue

        try:
            # samples_per_packet_frame 鏄В鐮佸櫒浠庤繖涓寘鏈熸湜瑙ｇ爜鍑虹殑姣忛€氶亾鏍锋湰鏁?            decoded_pcm = decoder.decode(opus_packet, samples_per_packet_frame)
            all_decoded_pcm.extend(decoded_pcm)
        except opuslib.OpusError as e:
            print(f"璀﹀憡: Opus 瑙ｇ爜閿欒 (璺宠繃鍖?: {e}. 鍖呴暱搴? {len(opus_packet)}")
            # 鍙互閫夋嫨鎻掑叆闈欓煶绛夐敊璇殣钘忕瓥鐣?            # num_silent_samples = samples_per_packet_frame * channels
            # silent_frame = b'\x00' * (num_silent_samples * sample_width)
            # all_decoded_pcm.extend(silent_frame)
        except Exception as e:
            print(f"璀﹀憡: 瑙ｇ爜杩囩▼涓彂鐢熸湭鐭ラ敊璇?(璺宠繃鍖?: {e}")


    if not all_decoded_pcm:
        return b''

    # 鍒涘缓 WAV 鏂囦欢澶村苟鍐欏叆鏁版嵁
    wav_buffer = BytesIO()
    try:
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width) # 閫氬父鏄?2 (16-bit)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(all_decoded_pcm)) # [10]
    except wave.Error as e:
        raise RuntimeError(f"鍒涘缓 WAV 鏂囦欢澶辫触: {e}")
    except Exception as e:
        raise RuntimeError(f"鍐欏叆 WAV 鏁版嵁鏃跺彂鐢熸湭鐭ラ敊璇? {e}")


    return wav_buffer.getvalue()

whisper_to_baidu = {
    'af': 'afr',       # 闃块潪鍒╁牚鏂
    'am': 'amh',       # 闃垮鍝堟媺璇?    'ar': 'ara',       # 闃挎媺浼
    'as': 'asm',       # 闃胯惃濮嗚
    'az': 'aze',       # 闃垮鎷滅枂璇?    'ba': 'bak',       # 宸翠粈鍩哄皵璇?    'be': 'bel',       # 鐧戒縿缃楁柉璇?    'bg': 'bul',       # 淇濆姞鍒╀簹璇?    'bn': 'ben',       # 瀛熷姞鎷夎
    'bo': None,       # 钘忚锛堢櫨搴?API 涓病鏈夊搴旂殑浠ｇ爜锛?    'br': 'bre',       # 甯冨垪濉斿凹璇?    'bs': 'bos',       # 娉㈡柉灏间簹璇?    'ca': 'cat',       # 鍔犳嘲缃楀凹浜氳
    'cs': 'cs',        # 鎹峰厠璇?    'cy': 'gle',       # 濞佸皵澹
    'da': 'dan',       # 涓归害璇?    'de': 'de',        # 寰疯
    'el': 'el',        # 甯岃厞璇?    'en': 'en',        # 鑻辫
    'es': 'spa',       # 瑗跨彮鐗欒
    'et': 'est',       # 鐖辨矙灏间簹璇?    'eu': 'baq',       # 宸存柉鍏嬭
    'fa': 'per',       # 娉㈡柉璇?    'fi': 'fin',       # 鑺叞璇?    'fo': 'fao',       # 娉曠綏璇?    'fr': 'fra',       # 娉曡
    'gl': 'glg',       # 鍔犲埄瑗夸簹璇?    'gu': 'guj',       # 鍙ゅ悏鎷夌壒璇?    'ha': 'hau',       # 璞惃璇?    'haw': 'haw',      # 澶忓▉澶疯
    'he': 'heb',       # 甯屼集鏉ヨ
    'hi': 'hi',        # 鍗板湴璇?    'hr': 'hrv',       # 鍏嬬綏鍦颁簹璇?    'ht': 'hat',       # 娴峰湴鍏嬮噷濂ュ皵璇?    'hu': 'hu',        # 鍖堢墮鍒╄
    'hy': 'arm',       # 浜氱編灏间簹璇?    'id': 'id',        # 鍗板凹璇?    'is': 'ice',       # 鍐板矝璇?    'it': 'it',        # 鎰忓ぇ鍒╄
    'ja': 'jp',        # 鏃ヨ
    'jw': 'jav',       # 鐖搰璇?    'ka': 'geo',       # 鏍奸瞾鍚変簹璇?    'kk': None,       # 鍝堣惃鍏嬭锛堢櫨搴?API 涓病鏈夊搴旂殑浠ｇ爜锛?    'km': 'hkm',       # 楂樻璇?    'kn': 'kan',       # 鍗＄撼杈捐
    'ko': 'kor',       # 闊╄
    'la': 'lat',       # 鎷変竵璇?    'lb': 'ltz',       # 鍗㈡．鍫¤
    'ln': 'lin',       # 鏋楀姞鎷夎
    'lo': 'lao',       # 鑰佹対璇?    'lt': 'lit',       # 绔嬮櫠瀹涜
    'lv': 'lav',       # 鎷夎劚缁翠簹璇?    'mg': 'mg',        # 椹揪鍔犳柉鍔犺
    'mi': None,       # 姣涘埄璇紙鐧惧害 API 涓病鏈夊搴旂殑浠ｇ爜锛?    'mk': 'mac',       # 椹叾椤胯
    'ml': 'mal',       # 椹媺闆呮媺濮嗚
    'mn': None,       # 钂欏彜璇紙鐧惧害 API 涓病鏈夊搴旂殑浠ｇ爜锛?    'mr': 'mar',       # 椹媺鍦拌
    'ms': 'may',       # 椹潵璇?    'mt': 'mlt',       # 椹€充粬璇?    'my': 'bur',       # 缂呯敻璇?    'ne': 'nep',       # 灏兼硦灏旇
    'nl': 'nl',        # 鑽峰叞璇?    'nn': 'nno',       # 鏂版尓濞佽
    'no': 'nor',       # 鎸▉璇?    'oc': 'oci',       # 濂ュ厠璇?    'pa': 'pan',       # 鏃侀伄鏅
    'pl': 'pl',        # 娉㈠叞璇?    'ps': 'pus',       # 鏅粈鍥捐
    'pt': 'pt',        # 钁¤悇鐗欒
    'ro': 'rom',       # 缃楅┈灏间簹璇?    'ru': 'ru',        # 淇勮
    'sa': 'san',       # 姊佃
    'sd': 'snd',       # 淇″痉璇?    'si': 'sin',       # 鍍т冀缃楄
    'sk': 'sk',        # 鏂礇浼愬厠璇?    'sl': 'slo',       # 鏂礇鏂囧凹浜氳
    'sn': 'sna',       # 淇撼璇?    'so': 'som',       # 绱㈤┈閲岃
    'sq': 'alb',       # 闃垮皵宸村凹浜氳
    'sr': 'srp',       # 濉炲皵缁翠簹璇?    'su': 'sun',       # 宸戒粬璇?    'sv': 'swe',       # 鐟炲吀璇?    'sw': 'swa',       # 鏂摝甯岄噷璇?    'ta': 'tam',       # 娉扮背灏旇
    'te': 'tel',       # 娉板崲鍥鸿
    'tg': 'tgk',       # 濉斿悏鍏嬭
    'th': 'th',        # 娉拌
    'tk': 'tuk',       # 鍦熷簱鏇艰
    'tl': 'tgl',       # 浠栧姞绂勮
    'tr': 'tr',        # 鍦熻€冲叾璇?    'tt': 'tat',       # 闉戦澕璇?    'uk': 'ukr',       # 涔屽厠鍏拌
    'ur': 'urd',       # 涔屽皵閮借
    'uz': None,       # 涔屽吂鍒厠璇紙鐧惧害 API 涓病鏈夊搴旂殑浠ｇ爜锛?    'vi': 'vie',       # 瓒婂崡璇?    'yi': 'yid',       # 鎰忕缁
    'yo': 'yor',       # 绾﹂瞾宸磋
    'yue': 'yue',     # 绮よ
    'zh': 'zh',        # 绠€浣撲腑鏂?    'zt': 'cht'        # 绻佷綋涓枃
}
libretranslate_to_baidu = {
    'ar': 'ara',       # 闃挎媺浼
    'az': 'aze',       # 闃垮鎷滅枂璇?    'bg': 'bul',       # 淇濆姞鍒╀簹璇?    'bn': 'ben',       # 瀛熷姞鎷夎
    'ca': 'cat',       # 鍔犳嘲缃楀凹浜氳
    'cs': 'cs',        # 鎹峰厠璇?    'da': 'dan',       # 涓归害璇?    'de': 'de',        # 寰疯
    'el': 'el',        # 甯岃厞璇?    'en': 'en',        # 鑻辫
    'eo': None,       # 涓栫晫璇紙鐧惧害 API 涓病鏈夊搴旂殑浠ｇ爜锛?    'es': 'spa',       # 瑗跨彮鐗欒
    'et': 'est',       # 鐖辨矙灏间簹璇?    'eu': 'baq',       # 宸存柉鍏嬭
    'fa': 'per',       # 娉㈡柉璇?    'fi': 'fin',       # 鑺叞璇?    'fr': 'fra',       # 娉曡
    'ga': 'gle',       # 鐖卞皵鍏拌
    'gl': 'glg',       # 鍔犲埄瑗夸簹璇?    'he': 'heb',       # 甯屼集鏉ヨ
    'hi': 'hi',        # 鍗板湴璇?    'hu': 'hu',        # 鍖堢墮鍒╄
    'id': 'id',        # 鍗板凹璇?    'it': 'it',        # 鎰忓ぇ鍒╄
    'ja': 'jp',        # 鏃ヨ
    'ko': 'kor',       # 闊╄
    'lt': 'lit',       # 绔嬮櫠瀹涜
    'lv': 'lav',       # 鎷夎劚缁翠簹璇?    'ms': 'may',       # 椹潵璇?    'nb': 'nob',       # 鎸▉璇紙涔﹂潰鎸▉璇級
    'nl': 'nl',        # 鑽峰叞璇?    'pl': 'pl',        # 娉㈠叞璇?    'pt': 'pt',        # 钁¤悇鐗欒
    'ro': 'rom',       # 缃楅┈灏间簹璇?    'ru': 'ru',        # 淇勮
    'sk': 'sk',        # 鏂礇浼愬厠璇?    'sl': 'slo',       # 鏂礇鏂囧凹浜氳
    'sq': 'alb',       # 闃垮皵宸村凹浜氳
    'sr': 'srp',       # 濉炲皵缁翠簹璇?    'sv': 'swe',       # 鐟炲吀璇?    'th': 'th',        # 娉拌
    'tl': 'tgl',       # 濉斿姞娲涜
    'tr': 'tr',        # 鍦熻€冲叾璇?    'uk': 'ukr',       # 涔屽厠鍏拌
    'ur': 'urd',       # 涔屽皵閮借
    'vi': 'vie',       # 瓒婂崡璇?    'zh': 'zh',        # 涓枃
    'zt': 'cht'        # 绻佷綋涓枃
}
codeTochinese={
    'af': '闃块潪鍒╁牚鏂',
    'am': '闃垮鍝堟媺璇?,
    'ar': '闃挎媺浼',
    'as': '闃胯惃濮嗚',
    'az': '闃垮鎷滅枂璇?,
    'ba': '宸翠粈鍩哄皵璇?,
    'be': '鐧戒縿缃楁柉璇?,
    'bg': '淇濆姞鍒╀簹璇?,
    'bn': '瀛熷姞鎷夎',
    'bo': '钘忚',
    'br': '甯冨垪濉斿凹璇?,
    'bs': '娉㈡柉灏间簹璇?,
    'ca': '鍔犳嘲缃楀凹浜氳',
    'cs': '鎹峰厠璇?,
    'cy': '濞佸皵澹',
    'da': '涓归害璇?,
    'de': '寰疯',
    'el': '甯岃厞璇?,
    'en': '鑻辫',
    'es': '瑗跨彮鐗欒',
    'et': '鐖辨矙灏间簹璇?,
    'eu': '宸存柉鍏嬭',
    'fa': '娉㈡柉璇?,
    'fi': '鑺叞璇?,
    'fo': '娉曠綏璇?,
    'fr': '娉曡',
    'gl': '鍔犲埄瑗夸簹璇?,
    'gu': '鍙ゅ悏鎷夌壒璇?,
    'ha': '璞惃璇?,
    'haw': '澶忓▉澶疯',
    'he': '甯屼集鏉ヨ',
    'hi': '鍗板湴璇?,
    'hr': '鍏嬬綏鍦颁簹璇?,
    'ht': '娴峰湴鍏嬮噷濂ュ皵璇?,
    'hu': '鍖堢墮鍒╄',
    'hy': '浜氱編灏间簹璇?,
    'id': '鍗板凹璇?,
    'is': '鍐板矝璇?,
    'it': '鎰忓ぇ鍒╄',
    'ja': '鏃ヨ',
    'jw': '鐖搰璇?,
    'ka': '鏍奸瞾鍚変簹璇?,
    'kk': '鍝堣惃鍏嬭',
    'km': '楂樻璇?,
    'kn': '鍗＄撼杈捐',
    'ko': '闊╄',
    'la': '鎷変竵璇?,
    'lb': '鍗㈡．鍫¤',
    'ln': '鏋楀姞鎷夎',
    'lo': '鑰佹対璇?,
    'lt': '绔嬮櫠瀹涜',
    'lv': '鎷夎劚缁翠簹璇?,
    'mg': '椹揪鍔犳柉鍔犺',
    'mi': '姣涘埄璇?,
    'mk': '椹叾椤胯',
    'ml': '椹媺闆呮媺濮嗚',
    'mn': '钂欏彜璇?,
    'mr': '椹媺鍦拌',
    'ms': '椹潵璇?,
    'mt': '椹€充粬璇?,
    'my': '缂呯敻璇?,
    'ne': '灏兼硦灏旇',
    'nl': '鑽峰叞璇?,
    'nn': '鏂版尓濞佽',
    'no': '鎸▉璇?,
    'oc': '濂ュ厠璇?,
    'pa': '鏃侀伄鏅',
    'pl': '娉㈠叞璇?,
    'ps': '鏅粈鍥捐',
    'pt': '钁¤悇鐗欒',
    'ro': '缃楅┈灏间簹璇?,
    'ru': '淇勮',
    'sa': '姊佃',
    'sd': '淇″痉璇?,
    'si': '鍍т冀缃楄',
    'sk': '鏂礇浼愬厠璇?,
    'sl': '鏂礇鏂囧凹浜氳',
    'sn': '淇撼璇?,
    'so': '绱㈤┈閲岃',
    'sq': '闃垮皵宸村凹浜氳',
    'sr': '濉炲皵缁翠簹璇?,
    'su': '宸戒粬璇?,
    'sv': '鐟炲吀璇?,
    'sw': '鏂摝甯岄噷璇?,
    'ta': '娉扮背灏旇',
    'te': '娉板崲鍥鸿',
    'tg': '濉斿悏鍏嬭',
    'th': '娉拌',
    'tk': '鍦熷簱鏇艰',
    'tl': '浠栧姞绂勮',
    'tr': '鍦熻€冲叾璇?,
    'tt': '闉戦澕璇?,
    'uk': '涔屽厠鍏拌',
    'ur': '涔屽皵閮借',
    'uz': '涔屽吂鍒厠璇?,
    'vi': '瓒婂崡璇?,
    'yi': '鎰忕缁',
    'yo': '绾﹂瞾宸磋',
    'yue': '绮よ',
    'zh': '绠€浣撲腑鏂?,
    'zt': '绻佷綋涓枃',
    'eo': '涓栫晫璇?,  # 鏉ヨ嚜libretranslate
    'ga': '鐖卞皵鍏拌',  # 鏉ヨ嚜libretranslate
    'nb': '鎸▉璇?  # 鏉ヨ嚜libretranslate
}
transalte_zt={
    'bing':'zh-Hant',
    'baidu':'cht',
    'alibaba':'zh-TW',
    'sogou':'zh-CHT',
    'iciba':'cnt',
    'itranslate':'zh-TW',
    'papago':'zh-TW'
}
def packaged_opus_stream_to_wav_bytes(
    packaged_opus_data: bytes,
    sample_rate: int,
    channels: int =1,
    frame_duration: int = 20, # 蹇呴』涓庣紪鐮佹椂浣跨敤鐨勫抚鏃堕暱鍖归厤
    sample_width: int = 2     # WAV 鏂囦欢涓瘡涓牱鏈殑瀛楄妭鏁?(渚嬪 2 瀵瑰簲 16-bit)
) -> bytes:
    """
    灏嗗甫闀垮害鍓嶇紑鐨?Opus 鍖呭瓧鑺傛祦瑙ｇ爜涓?WAV 闊抽鏁版嵁瀛楄妭娴併€?
    鍙傛暟:
        packaged_opus_data (bytes): 鍖呭惈澶氫釜[闀垮害+Opus鍖匽搴忓垪鐨勫崟涓€瀛楄妭娴併€?        sample_rate (int): Opus 鏁版嵁鐨勯噰鏍风巼銆?        channels (int): Opus 鏁版嵁鐨勯€氶亾鏁般€?        frame_duration (int): Opus 缂栫爜鏃朵娇鐢ㄧ殑甯ф椂闀匡紝鍗曚綅姣銆?                                姝ゅ弬鏁扮敤浜庤绠楄В鐮佸櫒鏈熸湜鐨勬瘡甯ф牱鏈暟銆?        sample_width (int): WAV 鏂囦欢涓瘡涓牱鏈殑瀛楄妭鏁?(閫氬父涓?2)銆?
    杩斿洖:
        bytes: WAV 鏍煎紡鐨勯煶棰戞暟鎹€傚鏋滆В鐮佸け璐ユ垨鏃犳湁鏁堟暟鎹紝鍙兘杩斿洖绌哄瓧鑺備覆鎴栭儴鍒嗚В鐮佹暟鎹€?    """
    if not packaged_opus_data:
        return b''
    if channels not in [1, 2]:
        raise ValueError("閫氶亾鏁板繀椤绘槸 1 鎴?2")
    if sample_rate not in [8000, 12000, 16000, 24000, 48000]:
        raise ValueError("涓嶆敮鎸佺殑閲囨牱鐜囥€?)

    try:
        decoder = opuslib.Decoder(sample_rate, channels)
    except opuslib.OpusError as e:
        raise RuntimeError(f"鍒涘缓 Opus 瑙ｇ爜鍣ㄥけ璐? {e}")

    # 瑙ｇ爜鍣ㄦ湡鏈涚殑姣忓抚锛堟瘡涓狾pus鍖呰В鐮佸悗锛夌殑姣忛€氶亾鏍锋湰鏁?    samples_per_packet_frame = int(sample_rate * frame_duration / 1000)

    all_decoded_pcm = bytearray()
    offset = 0
    while offset < len(packaged_opus_data):
        if offset + 4 > len(packaged_opus_data):
            print(f"璀﹀憡: 鏁版嵁涓嶈冻浠ヨ鍙栧寘闀垮害锛屽墿浣欏瓧鑺? {len(packaged_opus_data) - offset}")
            break
        
        try:
            packet_len = struct.unpack('>I', packaged_opus_data[offset:offset+4])[0]
        except struct.error as e:
            print(f"璀﹀憡: 瑙ｆ瀽鍖呴暱搴﹀け璐? {e}. 鍋忕Щ閲? {offset}")
            break # 鏃犳硶缁х画瑙ｆ瀽
            
        offset += 4

        if offset + packet_len > len(packaged_opus_data):
            print(f"璀﹀憡: 鏁版嵁涓嶈冻浠ヨ鍙栧畬鏁寸殑 Opus 鍖呫€傛湡鏈涢暱搴? {packet_len}, 鍓╀綑瀛楄妭: {len(packaged_opus_data) - offset}")
            break
        
        opus_packet = packaged_opus_data[offset:offset+packet_len]
        offset += packet_len

        if not opus_packet:
            print("璀﹀憡: 璇诲彇鍒颁竴涓┖鐨?Opus 鍖咃紝璺宠繃銆?)
            continue

        try:
            # samples_per_packet_frame 鏄В鐮佸櫒浠庤繖涓寘鏈熸湜瑙ｇ爜鍑虹殑姣忛€氶亾鏍锋湰鏁?            decoded_pcm = decoder.decode(opus_packet, samples_per_packet_frame)
            all_decoded_pcm.extend(decoded_pcm)
        except opuslib.OpusError as e:
            print(f"璀﹀憡: Opus 瑙ｇ爜閿欒 (璺宠繃鍖?: {e}. 鍖呴暱搴? {len(opus_packet)}")
            # 鍙互閫夋嫨鎻掑叆闈欓煶绛夐敊璇殣钘忕瓥鐣?            # num_silent_samples = samples_per_packet_frame * channels
            # silent_frame = b'\x00' * (num_silent_samples * sample_width)
            # all_decoded_pcm.extend(silent_frame)
        except Exception as e:
            print(f"璀﹀憡: 瑙ｇ爜杩囩▼涓彂鐢熸湭鐭ラ敊璇?(璺宠繃鍖?: {e}")


    if not all_decoded_pcm:
        return b''

    # 鍒涘缓 WAV 鏂囦欢澶村苟鍐欏叆鏁版嵁
    wav_buffer = BytesIO()
    try:
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width) # 閫氬父鏄?2 (16-bit)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(all_decoded_pcm)) # [10]
    except wave.Error as e:
        raise RuntimeError(f"鍒涘缓 WAV 鏂囦欢澶辫触: {e}")
    except Exception as e:
        raise RuntimeError(f"鍐欏叆 WAV 鏁版嵁鏃跺彂鐢熸湭鐭ラ敊璇? {e}")


    return wav_buffer.getvalue()
if __name__ == '__main__':
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
    app.run(debug=True,host='0.0.0.0',port=8980)
