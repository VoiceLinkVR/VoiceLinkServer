import functools
import pymysql
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

# 获取环境参数
whisper_host=os.getenv("WHISPER_HOST")
whisper_prot=os.getenv("WHISPER_PORT")
sensevoice_host=os.getenv("SENSEVOICE_HOST")
sensevoice_prot=os.getenv("SENSEVOICE_PORT")
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
enable_web_translators=os.getenv("ENABLE_WEB_TRANSLATORS")
translator_Service=os.getenv("TRANSLATOR_SERVICE")
redisUrl=os.getenv("LIMITER_REDIS_URL")
ttsUrl=os.getenv("TTS_URL")
ttsToken=os.getenv("TTS_TOKEN")
latestVersion=os.getenv("LATEST_VERSION")
packageBaseURL=os.getenv("PACKAGE_BASE_URL")
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
    limiter=Limiter(
        app=app,
        key_func=limit_key_func,
        default_limits=["400/hour"],
        storage_uri="memory://" if redisUrl is None else redisUrl)
    # 自定义错误处理器
    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit_exceeded(e):
        return jsonify({
        "error": "Too many request",
        "limit": str(e.limit.limit),  # 如 "2 per 1 day"
    }), 430 # 返回 403 状态码
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
    c1=f'2. 将收到的文字翻译成{codeTochinese[t2]}。' if t2!="none" else ''
    c2=f'3. 将收到的文字翻译成{codeTochinese[t3]}。' if t3!="none" else ''
    o1=f',"translatedText2":{codeTochinese[t2]}译文' if t2!="none" else ''
    o2=f',"translatedText3":{codeTochinese[t3]}译文' if t3!="none" else ''
    contenttext=f'''你是翻译助手。你的任务是：
1. 将收到的文字翻译成{codeTochinese[t1]}。
{c1}
{c2}

请严格按照如下格式仅输出JSON，不要输出Python代码或其他信息，JSON字段使用顿号【、】区隔：'''+'{'+f'"text":收到的文字,"translatedText":{codeTochinese[t1]}译文{o1}{o2}'+'}'
    app.logger.info("llm send:"+contenttext)

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
    app.logger.info("llm return:"+data)
    return data


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
from sqlalchemy import inspect

def check_and_update_db():
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            # 检查User表
            if 'user' in inspector.get_table_names():
                user_columns = [col['name'] for col in inspector.get_columns('user')]
                # 添加缺失字段
                if 'expiration_date' not in user_columns:
                    db.session.execute(text('ALTER TABLE user ADD COLUMN expiration_date DATETIME'))
                    app.logger.info("Added expiration_date to user table")
                if 'is_active' not in user_columns:
                    db.session.execute(text('ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1'))
                    app.logger.info("Added is_active to user table")

            # 检查request_log表
            if 'request_log' in inspector.get_table_names():
                log_columns = [col['name'] for col in inspector.get_columns('request_log')]
                if 'status' not in log_columns:
                    db.session.execute(text('ALTER TABLE request_log ADD COLUMN status VARCHAR(20) DEFAULT "pending"'))
                    app.session.execute(text('UPDATE request_log SET status = "success" WHERE status IS NULL'))
                    app.logger.info("Added status to request_log table")

            db.session.commit()
            
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"数据库升级失败: {str(e)}")
        finally:
            db.session.close()

# 在应用初始化后调用
check_and_update_db()


# 在应用初始化后调用
check_and_update_db()
def check_user_expiration():
    with app.app_context():
        now = datetime.now(pytz.utc)
        expired_users = User.query.filter(
            User.expiration_date <= now,
            User.is_active == True
        ).all()
        
        for user in expired_users:
            user.is_active = False
            app.logger.info(f"用户 {user.username} 已过期，自动禁用")
        
        db.session.commit()

# 配置定时任务
scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(
    func=check_user_expiration,
    trigger='cron',
    hour=0,  # 每天UTC时间0点执行
    minute=0,
    second=0
)
# scheduler.start()
# 用户模型
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    limit_rule= db.Column(db.String(100), nullable=True)
    expiration_date = db.Column(db.DateTime, nullable=True)  # 新增有效期字段
    is_active = db.Column(db.Boolean, default=True)  # 新增激活状态字段
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
        # duration=9999
        # if request.path!="/api/func/tts" and request.path!="/api/func/webtranslate":
        #     audio_file = request.files['file']
        #     try:
        #         duration = get_wav_duration_from_filestorage(audio_file)
        #     except wave.Error:
        #         app.logger.info("Invalid WAV file")
        current_user = get_jwt_identity()  # 获取当前用户的身份（通常是用户名）
        user=User.query.filter_by(username=current_user).first()
        if user:
            # if duration <6:
            #     rule=str(user.limit_rule).replace("8/minute","16/minute").replace("4/minute","8/minute")
            # else:
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
                    key_func=lambda:current_user+time.strftime("%Y-%m-%d", time.localtime()),
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

# def reset_limits():
#     """每天北京时间8点重置内存计数器"""
#     with app.app_context():
#         # 通过私有方法重置存储（注意：这是基于当前实现细节）
#         limiter.storage.reset()
#         app.logger.info("计数器已重置于北京时间8点")

# scheduler.add_job(
#     func=reset_limits,
#     trigger='cron',
#     hour=0,
#     minute=0,
#     second=0
# )
scheduler.start()

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

    # --- 兼容性修改 ---
    
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

    # --- 兼容性修改结束 ---


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
    # *********** 这里是关键的修复 ***********
    # 将过滤条件从 HAVING 移至 WHERE (filter)
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
            print(f"日期解析错误: {str(e)}")

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
        expiration_date = datetime.strptime(request.form['expiration_date'], '%Y-%m-%d') if request.form['expiration_date'] else None
        is_active = request.form.get('is_active', 'false') == 'on'
        if is_update :
            user_base=User.query.filter_by(username=new_username).first()
            user_base.is_admin=new_is_admin
            if expiration_date is not None and expiration_date != "":user_base.expiration_date = expiration_date
            user_base.is_active = is_active
            if new_password is not None and new_password != "":user_base.password=new_password
            if new_limit_rule is not None and new_limit_rule != "":user_base.limit_rule=new_limit_rule
                
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
        if not user.is_active:
            return jsonify({'message': '用户已被禁用'}), 403
        access_token = create_access_token(identity=username)
        return jsonify({'message': 'Login successful', 'access_token': access_token}), 200
    else:
        return jsonify({'message': 'Invalid credentials'}), 401
# 登录接口
@app.route('/api/latestVersionInfo', methods=['GET'])
def latestVersionInfo():
    app.logger.info(f"/latestVersionInfo")
    if latestVersion and packageBaseURL:
        return jsonify({'version': latestVersion, 'packgeURL': packageBaseURL+latestVersion+'.zip'}), 200
    else:
        return jsonify({'message': 'version not defined'}),460

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
    if enable_web_translators :
        translatedText=do_translate(text.text, from_='zh',to="en")
    else:
        translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
    return jsonify({'text': text.text,'translatedText': translatedText if enable_web_translators else translatedText.text}), 200

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
    
    if enable_web_translators and libretranslate_to_baidu[targetLanguage]:
        transText=do_translate(text.text, from_='zh',to=targetLanguage)
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
    targetLanguage2=params.get("targetLanguage2","none")
    targetLanguage3=params.get("targetLanguage3","none")
    emojiOutput=params.get("emojiOutput",'true')
    transText=''
    transText2=''
    transText3=''
    app.logger.info(f"targetLanguage:{targetLanguage}, sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
        return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    if targetLanguage not in supportedLanguagesList:
        return jsonify({'message':f"targetLanguage error,please use following languages:{str(supportedLanguagesList)}"}), 401
    
    if file.mimetype not in ['audio/wav','audio/opus']:
        return jsonify({'error': f'非法文件类型: {audio_file.mimetype}'}), 400
    audiofile=file.stream.read()
    if file.mimetype=='audio/opus':
        audiofile=packaged_opus_stream_to_wav_bytes(audiofile,16000)
    if sourceLanguage=='zh':
        response=requests.post(url=sensevoice_url,files={'file':audiofile})
        text = response.json()
        
        if emojiOutput=='true': text['text']=emoji.replace_emoji(text['text'], replace='')
        if  text['text']=='。' or text['text']=='': return jsonify({'text': "",'message':"filtered"}), 200
        
    else:
        filterText=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language="zh")
        if(filterText.text in errorFilter["errorResultDict"]) or any(errorKey in filterText.text for errorKey in errorFilter["errorKeyString"]):
            return jsonify({'text': "",'message':"filtered"}), 200
        text=whisperclient.audio.transcriptions.create(model=model, file=audiofile,language=sourceLanguage)
    stext=text.text if sourceLanguage!='zh' else text['text']
    app.logger.info(id+': '+stext)
    
    if enable_web_translators:
        try:
            translateSourceLanguage='auto'if sourceLanguage=='zh'else sourceLanguage
            transText=do_translate(stext, from_=translateSourceLanguage,to=targetLanguage)
            if targetLanguage2 != "none": transText2=do_translate(stext, from_=translateSourceLanguage,to=targetLanguage2)
            if targetLanguage3 != "none": transText3=do_translate(stext, from_=translateSourceLanguage,to=targetLanguage3)
        except Exception as e:
            app.logger.error(f"error:{traceback.format_exc()}")
        # return openai_translate(stext,targetLanguage,targetLanguage2,targetLanguage3), 200
    else:
        translatedText=whisperclient.audio.translations.create(model=model, file=audiofile)
        if targetLanguage =='en':transText=translatedText.text
        else:
            transText=translate_local(translatedText.text,"en",targetLanguage)
        if targetLanguage2 != "none": transText2=translate_local(translatedText.text,'en',targetLanguage2)
        if targetLanguage3 != "none": transText3=translate_local(translatedText.text,'en',targetLanguage3)
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text':stext,'translatedText':transText,'translatedText2':transText2,'translatedText3':transText3}), 200




# 多语言翻译

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
    app.logger.info(f"/func/vllmTest user:{current_user} id:{id}")
    global supportedLanguagesList
    init_supportedLanguagesList()
    file=request.files['file']
    params=request.form.to_dict()
    targetLanguage=params["targetLanguage"]
    sourceLanguage=params["sourceLanguage"]
    targetLanguage2=params.get("targetLanguage2", 'none')
    targetLanguage3=params.get("targetLanguage3",'none')
    system_prompt_content = f"""你是一个高级的语音处理助手。你的任务是：
1.首先将音频内容转录成其原始语言的文本。
2. 将转录的文本翻译成{codeTochinese[sourceLanguage]}。
3. 将转录的文本翻译成{codeTochinese[targetLanguage]}。
"""
    seps=[codeTochinese[sourceLanguage]+':',codeTochinese[targetLanguage]+':']
    if targetLanguage2!= 'none':
        seps.append(codeTochinese[targetLanguage2]+':')
        system_prompt_content+=f"4. 将转录的文本翻译成{codeTochinese[targetLanguage2]}\n"
    if targetLanguage3!='none':
        seps.append(codeTochinese[targetLanguage3]+':')
        system_prompt_content+=f"5. 将转录的文本翻译成{codeTochinese[targetLanguage3]}\n"
    system_prompt_content+="请按照以下格式清晰地组织你的输出：\n"
    system_prompt_content+='{"原文":"原始语言文本",'
    system_prompt_content+=f'"{codeTochinese[sourceLanguage]}":"{codeTochinese[sourceLanguage]}文本",'
    system_prompt_content+=f'"{codeTochinese[targetLanguage]}":"{codeTochinese[targetLanguage]}文本"'
    if targetLanguage2!= 'none':system_prompt_content+=f',"{codeTochinese[targetLanguage2]}":"{codeTochinese[targetLanguage2]}文本"'
    if targetLanguage3!= 'none':system_prompt_content+=f',"{codeTochinese[targetLanguage3]}":"{codeTochinese[targetLanguage3]}文本"'
    system_prompt_content+='}\n'
    app.logger.info(f"targetLanguage:{targetLanguage}, sourceLanguage:{sourceLanguage}")

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
        user_content.append({"type": "text", "text": "请处理下面的音频。"})

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
        app.logger.info(f"API call completed in {duration:.2f} seconds.")

        # print("\n--- Model Response ---")
        message_content = None
        if chat_completion.choices and len(chat_completion.choices) > 0:
            message_content = chat_completion.choices[0].message.content
            remaining=message_content
            app.logger.info(message_content)
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
            #     if ':' in line:  #确保行中有冒号可以分割
            #         # split(':', 1) 只在第一个冒号处分割
            #         parts = line.split(':', 1)
            #         key_from_s = parts[0].strip() # 提取键并去除首尾空格
            #         value_from_s = parts[1]       # 提取值，保留冒号后的所有内容（包括潜在的前导空格）
            #         translations_map[key_from_s] = value_from_s

            # 2. 按 keys 列表顺序提取值
            # ordered_translations = []
            # for key in keys:
            #     if key in translations_map:
            #         ordered_translations.append(translations_map[key])
            #     else:
                    # 如果某个key在s中找不到，可以根据需要决定如何处理
                    # 例如，添加一个None或者空字符串，或者抛出错误
                    # print(f"警告: 未在字符串s中找到键 '{key}'")
                    # pass # 当前需求下，假设keys中的键都能在s中找到

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
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': result.get("text"),
    'translatedText':result.get("translatedText"),
    'translatedText2':result.get("translatedText2",''),
    'translatedText3':result.get("translatedText3",'')}), 200
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
    if file.mimetype not in ['audio/wav','audio/opus']:
        return jsonify({'error': f'非法文件类型: {audio_file.mimetype}'}), 400
    audiofile=file.stream.read()
    if file.mimetype=='audio/opus':
        audiofile=packaged_opus_stream_to_wav_bytes(audiofile,16000)
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
    emojiOutput=params.get("emojiOutput",'true')
    app.logger.info(f"sourceLanguage:{sourceLanguage}")
    if sourceLanguage not in whisperSupportedLanguageList:
        return jsonify({'message':f"sourceLanguage error,please use following languages:{str(whisperSupportedLanguageList)}"}), 401
    if file.mimetype not in ['audio/wav','audio/opus']:
        return jsonify({'error': f'非法文件类型: {audio_file.mimetype}'}), 400
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
        if  text['text']=='。' or text['text']=='': return jsonify({'text': "",'message':"filtered"}), 200

    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text': text['text'] if sourceLanguage=='zh' else text.text}), 200


@app.route('/api/func/tts', methods=['POST'])
@log_request
@dynamic_limit
def tts_proxy():
    current_user = get_jwt_identity()
    id=str(uuid.uuid4())
    st=time.time()
    app.logger.info(f"/api/func/tts user:{current_user} id:{id}")
    try:
        # 获取请求参数
        data = request.get_json()
        text = data.get('input')
        voice = data.get('voice')
        speed = data.get('speed')
        app.logger.info(f"{text},{voice},{speed}")
        # 验证必要参数
        if not text or not voice or not speed:
            
            return jsonify({"error": "Missing required parameters: input or voice or speed"}), 400
        # if not voice:return jsonify({"error": "Unsupported language"}), 400

        # 构造请求参数
        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "speed": speed
        }
        app.logger.info(f"{ttsUrl}:{ttsToken}")
        # 发起转发请求
        response = requests.post(
            ttsUrl,
            json=payload,
            headers={ 
                "Authorization": f"Bearer {ttsToken}"
            },
            stream=True
        )

        # 流式返回响应
        def generate():
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    yield chunk
        et=time.time()
        app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
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
    app.logger.info(f"/api/func/web_translate user:{current_user} id:{id}")
    # 获取请求参数
    data = request.get_json()
    text = data.get('text')
    targetLanguage=data.get("targetLanguage")
    sourceLanguage=data.get("sourceLanguage")
    targetLanguage2=data.get("targetLanguage2","none")
    targetLanguage3=data.get("targetLanguage3","none")
    transText=''
    transText2=''
    transText3=''
    # 验证必要参数
    if not text or not targetLanguage or not sourceLanguage:
        
        return jsonify({"error": "Missing required parameters: input or voice or speed"}), 400
    # if not voice:return jsonify({"error": "Unsupported language"}), 400
    transText=do_translate(text,sourceLanguage,targetLanguage)
    if targetLanguage2 != "none": transText2=do_translate(text, from_=sourceLanguage,to=targetLanguage2)
    if targetLanguage3 != "none": transText3=do_translate(text, from_=sourceLanguage,to=targetLanguage3)
        
    et=time.time()
    app.logger.info(f"user:{current_user} id:{id} time:{et-st}")
    return jsonify({'text':text,'translatedText':transText,'translatedText2':transText2,'translatedText3':transText3}), 200

def packaged_opus_stream_to_wav_bytes(
    packaged_opus_data: bytes,
    sample_rate: int,
    channels: int,
    frame_duration: int = 20, # 必须与编码时使用的帧时长匹配
    sample_width: int = 2     # WAV 文件中每个样本的字节数 (例如 2 对应 16-bit)
) -> bytes:
    """
    将带长度前缀的 Opus 包字节流解码为 WAV 音频数据字节流。

    参数:
        packaged_opus_data (bytes): 包含多个[长度+Opus包]序列的单一字节流。
        sample_rate (int): Opus 数据的采样率。
        channels (int): Opus 数据的通道数。
        frame_duration (int): Opus 编码时使用的帧时长，单位毫秒。
                                此参数用于计算解码器期望的每帧样本数。
        sample_width (int): WAV 文件中每个样本的字节数 (通常为 2)。

    返回:
        bytes: WAV 格式的音频数据。如果解码失败或无有效数据，可能返回空字节串或部分解码数据。
    """
    if not packaged_opus_data:
        return b''
    if channels not in [1, 2]:
        raise ValueError("通道数必须是 1 或 2")
    if sample_rate not in [8000, 12000, 16000, 24000, 48000]:
        raise ValueError("不支持的采样率。")

    try:
        decoder = opuslib.Decoder(sample_rate, channels)
    except opuslib.OpusError as e:
        raise RuntimeError(f"创建 Opus 解码器失败: {e}")

    # 解码器期望的每帧（每个Opus包解码后）的每通道样本数
    samples_per_packet_frame = int(sample_rate * frame_duration / 1000)

    all_decoded_pcm = bytearray()
    offset = 0
    while offset < len(packaged_opus_data):
        if offset + 4 > len(packaged_opus_data):
            print(f"警告: 数据不足以读取包长度，剩余字节: {len(packaged_opus_data) - offset}")
            break
        
        try:
            packet_len = struct.unpack('>I', packaged_opus_data[offset:offset+4])[0]
        except struct.error as e:
            print(f"警告: 解析包长度失败: {e}. 偏移量: {offset}")
            break # 无法继续解析
            
        offset += 4

        if offset + packet_len > len(packaged_opus_data):
            print(f"警告: 数据不足以读取完整的 Opus 包。期望长度: {packet_len}, 剩余字节: {len(packaged_opus_data) - offset}")
            break
        
        opus_packet = packaged_opus_data[offset:offset+packet_len]
        offset += packet_len

        if not opus_packet:
            print("警告: 读取到一个空的 Opus 包，跳过。")
            continue

        try:
            # samples_per_packet_frame 是解码器从这个包期望解码出的每通道样本数
            decoded_pcm = decoder.decode(opus_packet, samples_per_packet_frame)
            all_decoded_pcm.extend(decoded_pcm)
        except opuslib.OpusError as e:
            print(f"警告: Opus 解码错误 (跳过包): {e}. 包长度: {len(opus_packet)}")
            # 可以选择插入静音等错误隐藏策略
            # num_silent_samples = samples_per_packet_frame * channels
            # silent_frame = b'\x00' * (num_silent_samples * sample_width)
            # all_decoded_pcm.extend(silent_frame)
        except Exception as e:
            print(f"警告: 解码过程中发生未知错误 (跳过包): {e}")


    if not all_decoded_pcm:
        return b''

    # 创建 WAV 文件头并写入数据
    wav_buffer = BytesIO()
    try:
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width) # 通常是 2 (16-bit)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(all_decoded_pcm)) # [10]
    except wave.Error as e:
        raise RuntimeError(f"创建 WAV 文件失败: {e}")
    except Exception as e:
        raise RuntimeError(f"写入 WAV 数据时发生未知错误: {e}")


    return wav_buffer.getvalue()

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
codeTochinese={
    'af': '阿非利堪斯语',
    'am': '阿姆哈拉语',
    'ar': '阿拉伯语',
    'as': '阿萨姆语',
    'az': '阿塞拜疆语',
    'ba': '巴什基尔语',
    'be': '白俄罗斯语',
    'bg': '保加利亚语',
    'bn': '孟加拉语',
    'bo': '藏语',
    'br': '布列塔尼语',
    'bs': '波斯尼亚语',
    'ca': '加泰罗尼亚语',
    'cs': '捷克语',
    'cy': '威尔士语',
    'da': '丹麦语',
    'de': '德语',
    'el': '希腊语',
    'en': '英语',
    'es': '西班牙语',
    'et': '爱沙尼亚语',
    'eu': '巴斯克语',
    'fa': '波斯语',
    'fi': '芬兰语',
    'fo': '法罗语',
    'fr': '法语',
    'gl': '加利西亚语',
    'gu': '古吉拉特语',
    'ha': '豪萨语',
    'haw': '夏威夷语',
    'he': '希伯来语',
    'hi': '印地语',
    'hr': '克罗地亚语',
    'ht': '海地克里奥尔语',
    'hu': '匈牙利语',
    'hy': '亚美尼亚语',
    'id': '印尼语',
    'is': '冰岛语',
    'it': '意大利语',
    'ja': '日语',
    'jw': '爪哇语',
    'ka': '格鲁吉亚语',
    'kk': '哈萨克语',
    'km': '高棉语',
    'kn': '卡纳达语',
    'ko': '韩语',
    'la': '拉丁语',
    'lb': '卢森堡语',
    'ln': '林加拉语',
    'lo': '老挝语',
    'lt': '立陶宛语',
    'lv': '拉脱维亚语',
    'mg': '马达加斯加语',
    'mi': '毛利语',
    'mk': '马其顿语',
    'ml': '马拉雅拉姆语',
    'mn': '蒙古语',
    'mr': '马拉地语',
    'ms': '马来语',
    'mt': '马耳他语',
    'my': '缅甸语',
    'ne': '尼泊尔语',
    'nl': '荷兰语',
    'nn': '新挪威语',
    'no': '挪威语',
    'oc': '奥克语',
    'pa': '旁遮普语',
    'pl': '波兰语',
    'ps': '普什图语',
    'pt': '葡萄牙语',
    'ro': '罗马尼亚语',
    'ru': '俄语',
    'sa': '梵语',
    'sd': '信德语',
    'si': '僧伽罗语',
    'sk': '斯洛伐克语',
    'sl': '斯洛文尼亚语',
    'sn': '修纳语',
    'so': '索马里语',
    'sq': '阿尔巴尼亚语',
    'sr': '塞尔维亚语',
    'su': '巽他语',
    'sv': '瑞典语',
    'sw': '斯瓦希里语',
    'ta': '泰米尔语',
    'te': '泰卢固语',
    'tg': '塔吉克语',
    'th': '泰语',
    'tk': '土库曼语',
    'tl': '他加禄语',
    'tr': '土耳其语',
    'tt': '鞑靼语',
    'uk': '乌克兰语',
    'ur': '乌尔都语',
    'uz': '乌兹别克语',
    'vi': '越南语',
    'yi': '意第绪语',
    'yo': '约鲁巴语',
    'yue': '粤语',
    'zh': '简体中文',
    'zt': '繁体中文',
    'eo': '世界语',  # 来自libretranslate
    'ga': '爱尔兰语',  # 来自libretranslate
    'nb': '挪威语'  # 来自libretranslate
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
    frame_duration: int = 20, # 必须与编码时使用的帧时长匹配
    sample_width: int = 2     # WAV 文件中每个样本的字节数 (例如 2 对应 16-bit)
) -> bytes:
    """
    将带长度前缀的 Opus 包字节流解码为 WAV 音频数据字节流。

    参数:
        packaged_opus_data (bytes): 包含多个[长度+Opus包]序列的单一字节流。
        sample_rate (int): Opus 数据的采样率。
        channels (int): Opus 数据的通道数。
        frame_duration (int): Opus 编码时使用的帧时长，单位毫秒。
                                此参数用于计算解码器期望的每帧样本数。
        sample_width (int): WAV 文件中每个样本的字节数 (通常为 2)。

    返回:
        bytes: WAV 格式的音频数据。如果解码失败或无有效数据，可能返回空字节串或部分解码数据。
    """
    if not packaged_opus_data:
        return b''
    if channels not in [1, 2]:
        raise ValueError("通道数必须是 1 或 2")
    if sample_rate not in [8000, 12000, 16000, 24000, 48000]:
        raise ValueError("不支持的采样率。")

    try:
        decoder = opuslib.Decoder(sample_rate, channels)
    except opuslib.OpusError as e:
        raise RuntimeError(f"创建 Opus 解码器失败: {e}")

    # 解码器期望的每帧（每个Opus包解码后）的每通道样本数
    samples_per_packet_frame = int(sample_rate * frame_duration / 1000)

    all_decoded_pcm = bytearray()
    offset = 0
    while offset < len(packaged_opus_data):
        if offset + 4 > len(packaged_opus_data):
            print(f"警告: 数据不足以读取包长度，剩余字节: {len(packaged_opus_data) - offset}")
            break
        
        try:
            packet_len = struct.unpack('>I', packaged_opus_data[offset:offset+4])[0]
        except struct.error as e:
            print(f"警告: 解析包长度失败: {e}. 偏移量: {offset}")
            break # 无法继续解析
            
        offset += 4

        if offset + packet_len > len(packaged_opus_data):
            print(f"警告: 数据不足以读取完整的 Opus 包。期望长度: {packet_len}, 剩余字节: {len(packaged_opus_data) - offset}")
            break
        
        opus_packet = packaged_opus_data[offset:offset+packet_len]
        offset += packet_len

        if not opus_packet:
            print("警告: 读取到一个空的 Opus 包，跳过。")
            continue

        try:
            # samples_per_packet_frame 是解码器从这个包期望解码出的每通道样本数
            decoded_pcm = decoder.decode(opus_packet, samples_per_packet_frame)
            all_decoded_pcm.extend(decoded_pcm)
        except opuslib.OpusError as e:
            print(f"警告: Opus 解码错误 (跳过包): {e}. 包长度: {len(opus_packet)}")
            # 可以选择插入静音等错误隐藏策略
            # num_silent_samples = samples_per_packet_frame * channels
            # silent_frame = b'\x00' * (num_silent_samples * sample_width)
            # all_decoded_pcm.extend(silent_frame)
        except Exception as e:
            print(f"警告: 解码过程中发生未知错误 (跳过包): {e}")


    if not all_decoded_pcm:
        return b''

    # 创建 WAV 文件头并写入数据
    wav_buffer = BytesIO()
    try:
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width) # 通常是 2 (16-bit)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(all_decoded_pcm)) # [10]
    except wave.Error as e:
        raise RuntimeError(f"创建 WAV 文件失败: {e}")
    except Exception as e:
        raise RuntimeError(f"写入 WAV 数据时发生未知错误: {e}")


    return wav_buffer.getvalue()
if __name__ == '__main__':
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
    app.run(debug=True,host='0.0.0.0',port=8980)

