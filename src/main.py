import time
import uuid
from ipaddress import ip_address
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from core.config import refresh_settings_from_override_file_if_changed, settings
from core.dependencies import fallback_expired_user_to_free, get_db, oauth2_scheme, resolve_authenticated_user
from core.identity import host_matches, normalize_host
from core.logging_config import logger, reset_request_id, set_request_id
from core.quota_service import commit_quota_allocation, is_new_quota_mode_enabled
from core.rate_limiter import RateLimitExceeded, get_client_ip, rate_limiter
from core.schema_upgrade import run_schema_upgrade
from core.services import init_supported_languages, load_filter_config, update_filter_config
from core.v2_dependencies import fallback_expired_v2_user_to_free, resolve_authenticated_v2_user
from db.base import SessionLocal, engine
from db.models import RequestLog, User, V2RequestLog, V2User
from routers import admin_api, api, auth, manage_api, portal_api, user_center


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


def _add_column_if_missing(db, columns: list[str], column_name: str, ddl: str, log_message: str) -> None:
    if column_name in columns:
        return
    db.execute(text(ddl))
    columns.append(column_name)
    logger.info(log_message)


def _sync_payment_order_lookup_digests(db) -> None:
    updated_count = 0
    for order in db.query(PaymentOrder).all():
        expected_custom_id_digest = payment_lookup_digest(getattr(order, "custom_id", None))
        expected_remark_digest = payment_lookup_digest(getattr(order, "remark", None))
        changed = False

        if getattr(order, "custom_id_digest", None) != expected_custom_id_digest:
            order.custom_id_digest = expected_custom_id_digest
            changed = True
        if getattr(order, "remark_digest", None) != expected_remark_digest:
            order.remark_digest = expected_remark_digest
            changed = True

        if changed:
            updated_count += 1

    if updated_count:
        logger.info("Synchronized payment_order lookup digests for %s rows", updated_count)


@contextmanager
def _startup_db_init_lock(timeout_seconds: int = 180):
    if engine.dialect.name != "mysql":
        yield
        return

    lock_name = "voicelinkvr_server_schema_init"
    connection = engine.connect()
    try:
        acquired = connection.execute(
            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
            {"lock_name": lock_name, "timeout_seconds": timeout_seconds},
        ).scalar()
        if int(acquired or 0) != 1:
            raise RuntimeError(f"Failed to acquire startup DB lock: {lock_name}")
        logger.info("Acquired MySQL startup DB lock: %s", lock_name)
        yield
    finally:
        try:
            connection.execute(text("SELECT RELEASE_LOCK(:lock_name)"), {"lock_name": lock_name})
            logger.info("Released MySQL startup DB lock: %s", lock_name)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to release MySQL startup DB lock %s: %s", lock_name, exc)
        connection.close()


def _try_acquire_scheduler_lock():
    if engine.dialect.name != "mysql":
        return True, None

    lock_name = "voicelinkvr_server_scheduler"
    connection = engine.connect()
    try:
        acquired = connection.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"),
            {"lock_name": lock_name},
        ).scalar()
        if int(acquired or 0) != 1:
            connection.close()
            return False, None
        logger.info("Acquired MySQL scheduler lock: %s", lock_name)
        return True, connection
    except Exception:
        connection.close()
        raise


def _release_scheduler_lock(connection) -> None:
    if connection is None:
        return

    lock_name = "voicelinkvr_server_scheduler"
    try:
        connection.execute(text("SELECT RELEASE_LOCK(:lock_name)"), {"lock_name": lock_name})
        logger.info("Released MySQL scheduler lock: %s", lock_name)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to release MySQL scheduler lock %s: %s", lock_name, exc)
    finally:
        connection.close()


def check_and_update_db():
    db = SessionLocal()
    try:
        inspector = inspect(engine)
        if "user" in inspector.get_table_names():
            user_columns = [col["name"] for col in inspector.get_columns("user")]
            _ensure_password_column_capacity(db, inspector)
            if "email" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN email VARCHAR(255)"))
                logger.info("Added email to user table")
            if "email_verified" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN email_verified BOOLEAN DEFAULT 0"))
                logger.info("Added email_verified to user table")
            if "display_name" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN display_name VARCHAR(100)"))
                logger.info("Added display_name to user table")
            if "expiration_date" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN expiration_date DATETIME"))
                logger.info("Added expiration_date to user table")
            if "is_active" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1"))
                logger.info("Added is_active to user table")
            if "created_at" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN created_at DATETIME"))
                logger.info("Added created_at to user table")
            if "last_login_at" not in user_columns:
                db.execute(text("ALTER TABLE user ADD COLUMN last_login_at DATETIME"))
                logger.info("Added last_login_at to user table")
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

            user_indexes = {idx.get("name") for idx in inspector.get_indexes("user")}
            for index_name, create_sql in [
                ("idx_user_email", "CREATE INDEX idx_user_email ON user (email)"),
                ("idx_user_email_verified", "CREATE INDEX idx_user_email_verified ON user (email_verified)"),
            ]:
                if index_name not in user_indexes:
                    db.execute(text(create_sql))
                    logger.info("Added index: %s", index_name)

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
            _add_column_if_missing(
                db,
                payment_columns,
                "custom_id_digest",
                "ALTER TABLE payment_order ADD COLUMN custom_id_digest VARCHAR(64)",
                "Added custom_id_digest to payment_order table",
            )
            _add_column_if_missing(
                db,
                payment_columns,
                "remark_digest",
                "ALTER TABLE payment_order ADD COLUMN remark_digest VARCHAR(64)",
                "Added remark_digest to payment_order table",
            )
            _add_column_if_missing(
                db,
                payment_columns,
                "activation_source",
                "ALTER TABLE payment_order ADD COLUMN activation_source VARCHAR(64)",
                "Added activation_source to payment_order table",
            )
            _add_column_if_missing(
                db,
                payment_columns,
                "activation_error",
                "ALTER TABLE payment_order ADD COLUMN activation_error TEXT",
                "Added activation_error to payment_order table",
            )
            _add_column_if_missing(
                db,
                payment_columns,
                "activation_error_at",
                "ALTER TABLE payment_order ADD COLUMN activation_error_at DATETIME",
                "Added activation_error_at to payment_order table",
            )

            _sync_payment_order_lookup_digests(db)
            payment_indexes = {idx.get("name") for idx in inspector.get_indexes("payment_order")}
            for index_name, create_sql in [
                (
                    "idx_payment_order_custom_id_digest",
                    "CREATE UNIQUE INDEX idx_payment_order_custom_id_digest ON payment_order (custom_id_digest)",
                ),
                (
                    "idx_payment_order_remark_digest",
                    "CREATE INDEX idx_payment_order_remark_digest ON payment_order (remark_digest)",
                ),
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

        if "account_token" in inspector.get_table_names():
            account_token_columns = [col["name"] for col in inspector.get_columns("account_token")]
            if "code" not in account_token_columns:
                db.execute(text("ALTER TABLE account_token ADD COLUMN code VARCHAR(16)"))
                logger.info("Added code to account_token table")
            account_token_indexes = {idx.get("name") for idx in inspector.get_indexes("account_token")}
            for index_name, create_sql in [
                ("idx_account_token_username_purpose", "CREATE INDEX idx_account_token_username_purpose ON account_token (username, purpose)"),
                ("idx_account_token_email_purpose", "CREATE INDEX idx_account_token_email_purpose ON account_token (email, purpose)"),
                ("idx_account_token_purpose_expires_at", "CREATE INDEX idx_account_token_purpose_expires_at ON account_token (purpose, expires_at)"),
                ("idx_account_token_purpose_code", "CREATE INDEX idx_account_token_purpose_code ON account_token (purpose, code)"),
            ]:
                if index_name not in account_token_indexes:
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
    expired_users = db.query(User).filter(User.expiration_date <= now_dt).all()
    expired_v2_users = db.query(V2User).filter(V2User.expiration_date <= now_dt).all()

    downgraded_count = 0
    for user in expired_users:
        downgraded, _ = fallback_expired_user_to_free(db, user)
        if downgraded:
            downgraded_count += 1
            logger.info("Downgraded expired legacy user to free plan: %s", user.username)
    for user in expired_v2_users:
        downgraded, _ = fallback_expired_v2_user_to_free(db, user)
        if downgraded:
            downgraded_count += 1
            logger.info("Downgraded expired v2 user to free plan: %s", user.username)
    db.commit()
    logger.info("User expiration check completed, downgraded user count: %s", downgraded_count)
    db.close()


scheduler = AsyncIOScheduler(timezone=pytz.utc)


def _is_lan_client_ip(client_ip: str) -> bool:
    normalized = str(client_ip or "").strip()
    if not normalized or normalized == "unknown":
        return False
    try:
        candidate = ip_address(normalized)
    except ValueError:
        return normalized in {"localhost"}
    return candidate.is_private or candidate.is_loopback


def _resolve_admin_access_ip(request: Request) -> str:
    peer_ip = request.client.host if request.client else ""
    if _is_lan_client_ip(peer_ip):
        forwarded_ip = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "")
        forwarded_ip = forwarded_ip.split(",")[0].strip()
        if forwarded_ip:
            return forwarded_ip
    return peer_ip or "unknown"


def _is_admin_surface_path(path: str) -> bool:
    return (
        path == "/manageapi"
        or path.startswith("/manageapi/")
        or path == "/v1/manageapi"
        or path.startswith("/v1/manageapi/")
        or path == "/adminapi"
        or path.startswith("/adminapi/")
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup")
    scheduler_lock_connection = None
    with _startup_db_init_lock():
        run_schema_upgrade()
    load_filter_config()
    init_supported_languages()
    run_scheduler, scheduler_lock_connection = _try_acquire_scheduler_lock()
    if run_scheduler:
        scheduler.add_job(check_user_expiration, "cron", hour=0, minute=0, second=0)
        scheduler.add_job(update_filter_config, "cron", day_of_week=0, hour=3, minute=0, second=0)
        scheduler.start()
        logger.info("Background scheduler started")
    else:
        logger.info("Skipped scheduler startup in this worker")
    yield
    logger.info("Application shutdown")
    if scheduler.running:
        scheduler.shutdown()
    _release_scheduler_lock(scheduler_lock_connection)


app = FastAPI(title="VoiceLinkVR Server", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY)


def _resolve_request_host(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        return normalize_host(forwarded_host.split(",")[0].strip())
    return normalize_host(request.headers.get("host") or request.url.hostname or "")


def _rewrite_versioned_path(request: Request) -> None:
    path = request.scope.get("path") or request.url.path
    if path.startswith("/v1/") or path.startswith("/v2/") or path.startswith("/adminapi") or path.startswith("/status-probe"):
        return

    host = _resolve_request_host(request)
    if host_matches(host, settings.api_v1_hosts):
        if path == "/api" or path.startswith("/api/") or path == "/manageapi" or path.startswith("/manageapi/"):
            request.scope["path"] = f"/v1{path}"
            return

    if host_matches(host, settings.api_v2_hosts):
        if path == "/auth" or path.startswith("/auth/"):
            request.scope["path"] = f"/v2{path}"
            return
        if path == "/api/account" or path.startswith("/api/account/") or path == "/api/billing" or path.startswith("/api/billing/"):
            request.scope["path"] = f"/v2{path}"
            return


@app.middleware("http")
async def site_config_refresh_middleware(request: Request, call_next):
    refresh_settings_from_override_file_if_changed()
    return await call_next(request)


@app.middleware("http")
async def version_host_routing_middleware(request: Request, call_next):
    _rewrite_versioned_path(request)
    return await call_next(request)


@app.middleware("http")
async def lan_only_admin_middleware(request: Request, call_next):
    if _is_admin_surface_path(request.url.path):
        client_ip = _resolve_admin_access_ip(request)
        if not _is_lan_client_ip(client_ip):
            logger.warning("[ADMIN-ACCESS] Denied non-LAN request. ip=%s path=%s", client_ip, request.url.path)
            if (
                request.url.path == "/manageapi"
                or request.url.path.startswith("/manageapi/")
                or request.url.path == "/v1/manageapi"
                or request.url.path.startswith("/v1/manageapi/")
                or request.url.path == "/adminapi"
                or request.url.path.startswith("/adminapi/")
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Management API is only available from LAN clients."},
                )
            return PlainTextResponse("Management UI is only available from LAN clients.", status_code=403)
    return await call_next(request)


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
    current_user_uuid = None
    actor_type = "anonymous"
    rate_limit_scope = None
    quota_scope = None
    request_host = _resolve_request_host(request)
    is_v2_request = request.scope.get("path", "").startswith("/v2/") or host_matches(request_host, settings.api_v2_hosts)
    log_model = V2RequestLog if is_v2_request else RequestLog

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
        if db:
            authenticated_user = None
            if is_v2_request:
                authenticated_v2_user = resolve_authenticated_v2_user(
                    request,
                    db,
                    token=token,
                    allow_inactive=True,
                    allow_expired=True,
                )
                if authenticated_v2_user is not None:
                    authenticated_user = authenticated_v2_user

            if authenticated_user is None and not is_v2_request:
                authenticated_user = resolve_authenticated_user(
                    request,
                    db,
                    token=token,
                    allow_inactive=True,
                    allow_expired=True,
                )

            if authenticated_user is not None:
                current_user = authenticated_user.username
                current_user_uuid = authenticated_user.user_uuid
                if bool(getattr(authenticated_user, "is_default_test_user", False)):
                    actor_type = "anonymous_test"
                    rate_limit_scope = "ip"
                    quota_scope = "ip"
                else:
                    actor_type = "authenticated"
                    rate_limit_scope = "user"
                    quota_scope = "user"
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
                        actor_type = finalized_quota.actor_type
                        quota_scope = finalized_quota.quota_scope
                        rate_limit_scope = finalized_quota.quota_scope
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
                if log_model is V2RequestLog:
                    log = V2RequestLog(
                        request_id=request_id,
                        username=current_user if current_user != "anonymous" else None,
                        user_uuid=current_user_uuid,
                        actor_type=actor_type,
                        rate_limit_scope=rate_limit_scope,
                        quota_scope=quota_scope,
                        ip=client_ip,
                        host=request_host,
                        method=request.method,
                        endpoint=request.url.path,
                        duration=log_data.get("duration", 0),
                        status=log_data.get("status", "failed"),
                        status_code=response_status_code,
                        resource_cost=log_data.get("resource_cost", 0),
                        resource_deducted=log_data.get("resource_deducted", False),
                        quota_source=log_data.get("quota_source", "none"),
                    )
                else:
                    log = RequestLog(
                        username=current_user if current_user != "anonymous" else None,
                        user_uuid=current_user_uuid,
                        actor_type=actor_type,
                        rate_limit_scope=rate_limit_scope,
                        quota_scope=quota_scope,
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


if settings.API_V1_ENABLED:
    app.include_router(api.router, prefix="/v1/api", tags=["V1 API"])
    app.include_router(user_center.router, prefix="/v1/api/user-center", tags=["V1 User Center"])
    app.include_router(manage_api.router, prefix="/v1/manageapi", tags=["V1 Management API"])

if settings.API_V2_ENABLED:
    if settings.API_V2_AUTH_ENABLED:
        app.include_router(auth.router, prefix="/v2/auth", tags=["V2 Auth"])
    if settings.API_V2_PORTAL_ENABLED:
        app.include_router(portal_api.router, prefix="/v2/api", tags=["V2 Portal API"])
        app.include_router(api.router, prefix="/v2/api", tags=["V2 Compat API"])

if settings.LEGACY_MODE:
    app.include_router(api.router, prefix="/api", tags=["Legacy API"])
    app.include_router(user_center.router, prefix="/api/user-center", tags=["Legacy User Center"])
    app.include_router(manage_api.router, prefix="/manageapi", tags=["Legacy Management API"])
    app.include_router(auth.router, prefix="/auth", tags=["Legacy Auth"])

app.include_router(admin_api.router, prefix="/adminapi", tags=["Admin API"])


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to VoiceLinkVR FastAPI Server!"}
