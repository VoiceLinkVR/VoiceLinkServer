import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from jose import jwt
from sqlalchemy import inspect, text
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings
from core.dependencies import get_db, oauth2_scheme
from core.logging_config import logger, reset_request_id, set_request_id
from core.quota_service import commit_quota_allocation, is_new_quota_mode_enabled
from core.rate_limiter import RateLimitExceeded, get_client_ip, rate_limiter
from core.services import init_supported_languages, load_filter_config, update_filter_config
from db.base import Base, SessionLocal, engine
from db.models import PaymentOrder, RequestLog, User
from routers import api, manage_api, ui, user_center


def _ensure_password_column_capacity(db, inspector) -> None:
    password_column = next(
        (col for col in inspector.get_columns("user") if col.get("name") == "password"),
        None,
    )
    password_length = getattr(password_column.get("type"), "length", None) if password_column else None
    if password_length is None or password_length >= 255:
        return

    dialect_name = inspector.bind.dialect.name
    if dialect_name == "mysql":
        db.execute(text("ALTER TABLE user MODIFY COLUMN password VARCHAR(255) NOT NULL"))
        logger.info("Expanded user.password to VARCHAR(255)")
    elif dialect_name == "sqlite":
        logger.info("Skipped user.password resize for SQLite; declared length is not enforced")
    else:
        logger.warning("Unsupported dialect for password resize: %s", dialect_name)


def check_and_update_db():
    db = SessionLocal()
    try:
        inspector = inspect(engine)
        if "user" in inspector.get_table_names():
            user_columns = [col["name"] for col in inspector.get_columns("user")]
            _ensure_password_column_capacity(db, inspector)
            if "expiration_date" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN expiration_date DATETIME"))
                logger.info("Added expiration_date to user table")
            if "is_active" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1"))
                logger.info("Added is_active to user table")
            if "boost_expiration_date" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN boost_expiration_date DATETIME"))
                logger.info("Added boost_expiration_date to user table")
            if "boost_limit_rule" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN boost_limit_rule VARCHAR(100)"))
                logger.info("Added boost_limit_rule to user table")
            if "addon_resource_balance" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN addon_resource_balance INTEGER DEFAULT 0"))
                logger.info("Added addon_resource_balance to user table")
            if "addon_resource_total" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN addon_resource_total INTEGER DEFAULT 0"))
                logger.info("Added addon_resource_total to user table")
            if "addon_expiration_date" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN addon_expiration_date DATETIME"))
                logger.info("Added addon_expiration_date to user table")

        if "request_log" in inspector.get_table_names():
            log_columns = [col["name"] for col in inspector.get_columns("request_log")]
            if "status" not in log_columns:
                db.execute(text('ALTER TABLE request_log ADD COLUMN status VARCHAR(20) DEFAULT "pending"'))
                logger.info("Added status to request_log table")
            if "resource_cost" not in log_columns:
                db.execute(text("ALTER TABLE request_log ADD COLUMN resource_cost INTEGER DEFAULT 0"))
                logger.info("Added resource_cost to request_log table")
            if "resource_deducted" not in log_columns:
                db.execute(text("ALTER TABLE request_log ADD COLUMN resource_deducted BOOLEAN DEFAULT 0"))
                logger.info("Added resource_deducted to request_log table")
            if "quota_source" not in log_columns:
                db.execute(text('ALTER TABLE request_log ADD COLUMN quota_source VARCHAR(20) DEFAULT "none"'))
                logger.info("Added quota_source to request_log table")

            existing_indexes = {idx.get("name") for idx in inspector.get_indexes("request_log")}
            index_defs = [
                ("idx_request_log_timestamp", "CREATE INDEX idx_request_log_timestamp ON request_log (timestamp)"),
                ("idx_request_log_timestamp_endpoint", "CREATE INDEX idx_request_log_timestamp_endpoint ON request_log (timestamp, endpoint)"),
                ("idx_request_log_timestamp_status", "CREATE INDEX idx_request_log_timestamp_status ON request_log (timestamp, status)"),
                ("idx_request_log_username_timestamp", "CREATE INDEX idx_request_log_username_timestamp ON request_log (username, timestamp)"),
                (
                    "idx_request_log_timestamp_resource_deducted",
                    "CREATE INDEX idx_request_log_timestamp_resource_deducted ON request_log (timestamp, resource_deducted)",
                ),
            ]
            for index_name, create_sql in index_defs:
                if index_name not in existing_indexes:
                    db.execute(text(create_sql))
                    logger.info("Added index: %s", index_name)

        if "payment_order" in inspector.get_table_names():
            payment_columns = [col["name"] for col in inspector.get_columns("payment_order")]
            if "activation_source" not in payment_columns:
                db.execute(text("ALTER TABLE payment_order ADD COLUMN activation_source VARCHAR(64)"))
                logger.info("Added activation_source to payment_order table")
            if "activation_error" not in payment_columns:
                db.execute(text("ALTER TABLE payment_order ADD COLUMN activation_error TEXT"))
                logger.info("Added activation_error to payment_order table")
            if "activation_error_at" not in payment_columns:
                db.execute(text("ALTER TABLE payment_order ADD COLUMN activation_error_at DATETIME"))
                logger.info("Added activation_error_at to payment_order table")
            payment_indexes = {idx.get("name") for idx in inspector.get_indexes("payment_order")}
            for index_name, create_sql in [
                ("idx_payment_order_status", "CREATE INDEX idx_payment_order_status ON payment_order (status)"),
                ("idx_payment_order_username", "CREATE INDEX idx_payment_order_username ON payment_order (username)"),
                (
                    "idx_payment_order_activation_source",
                    "CREATE INDEX idx_payment_order_activation_source ON payment_order (activation_source)",
                ),
            ]:
                if index_name not in payment_indexes:
                    db.execute(text(create_sql))
                    logger.info("Added index: %s", index_name)

        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Database upgrade failed: %s", exc)
    finally:
        db.close()


def check_user_expiration():
    db = SessionLocal()
    logger.info("Starting user expiration check")
    now_dt = datetime.now(pytz.utc)
    expired_users = db.query(User).filter(User.expiration_date <= now_dt, User.is_active == True).all()
    for user in expired_users:
        user.is_active = False
        logger.info("Disabled expired user: %s", user.username)
    db.commit()
    logger.info("User expiration check completed, expired user count: %s", len(expired_users))
    db.close()


scheduler = AsyncIOScheduler(timezone=pytz.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup")
    Base.metadata.create_all(bind=engine)
    check_and_update_db()
    load_filter_config()
    init_supported_languages()
    scheduler.add_job(check_user_expiration, "cron", hour=0, minute=0, second=0)
    scheduler.add_job(update_filter_config, "cron", day_of_week=0, hour=3, minute=0, second=0)
    scheduler.start()
    logger.info("Background scheduler started")
    yield
    logger.info("Application shutdown")
    scheduler.shutdown()


app = FastAPI(title="VoiceLinkVR Server", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start_time = time.time()
    request_id = str(uuid.uuid4())
    request_id_token = set_request_id(request_id)
    log_data = {
        "status": "failed",
        "resource_cost": 0,
        "resource_deducted": False,
        "quota_source": "none",
    }
    request.state.request_id = request_id
    request.state.rate_limit_contexts = []
    request.state.pending_quota_allocation = None

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
        logger.error("[DB-ERROR] Failed to get database session: %s", db_exc)

    try:
        token = await oauth2_scheme(request)
        if token and db:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            username = payload.get("sub")
            if username:
                user = db.query(User).filter(User.username == username).first()
                if user:
                    current_user = user.username
        logger.info(
            "[API-START] user=%s ip=%s endpoint=%s method=%s",
            current_user,
            client_ip,
            request.url.path,
            request.method,
        )

        default_context = rate_limiter.create_default_context(request)
        if default_context:
            default_context.check()
            request.state.rate_limit_contexts.append(default_context)

        response = await call_next(request)
        response_status_code = response.status_code
        log_data["duration"] = time.time() - start_time
        if response_status_code in (429, 430):
            log_data["status"] = "rate_limited"
        elif 200 <= response_status_code < 400:
            log_data["status"] = "success"
        else:
            log_data["status"] = "failed"
        logger.info(
            "[API-END] user=%s endpoint=%s status=%s duration=%.3fs",
            current_user,
            request.url.path,
            response_status_code,
            log_data["duration"],
        )

    except RateLimitExceeded as limit_exc:
        rate_limit_triggered = True
        response_status_code = 430
        log_data["duration"] = time.time() - start_time
        log_data["status"] = "rate_limited"
        logger.warning(
            "[RATE-LIMIT] user=%s ip=%s limit=%s triggered_limit=%s",
            current_user,
            client_ip,
            limit_exc.limit,
            limit_exc.triggered_limit,
        )
        response = JSONResponse(
            status_code=430,
            content={
                "error": "Too many request",
                "LimitRules": limit_exc.limit,
                "limit": limit_exc.triggered_limit,
            },
        )

    except Exception as exc:
        response_status_code = 500
        log_data["duration"] = time.time() - start_time
        log_data["status"] = "error"
        logger.error("[API-ERROR] user=%s endpoint=%s error=%s", current_user, request.url.path, exc)
        logger.exception("Unhandled API exception")
        response = JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    finally:
        session_for_log = None
        try:
            session_for_log = db or SessionLocal()
            status_for_limits = response_status_code or 500

            pending_quota = getattr(request.state, "pending_quota_allocation", None)
            if (
                not rate_limit_triggered
                and is_new_quota_mode_enabled()
                and pending_quota is not None
                and 200 <= status_for_limits < 400
            ):
                try:
                    finalized_quota = commit_quota_allocation(session_for_log, pending_quota)
                    if finalized_quota is not None:
                        log_data["resource_cost"] = int(finalized_quota.cost)
                        log_data["resource_deducted"] = True
                        log_data["quota_source"] = finalized_quota.final_source
                except HTTPException as quota_exc:
                    response_status_code = quota_exc.status_code
                    status_for_limits = response_status_code
                    log_data["status"] = "rate_limited" if quota_exc.status_code in (429, 430) else "failed"
                    log_data["resource_cost"] = 0
                    log_data["resource_deducted"] = False
                    log_data["quota_source"] = "none"
                    response = JSONResponse(status_code=quota_exc.status_code, content={"detail": quota_exc.detail})
                    logger.warning(
                        "[QUOTA] commit rejected request. user=%s endpoint=%s detail=%s",
                        current_user,
                        request.url.path,
                        quota_exc.detail,
                    )

            if rate_limiter.enabled:
                for context in getattr(request.state, "rate_limit_contexts", []):
                    should_deduct = context.should_deduct(status_for_limits)
                    if (
                        not is_new_quota_mode_enabled()
                        and not rate_limit_triggered
                        and should_deduct
                        and getattr(context, "context_type", "") == "user"
                    ):
                        log_data["resource_cost"] += getattr(context, "normalized_cost", 1)
                        log_data["resource_deducted"] = True
                        log_data["quota_source"] = "daily"
                    if not rate_limit_triggered and should_deduct:
                        context.commit()

            try:
                log = RequestLog(
                    username=current_user if current_user != "anonymous" else None,
                    ip=client_ip,
                    endpoint=request.url.path,
                    duration=log_data.get("duration", 0),
                    status=log_data.get("status", "failed"),
                    resource_cost=log_data.get("resource_cost", 0),
                    resource_deducted=log_data.get("resource_deducted", False),
                    quota_source=log_data.get("quota_source", "none"),
                )
                session_for_log.add(log)
                session_for_log.commit()
            except Exception as db_e:  # pylint: disable=broad-except
                if session_for_log is not None:
                    session_for_log.rollback()
                logger.error("[DB-ERROR] Failed to commit request log: %s", db_e)
        finally:
            if response is not None:
                response.headers["X-Request-ID"] = request_id
            if db is None and session_for_log is not None:
                session_for_log.close()
            if db_gen is not None:
                db_gen.close()
            reset_request_id(request_id_token)

    return response


app.include_router(ui.router, prefix="/ui", tags=["UI"])
app.include_router(api.router, prefix="/api", tags=["API"])
app.include_router(user_center.router, prefix="/api/user-center", tags=["User Center"])
app.include_router(manage_api.router, prefix="/manageapi", tags=["Management API"])


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to VoiceLinkVR FastAPI Server!"}
