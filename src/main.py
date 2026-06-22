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
from core.dependencies import oauth2_scheme, resolve_authenticated_user
from core.identity import host_matches, normalize_host
from core.logging_config import logger, reset_request_id, set_request_id
from core.quota_service import commit_quota_allocation, is_new_quota_mode_enabled
from core.rate_limiter import RateLimitExceeded, get_client_ip, is_website_rate_limit_exempt_path, rate_limiter
from core.schema_upgrade import run_schema_upgrade
from core.security_policy import normalize_cookie_samesite, validate_secret_strength
from core.services import ClientDisconnectedError, init_supported_languages, load_filter_config, update_filter_config
from core.update_service import refresh_translation_capabilities_cache
from core.v2_request_metrics import V2RequestMetricRecord, record_v2_request_metrics, utcnow_naive
from core.v2_dependencies import fallback_expired_v2_user_to_free, resolve_authenticated_v2_user
from db.base import SessionLocal, engine
from db.models import RequestLog, V2User
from routers import admin_api, api, auth, manage_api, portal_api, public_download, user_center, v2_tts, voice_clone_presets


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
    expired_v2_users = db.query(V2User).filter(V2User.expiration_date <= now_dt).all()

    downgraded_count = 0
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
        path == "/v1/manageapi"
        or path.startswith("/v1/manageapi/")
        or path == "/v2/adminapi"
        or path.startswith("/v2/adminapi/")
    )


RUNTIME_MODES = {"all", "light", "heavy"}
HEAVY_PATH_PREFIXES = (
    "/v1/api/whisper/",
    "/v2/api/whisper/",
    "/v2/api/tts/",
    "/v2/api/clone/",
)
HEAVY_EXACT_PATHS = {
    "/v1/api/whisper",
    "/v2/api/whisper",
    "/v2/api/tts",
    "/v2/api/clone",
    "/v1/api/func/translateToEnglish",
    "/v1/api/func/translateToOtherLanguage",
    "/v1/api/func/multitranslateToOtherLanguage",
    "/v1/api/func/photoTranslate",
    "/v1/api/func/tts",
    "/v1/api/func/webtranslate",
    "/v2/api/func/translateToEnglish",
    "/v2/api/func/translateToOtherLanguage",
    "/v2/api/func/multitranslateToOtherLanguage",
    "/v2/api/func/photoTranslate",
    "/v2/api/func/tts",
    "/v2/api/func/webtranslate",
}
RUNTIME_ALWAYS_ALLOWED_PATHS = {
    "/",
    "/healthz",
    "/readyz",
}


def _runtime_mode() -> str:
    mode = str(getattr(settings, "VOICELINK_RUNTIME_MODE", "all") or "all").strip().lower()
    return mode if mode in RUNTIME_MODES else "all"


def _is_heavy_path(path: str) -> bool:
    normalized = path.rstrip("/") if path != "/" else path
    if normalized in HEAVY_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in HEAVY_PATH_PREFIXES)


def _runtime_mode_rejection_response(path: str) -> JSONResponse | None:
    mode = _runtime_mode()
    if mode == "all":
        return None
    if path in RUNTIME_ALWAYS_ALLOWED_PATHS:
        return None

    if mode == "light" and _is_heavy_path(path):
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "heavy_endpoint_not_available_on_light",
                    "message": "Heavy endpoint is routed to light backend",
                }
            },
            headers={"Retry-After": "2"},
        )

    if mode == "heavy" and not _is_heavy_path(path):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    return None


def _validate_runtime_security_settings() -> None:
    mode = str(getattr(settings, "VOICELINK_RUNTIME_MODE", "all") or "all").strip().lower()
    if mode not in RUNTIME_MODES:
        raise RuntimeError("VOICELINK_RUNTIME_MODE must be one of all, light, heavy")
    settings.VOICELINK_RUNTIME_MODE = mode
    settings.SESSION_COOKIE_SAME_SITE = normalize_cookie_samesite(settings.SESSION_COOKIE_SAME_SITE)
    if int(settings.SESSION_COOKIE_MAX_AGE or 0) <= 0:
        raise RuntimeError("SESSION_COOKIE_MAX_AGE must be greater than 0")
    if int(settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES or 0) <= 0:
        raise RuntimeError("JWT_ACCESS_TOKEN_EXPIRES_MINUTES must be greater than 0")
    if int(settings.GUEST_SESSION_EXPIRES_MINUTES or 0) <= 0:
        raise RuntimeError("GUEST_SESSION_EXPIRES_MINUTES must be greater than 0")

    if not settings.SECURITY_ENFORCE_STRONG_SECRETS:
        return

    for field_name, value in (
        ("JWT_SECRET_KEY", settings.JWT_SECRET_KEY),
        ("SESSION_SECRET_KEY", settings.SESSION_SECRET_KEY),
        ("AFDIAN_CUSTOM_ID_KEY", settings.AFDIAN_CUSTOM_ID_KEY),
    ):
        validate_secret_strength(value, field_name=field_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup runtime_mode=%s", _runtime_mode())
    _validate_runtime_security_settings()
    scheduler_lock_connection = None
    if settings.VOICELINK_RUN_SCHEMA_UPGRADE:
        with _startup_db_init_lock():
            run_schema_upgrade()
    else:
        logger.info("Skipped schema upgrade because VOICELINK_RUN_SCHEMA_UPGRADE=false")
    load_filter_config()
    init_supported_languages()
    refresh_translation_capabilities_cache()
    run_scheduler = False
    if settings.VOICELINK_RUN_SCHEDULER:
        run_scheduler, scheduler_lock_connection = _try_acquire_scheduler_lock()
    else:
        logger.info("Skipped scheduler startup because VOICELINK_RUN_SCHEDULER=false")
    if run_scheduler:
        scheduler.add_job(check_user_expiration, "cron", hour=0, minute=0, second=0)
        scheduler.add_job(update_filter_config, "cron", day_of_week=0, hour=3, minute=0, second=0)
        scheduler.add_job(refresh_translation_capabilities_cache, "cron", hour=4, minute=0, second=0)
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


def _resolve_request_host(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        return normalize_host(forwarded_host.split(",")[0].strip())
    return normalize_host(request.headers.get("host") or request.url.hostname or "")


def _rewrite_versioned_path(request: Request) -> None:
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
    if settings.ADMIN_SURFACE_LAN_ONLY and _is_admin_surface_path(request.url.path):
        client_ip = _resolve_admin_access_ip(request)
        if not _is_lan_client_ip(client_ip):
            logger.warning("[ADMIN-ACCESS] Denied non-LAN request. ip=%s path=%s", client_ip, request.url.path)
            if (
                request.url.path == "/v1/manageapi"
                or request.url.path.startswith("/v1/manageapi/")
                or request.url.path == "/v2/adminapi"
                or request.url.path.startswith("/v2/adminapi/")
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

    response = None
    response_status_code = 500
    rate_limit_triggered = False
    authenticated_user = None
    authenticated_user_is_v2 = False

    runtime_rejection = _runtime_mode_rejection_response(request.url.path)
    if runtime_rejection is not None:
        logger.warning(
            "[RUNTIME-MODE] rejected request before auth. mode=%s path=%s method=%s",
            _runtime_mode(),
            request.url.path,
            request.method,
        )
        runtime_rejection.headers["X-Request-ID"] = request_id
        reset_request_id(request_id_token)
        return runtime_rejection

    try:
        token = await oauth2_scheme(request)
        auth_db = None
        try:
            auth_db = SessionLocal()
            if is_v2_request:
                authenticated_v2_user = resolve_authenticated_v2_user(
                    request,
                    auth_db,
                    token=token,
                    allow_inactive=True,
                    allow_expired=True,
                )
                if authenticated_v2_user is not None:
                    authenticated_user = authenticated_v2_user
                    authenticated_user_is_v2 = True
            if authenticated_user is None and not is_v2_request:
                authenticated_user = resolve_authenticated_user(
                    request,
                    auth_db,
                    token=token,
                    allow_inactive=True,
                    allow_expired=True,
                )

            if authenticated_user is not None:
                current_user = authenticated_user.username
                current_user_uuid = getattr(authenticated_user, "user_uuid", None)
                if bool(getattr(authenticated_user, "is_default_test_user", False)):
                    actor_type = "anonymous_test"
                    rate_limit_scope = "ip"
                    quota_scope = "ip"
                else:
                    actor_type = "authenticated"
                    rate_limit_scope = "user"
                    quota_scope = "user"
        except Exception as db_exc:
            logger.error("[DB-ERROR] Failed to resolve request user: %s", db_exc)
        finally:
            if auth_db is not None:
                auth_db.close()
        logger.info(
            "[API-START] user=%s ip=%s endpoint=%s method=%s",
            current_user,
            client_ip,
            request.url.path,
            request.method,
        )

        should_apply_default_limit = (
            authenticated_user is None
            and request.method.upper() != "OPTIONS"
            and request.url.path != "/v2/auth/me"
            and not is_website_rate_limit_exempt_path(request.url.path)
            and not _is_admin_surface_path(request.url.path)
        )
        if should_apply_default_limit:
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

    except ClientDisconnectedError as disconnect_exc:
        response_status_code = 499
        log_data["duration"] = time.time() - start_time
        log_data["status"] = "client_disconnected"
        logger.info(
            "[CLIENT-DISCONNECT] user=%s endpoint=%s stage=%s duration=%.3fs",
            current_user,
            request.url.path,
            disconnect_exc,
            log_data["duration"],
        )
        response = JSONResponse(status_code=499, content={"detail": "Client Closed Request"})

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
            session_for_log = SessionLocal()
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
                record_time = utcnow_naive()
                if is_v2_request:
                    record_v2_request_metrics(
                        session_for_log,
                        V2RequestMetricRecord(
                            request_id=request_id,
                            created_at=record_time,
                            username=current_user if current_user != "anonymous" else None,
                            user_uuid=current_user_uuid,
                            actor_type=actor_type,
                            rate_limit_scope=rate_limit_scope,
                            quota_scope=quota_scope,
                            ip=client_ip,
                            host=request_host,
                            method=request.method,
                            endpoint=request.url.path,
                            duration_ms=max(0, int(round(float(log_data.get("duration", 0) or 0) * 1000))),
                            status=log_data.get("status", "failed"),
                            status_code=response_status_code,
                            resource_cost=int(log_data.get("resource_cost", 0) or 0),
                            resource_deducted=bool(log_data.get("resource_deducted", False)),
                            quota_source=log_data.get("quota_source", "none"),
                            is_default_test_user=bool(getattr(authenticated_user, "is_default_test_user", False)),
                            extra_payload=getattr(request.state, "audit_payload", None),
                        ),
                    )
                if not is_v2_request or (authenticated_user is not None and not authenticated_user_is_v2):
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
            if session_for_log is not None:
                session_for_log.close()
            reset_request_id(request_id_token)

    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET_KEY,
    https_only=settings.SESSION_COOKIE_HTTPS_ONLY,
    same_site=settings.SESSION_COOKIE_SAME_SITE,
    max_age=settings.SESSION_COOKIE_MAX_AGE,
)


if settings.API_V1_ENABLED:
    app.include_router(api.router, prefix="/v1/api", tags=["V1 API"])
    app.include_router(user_center.router, prefix="/v1/api/user-center", tags=["V1 User Center"])
    app.include_router(manage_api.router, prefix="/v1/manageapi", tags=["V1 Management API"])

if settings.API_V2_ENABLED:
    app.include_router(public_download.router, prefix="/v2/api", tags=["V2 Public Downloads"])
    if settings.API_V2_AUTH_ENABLED:
        app.include_router(auth.router, prefix="/v2/auth", tags=["V2 Auth"])
    app.include_router(voice_clone_presets.router, prefix="/v2/api", tags=["V2 Voice Clone Presets"])
    app.include_router(v2_tts.router, prefix="/v2/api", tags=["V2 TTS API"])
    if settings.API_V2_PORTAL_ENABLED:
        app.include_router(portal_api.router, prefix="/v2/api", tags=["V2 Portal API"])
        app.include_router(api.router, prefix="/v2/api", tags=["V2 Compat API"])

app.include_router(admin_api.router, prefix="/v2/adminapi", tags=["V2 Admin API"])
app.include_router(voice_clone_presets.admin_router, prefix="/v2/adminapi", tags=["V2 Admin Voice Clone Presets"])


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {
        "status": "ok",
        "mode": _runtime_mode(),
        "schedulerEnabled": bool(settings.VOICELINK_RUN_SCHEDULER),
        "schemaUpgradeEnabled": bool(settings.VOICELINK_RUN_SCHEMA_UPGRADE),
    }


@app.get("/readyz", include_in_schema=False)
async def readyz():
    db = None
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[READYZ] dependency check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "mode": _runtime_mode(),
                "error": "database_unavailable",
            },
        )
    finally:
        if db is not None:
            db.close()
    return {
        "status": "ready",
        "mode": _runtime_mode(),
        "schedulerEnabled": bool(settings.VOICELINK_RUN_SCHEDULER),
        "schemaUpgradeEnabled": bool(settings.VOICELINK_RUN_SCHEMA_UPGRADE),
    }


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to VoiceLinkVR FastAPI Server!"}
