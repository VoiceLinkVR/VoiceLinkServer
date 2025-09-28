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
from jose import jwt
from core.config import settings
from core.logging_config import logger
from core.services import load_filter_config, update_filter_config, init_supported_languages
from core.rate_limiter import rate_limiter, RateLimitExceeded, get_client_ip
from core.dependencies import oauth2_scheme, get_db
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
    # 添加每周一凌晨3点检查filter配置更新的任务
    scheduler.add_job(update_filter_config, 'cron', day_of_week=0, hour=3, minute=0, second=0)
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
    request.state.rate_limit_contexts = []

    client_ip = get_client_ip(request)
    current_user = "anonymous"

    db_gen = None
    db = None
    response = None
    response_status_code = 500
    rate_limit_triggered = False

    try:
        db_gen = get_db()
        db = next(db_gen)
    except Exception as db_exc:
        logger.error(f"[DB-ERROR] 获取会话失败: {db_exc}")

    try:
        token = await oauth2_scheme(request)
        if token and db:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            username = payload.get("sub")
            if username:
                user = db.query(User).filter(User.username == username).first()
                if user:
                    current_user = user.username
        logger.info(f"[API-START] 用户: {current_user}, IP: {client_ip}, 接口: {request.url.path}, 方法: {request.method}")

        default_context = rate_limiter.create_default_context(request)
        if default_context:
            default_context.check()
            request.state.rate_limit_contexts.append(default_context)

        response = await call_next(request)
        response_status_code = response.status_code
        log_data['duration'] = time.time() - start_time
        if response_status_code == 429 or response_status_code == 430:
            log_data['status'] = 'rate_limited'
        elif 200 <= response_status_code < 400:
            log_data['status'] = 'success'
        else:
            log_data['status'] = 'failed'
        logger.info(f"[API-END] 用户: {current_user}, 接口: {request.url.path}, 状态码: {response_status_code}, 耗时: {log_data['duration']:.3f}s")

    except RateLimitExceeded as limit_exc:
        rate_limit_triggered = True
        response_status_code = 430
        log_data['duration'] = time.time() - start_time
        log_data['status'] = 'rate_limited'
        logger.warning(
            f"[RATE-LIMIT] 用户: {current_user}, IP: {client_ip}, "
            f"全局规则: {limit_exc.limit}, 触发规则: {limit_exc.triggered_limit}"
        )
        response = JSONResponse(
            status_code=430,
            content={
                "error": "Too many request",
                "LimitRules": limit_exc.limit,  # 原始多条规则
                "limit": limit_exc.triggered_limit  # 真正超限的那一条
            },
        )

    except Exception as e:
        response_status_code = 500
        log_data['duration'] = time.time() - start_time
        log_data['status'] = 'error'
        logger.error(f"[API-ERROR] 用户: {current_user}, 接口: {request.url.path}, 错误: {e}")
        logger.exception("API处理异常:")
        response = JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    finally:
        if rate_limiter.enabled and not rate_limit_triggered:
            status_for_limits = response_status_code or 500
            for context in getattr(request.state, "rate_limit_contexts", []):
                if context.should_deduct(status_for_limits):
                    context.commit()

        session_for_log = db or SessionLocal()
        try:
            log = RequestLog(
                username=current_user if current_user != "anonymous" else None,
                ip=client_ip,
                endpoint=request.url.path,
                duration=log_data.get('duration', 0),
                status=log_data.get('status', 'failed')
            )
            session_for_log.add(log)
            session_for_log.commit()
        except Exception as db_e:  # pylint: disable=broad-except
            session_for_log.rollback()
            logger.error(f"[DB-ERROR] 日志提交失败: {db_e}")
        finally:
            if db is None:
                session_for_log.close()
            if db_gen is not None:
                db_gen.close()

    return response

# 包含路由
app.include_router(ui.router, prefix="/ui", tags=["UI"])
app.include_router(api.router, prefix="/api", tags=["API"])
app.include_router(manage_api.router, prefix="/manageapi", tags=["Management API"])

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to VoiceLinkVR FastAPI Server!"}