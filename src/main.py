import time
import pytz
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy import inspect

from core.config import settings
from core.logging_config import logger
from core.services import load_filter_config, init_supported_languages
from db.base import Base, engine, SessionLocal
from db.models import User, RequestLog
from routers import api, ui, manage_api

def check_and_update_db():
    db = SessionLocal()
    try:
        inspector = inspect(engine)
        if 'user' in inspector.get_table_names():
            user_columns = [col['name'] for col in inspector.get_columns('user')]
            if 'expiration_date' not in user_columns:
                db.execute(text('ALTER TABLE user ADD COLUMN expiration_date DATETIME'))
                logger.info("Added expiration_date to user table")
            if 'is_active' not in user_columns:
                db.execute(text('ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1'))
                logger.info("Added is_active to user table")
        if 'request_log' in inspector.get_table_names():
            log_columns = [col['name'] for col in inspector.get_columns('request_log')]
            if 'status' not in log_columns:
                db.execute(text('ALTER TABLE request_log ADD COLUMN status VARCHAR(20) DEFAULT "pending"'))
                logger.info("Added status to request_log table")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"数据库升级失败: {str(e)}")
    finally:
        db.close()

def check_user_expiration():
    db = SessionLocal()
    logger.info("开始检查用户过期状态...")
    now = datetime.now(pytz.utc)
    expired_users = db.query(User).filter(User.expiration_date <= now, User.is_active == True).all()
    for user in expired_users:
        user.is_active = False
        logger.info(f"用户 {user.username} 已过期，自动禁用")
    db.commit()
    logger.info(f"用户过期检查完成，共处理 {len(expired_users)} 个过期用户")
    db.close()

scheduler = AsyncIOScheduler(timezone=pytz.utc)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("应用启动...")
    Base.metadata.create_all(bind=engine)
    check_and_update_db()
    load_filter_config()
    init_supported_languages()
    scheduler.add_job(check_user_expiration, 'cron', hour=0, minute=0, second=0)
    scheduler.start()
    logger.info("后台任务调度器已启动")
    yield
    logger.info("应用关闭...")
    scheduler.shutdown()

app = FastAPI(title="VoiceLinkVR Server", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY)

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start_time = time.time()
    log_data = {'status': 'failed'}

    # 尝试解析JWT获取用户名
    current_user = "anonymous"
    try:
        from core.dependencies import oauth2_scheme, get_db
        from jose import jwt
        db_gen = get_db()
        db = next(db_gen)
        token = await oauth2_scheme(request)
        if token:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            current_user = payload.get("sub", "anonymous")
    except Exception:
        pass # 如果token无效或不存在，则保持匿名

    # 获取IP地址
    x_real_ip = request.headers.get('x-real-ip')
    x_forwarded_for = request.headers.get('x-forwarded-for')
    if x_real_ip:
        ip = x_real_ip.split(',')[0].strip()
    elif x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"

    logger.info(f"[API-START] 用户: {current_user}, IP: {ip}, 接口: {request.url.path}, 方法: {request.method}")

    try:
        response = await call_next(request)
        log_data['duration'] = time.time() - start_time
        status_code = response.status_code
        log_data['status'] = 'success' if 200 <= status_code < 400 else 'failed'
        if status_code == 429: log_data['status'] = 'rate_limited'
        logger.info(f"[API-END] 用户: {current_user}, 接口: {request.url.path}, 状态码: {status_code}, 耗时: {log_data['duration']:.3f}s")
        return response
    except Exception as e:
        log_data['duration'] = time.time() - start_time
        log_data['status'] = 'error'
        logger.error(f"[API-ERROR] 用户: {current_user}, 接口: {request.url.path}, 错误: {e}")
        logger.exception("API处理异常:")
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
    finally:
        log = RequestLog(
            username=current_user if current_user != "anonymous" else None,
            ip=ip, endpoint=request.url.path, duration=log_data.get('duration', 0),
            status=log_data.get('status', 'failed')
        )
        try:
            db.add(log)
            db.commit()
        except Exception as db_e:
            db.rollback()
            logger.error(f"[DB-ERROR] 日志提交失败: {db_e}")
        finally:
            next(db_gen, None) # 关闭 db session

# 包含路由
app.include_router(ui.router, prefix="/ui", tags=["UI"])
app.include_router(api.router, prefix="/api", tags=["API"])
app.include_router(manage_api.router, prefix="/manageapi", tags=["Management API"])

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to VoiceLinkVR FastAPI Server!"}