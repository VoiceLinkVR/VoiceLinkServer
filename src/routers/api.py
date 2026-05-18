import emoji
import httpx
import re
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Form, Body, Response, Request, Query
# 移除 OAuth2PasswordRequestForm 的导入
# from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from pydantic import BaseModel
from typing import Optional

from core.dependencies import (
    get_db,
    issue_noninteractive_login_artifacts,
    oauth2_scheme,
    resolve_authenticated_user,
    verify_password,
    get_effective_user_limit_rule,
    utcnow_naive,
)
from core.quota_service import (
    QUOTA_SOURCE_ADDON,
    QUOTA_SOURCE_DAILY,
    build_quota_snapshot,
    is_new_quota_mode_enabled,
)
from core.rate_limiter import enforce_auth_attempt_rate_limit, enforce_user_rate_limit, set_user_context_cost
from core.config import settings
from core.identity import has_bound_email
from core.v2_dependencies import resolve_authenticated_v2_user
from db.models import User, RequestLog, V2User
from core.services import (
    errorFilter, do_translate,
    supportedLanguagesList, whisperSupportedLanguageList, packaged_opus_stream_to_wav_bytes,
    compress_repeated_chars, do_transcribe, do_translate_audio,
    normalize_asr_engine, asr_engine_supports_direct_translation,
    resolve_asr_engine_for_language,
    get_runtime_translation_source_languages, get_runtime_translation_target_languages,
    is_runtime_translation_source_supported, is_runtime_translation_target_supported,
    normalize_translation_language_code,
    detect_translation_source_language,
)
from core.image_translate_service import translate_image_to_targets_async
from core.logging_config import logger
# 导入用于接收JSON体的Pydantic模型
from schemas.user import UserLogin

router = APIRouter()
DAY_LIMIT_RULE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*day\s*$", re.IGNORECASE)
DEFAULT_USAGE_DAYS = 7
USAGE_EXCLUDED_PREFIXES = (
    "/auth",
    "/v2/auth",
    "/api/account",
    "/v2/api/account",
    "/api/billing",
    "/v2/api/billing",
    "/api/content",
    "/v2/api/content",
    "/v2/adminapi",
)


async def get_current_api_user_for_status(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User | V2User:
    user: User | V2User | None = None
    if _is_v2_request(request):
        user = resolve_authenticated_v2_user(request, db, token=token, allow_inactive=True, allow_expired=True)
    else:
        user = resolve_authenticated_user(request, db, token=token, allow_inactive=True, allow_expired=True)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _is_v2_request(request: Request) -> bool:
    return str(request.scope.get("path", "") or "").startswith("/v2/")


def _extract_daily_limit(limit_rule: str) -> Optional[int]:
    if not limit_rule:
        return None
    for part in (segment.strip() for segment in limit_rule.split(";") if segment.strip()):
        matched = DAY_LIMIT_RULE_PATTERN.match(part)
        if matched:
            return int(matched.group(1))
    return None


def _append_usage_excluded_filters(filters: list):
    for prefix in USAGE_EXCLUDED_PREFIXES:
        filters.append(RequestLog.endpoint.notlike(f"{prefix}%"))
    return filters


def _is_test_user(username: str) -> bool:
    configured = (settings.LIMIT_PUBLIC_TEST_USER or "").strip()
    if configured and username == configured:
        return True
    if username == (settings.DEFAULT_TEST_USER_USERNAME or "").strip():
        return True
    return username.lower() == "testuser"


def _apply_resource_cost(request: Request, cost: int) -> None:
    set_user_context_cost(request, max(1, int(cost or 1)))


def _get_compat_resource_cost(cost: int) -> int:
    if settings.LEGACY_CLIENT_COMPAT:
        return 1
    return max(1, int(cost or 1))


def _apply_effective_resource_cost(request: Request, cost: int) -> None:
    _apply_resource_cost(request, _get_compat_resource_cost(cost))


def _reject_auto_language(field_name: str, language: str) -> None:
    if (language or "").strip().lower() == "auto":
        raise HTTPException(status_code=400, detail=f"{field_name} 'auto' is no longer supported")


def _validate_runtime_target_language(field_name: str, language: str, allow_none: bool = False) -> str:
    normalized = (language or "").strip()
    if allow_none and normalized == "none":
        return normalized
    if normalized.lower() == "auto":
        raise HTTPException(status_code=400, detail=f"{field_name} 'auto' is no longer supported")

    canonical = normalize_translation_language_code(normalized)
    if not is_runtime_translation_target_supported(canonical):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} error, please use following languages: {str(get_runtime_translation_target_languages())}",
        )
    return canonical


def _validate_runtime_source_language(field_name: str, language: str, allow_none: bool = False) -> str:
    normalized = (language or "").strip()
    if allow_none and normalized == "none":
        return normalized
    if normalized.lower() == "auto":
        raise HTTPException(status_code=400, detail=f"{field_name} 'auto' is no longer supported")

    canonical = normalize_translation_language_code(normalized)
    if not is_runtime_translation_source_supported(canonical):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} error, please use following languages: {str(get_runtime_translation_source_languages())}",
        )
    return canonical


def _resolve_asr_engine(engine: Optional[str], asr_engine: Optional[str]) -> str:
    requested = engine if (engine or "").strip() else asr_engine
    try:
        return normalize_asr_engine(requested)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


CHINESE_ROUTE_ASR_ENGINES = {"firered", "sensevoice"}
WHISPER_ROUTE_ASR_ENGINES = {"whisper", "whisper-trt"}


def _requested_asr_engine_value(engine: Optional[str], asr_engine: Optional[str]) -> Optional[str]:
    return engine if (engine or "").strip() else asr_engine


def _default_whisper_route_engine() -> str:
    return "whisper-trt" if settings.WHISPER_BACKEND == "whisper-trt" else "whisper"


def _resolve_chinese_asr_engine(
    engine: Optional[str],
    asr_engine: Optional[str],
    endpoint: str,
) -> str:
    selected_engine = _resolve_asr_engine(engine, asr_engine)
    if selected_engine not in CHINESE_ROUTE_ASR_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=f"{endpoint} Chinese ASR supports firered and sensevoice only",
        )
    return selected_engine


def _resolve_non_chinese_asr_engine(
    engine: Optional[str],
    asr_engine: Optional[str],
    source_language: str,
    endpoint: str,
) -> str:
    requested = _requested_asr_engine_value(engine, asr_engine)
    if not (requested or "").strip():
        selected_engine = _default_whisper_route_engine()
        logger.info(
            "[%s] non-Chinese source uses default Whisper ASR chain: effective=%s sourceLanguage=%s",
            endpoint,
            selected_engine,
            source_language,
        )
        return selected_engine

    selected_engine = _resolve_asr_engine(engine, asr_engine)
    if selected_engine in WHISPER_ROUTE_ASR_ENGINES:
        return selected_engine

    effective_engine = _default_whisper_route_engine()
    if effective_engine != selected_engine:
        logger.info(
            "[%s] non-Chinese source uses full Whisper ASR chain: requested=%s effective=%s sourceLanguage=%s",
            endpoint,
            selected_engine,
            effective_engine,
            source_language,
        )
    return effective_engine


def _resolve_source_asr_engine(
    engine: Optional[str],
    asr_engine: Optional[str],
    source_language: str,
    endpoint: str,
) -> str:
    if normalize_translation_language_code(source_language) == "zh":
        return _resolve_chinese_asr_engine(engine, asr_engine, endpoint)
    return _resolve_non_chinese_asr_engine(engine, asr_engine, source_language, endpoint)


def _resolve_audio_translation_asr_engine(
    engine: Optional[str],
    asr_engine: Optional[str],
    endpoint: str,
) -> str:
    requested = _requested_asr_engine_value(engine, asr_engine)
    if not (requested or "").strip():
        return _default_whisper_route_engine()

    selected_engine = _resolve_asr_engine(engine, asr_engine)
    if selected_engine not in WHISPER_ROUTE_ASR_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=f"{endpoint} audio translation supports whisper and whisper-trt only",
        )
    return selected_engine


def _get_audio_translation_target_languages(asr_engine: Optional[str] = None) -> list[str]:
    languages = set(get_runtime_translation_target_languages())
    if asr_engine and asr_engine_supports_direct_translation(asr_engine):
        languages.update(whisperSupportedLanguageList)
    return sorted(languages)


def _validate_audio_target_language(
    field_name: str,
    language: str,
    allow_none: bool = False,
    asr_engine: Optional[str] = None,
) -> str:
    normalized = (language or "").strip()
    if allow_none and normalized == "none":
        return normalized
    if normalized.lower() == "auto":
        raise HTTPException(status_code=400, detail=f"{field_name} 'auto' is no longer supported")

    canonical = normalize_translation_language_code(normalized)
    target_languages = _get_audio_translation_target_languages(asr_engine)
    if canonical in set(target_languages):
        return canonical

    raise HTTPException(
        status_code=400,
        detail=f"{field_name} error, please use following languages: {str(target_languages)}",
    )

# --- API 路由 ---

def _daily_resource_cost_case():
    if is_new_quota_mode_enabled():
        return case((RequestLog.quota_source == QUOTA_SOURCE_DAILY, RequestLog.resource_cost), else_=0)
    return RequestLog.resource_cost


def _addon_resource_cost_case():
    if is_new_quota_mode_enabled():
        return case((RequestLog.quota_source == QUOTA_SOURCE_ADDON, RequestLog.resource_cost), else_=0)
    return 0


@router.post("/login")
async def login(
    request: Request,
    db: Session = Depends(get_db),
    user_credentials: UserLogin = Body(...),
):
    if _is_v2_request(request):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if not settings.API_V1_LEGACY_LOGIN_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    enforce_auth_attempt_rate_limit(
        request,
        scope_prefix="legacy_login",
        ip_limit=settings.LEGACY_LOGIN_IP_LIMIT,
        identity=user_credentials.username,
        identity_limit=settings.LEGACY_LOGIN_IDENTITY_LIMIT,
    )
    logger.info("[LOGIN] Attempting legacy login for user=%s", user_credentials.username)

    user = db.query(User).filter(User.username == user_credentials.username).first()

    if not user:
        logger.warning("[LOGIN] Legacy user not found: %s", user_credentials.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    logger.info("[LOGIN] Found legacy user=%s active=%s admin=%s", user.username, user.is_active, user.is_admin)

    if not verify_password(user_credentials.password, user.password):
        logger.warning("[LOGIN] Legacy password verification failed for user=%s", user_credentials.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        logger.warning("[LOGIN] Legacy user disabled: %s", user_credentials.username)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    if bool(getattr(user, "is_default_test_user", False)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Guest trial must use the guest auth endpoint")
    if bool(getattr(user, "is_admin", False)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin accounts must use /v2/adminapi/auth/login")
    if has_bound_email(getattr(user, "email", None)) or int(getattr(user, "identity_version", 1) or 1) >= 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Legacy login is only available for accounts that have not completed migration",
        )

    logger.info("[LOGIN] Legacy login succeeded for user=%s", user_credentials.username)

    access_token, _, _ = issue_noninteractive_login_artifacts(db, user, request, session_type="legacy")
    db.commit()
    return {"message": "Login successful", "access_token": access_token}

@router.get("/latestVersionInfo")
async def latest_version_info():
    if not settings.LEGACY_CLIENT_COMPAT:
        raise HTTPException(status_code=404, detail="Not Found")

    latest_version = (settings.LATEST_VERSION or "").strip()
    package_base_url = (settings.PACKAGE_BASE_URL or "").strip()
    package_type = settings.PACKAGE_TYPE or ""

    if latest_version and package_base_url:
        return {
            "version": latest_version,
            "packgeURL": f"{package_base_url}{latest_version}{package_type}",
        }

    return Response(
        content='{"message":"version not defined"}',
        media_type="application/json",
        status_code=460,
    )


@router.get("/account/status")
async def account_status(
    db: Session = Depends(get_db),
    current_user: User | V2User = Depends(get_current_api_user_for_status),
):
    if _is_test_user(current_user.username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "test_user_unsupported", "message": "测试账户不支持查询账户到期信息"},
        )

    expiration_utc = None
    remaining_seconds = None
    boost_expiration_utc = None
    boost_remaining_seconds = None
    is_expired = not bool(current_user.is_active)

    quota_snapshot = build_quota_snapshot(db, current_user)

    return {
        "username": current_user.username,
        "user_uuid": str(getattr(current_user, "user_uuid", None) or current_user.username or "").strip(),
        "is_active": bool(current_user.is_active),
        "is_expired": is_expired,
        "expiration_utc": expiration_utc,
        "remaining_seconds": remaining_seconds,
        "boost_expiration_utc": boost_expiration_utc,
        "boost_remaining_seconds": boost_remaining_seconds,
        "boost_limit_rule": current_user.boost_limit_rule,
        "daily_limit": quota_snapshot.daily_limit,
        "today_daily_resource_used": quota_snapshot.today_daily_used,
        "remaining_daily": quota_snapshot.remaining_daily,
        "addon_resource_balance": quota_snapshot.addon_resource_balance,
        "addon_resource_total": quota_snapshot.addon_resource_total,
        "addon_expiration_utc": quota_snapshot.addon_expiration_utc,
    }


@router.get("/account/usage")
async def account_usage(
    db: Session = Depends(get_db),
    current_user: User | V2User = Depends(get_current_api_user_for_status),
    start_date: Optional[date] = Query(default=None, description="Start date in YYYY-MM-DD format"),
    end_date: Optional[date] = Query(default=None, description="End date in YYYY-MM-DD format"),
):
    if _is_test_user(current_user.username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "test_user_unsupported", "message": "\u6d4b\u8bd5\u8d26\u6237\u4e0d\u652f\u6301\u67e5\u8be2\u7528\u91cf\u548c\u5230\u671f\u4fe1\u606f"},
        )

    now = datetime.now(timezone.utc)
    today_utc = now.date()

    if start_date is None and end_date is None:
        query_end_date = today_utc
        query_start_date = today_utc - timedelta(days=DEFAULT_USAGE_DAYS - 1)
    elif start_date is None:
        query_end_date = end_date
        query_start_date = end_date
    elif end_date is None:
        query_start_date = start_date
        query_end_date = start_date
    else:
        query_start_date = start_date
        query_end_date = end_date

    if query_start_date > query_end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_date_range", "message": "start_date cannot be later than end_date"},
        )

    if (query_end_date - query_start_date).days > 365:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "date_range_too_large", "message": "date range cannot exceed 366 days"},
        )

    range_start = datetime.combine(query_start_date, datetime.min.time())
    range_end_exclusive = datetime.combine(query_end_date + timedelta(days=1), datetime.min.time())

    range_filters = _append_usage_excluded_filters(
        [
            RequestLog.username == current_user.username,
            RequestLog.timestamp >= range_start,
            RequestLog.timestamp < range_end_exclusive,
        ]
    )

    usage_row = (
        db.query(
            func.sum(case((RequestLog.status == "success", 1), else_=0)).label("success_count"),
            func.sum(case((RequestLog.status == "failed", 1), else_=0)).label("failed_count"),
            func.sum(case((RequestLog.status == "rate_limited", 1), else_=0)).label("rate_limited_count"),
            func.sum(RequestLog.resource_cost).label("resource_cost_sum"),
            func.sum(_daily_resource_cost_case()).label("daily_resource_cost_sum"),
            func.sum(_addon_resource_cost_case()).label("addon_resource_cost_sum"),
            func.sum(case((RequestLog.resource_deducted == True, 1), else_=0)).label("deducted_request_count"),  # noqa: E712
            func.count().label("total_count"),
        )
        .filter(*range_filters)
        .first()
    )

    success_count = int((usage_row.success_count if usage_row and usage_row.success_count is not None else 0) or 0)
    failed_count = int((usage_row.failed_count if usage_row and usage_row.failed_count is not None else 0) or 0)
    rate_limited_count = int((usage_row.rate_limited_count if usage_row and usage_row.rate_limited_count is not None else 0) or 0)
    resource_cost_sum = int((usage_row.resource_cost_sum if usage_row and usage_row.resource_cost_sum is not None else 0) or 0)
    daily_resource_cost_sum = int((usage_row.daily_resource_cost_sum if usage_row and usage_row.daily_resource_cost_sum is not None else 0) or 0)
    addon_resource_cost_sum = int((usage_row.addon_resource_cost_sum if usage_row and usage_row.addon_resource_cost_sum is not None else 0) or 0)
    deducted_request_count = int((usage_row.deducted_request_count if usage_row and usage_row.deducted_request_count is not None else 0) or 0)
    total_count = int((usage_row.total_count if usage_row and usage_row.total_count is not None else 0) or 0)

    today_range_start = datetime.combine(today_utc, datetime.min.time())
    today_range_end_exclusive = datetime.combine(today_utc + timedelta(days=1), datetime.min.time())
    today_filters = _append_usage_excluded_filters(
        [
            RequestLog.username == current_user.username,
            RequestLog.timestamp >= today_range_start,
            RequestLog.timestamp < today_range_end_exclusive,
        ]
    )
    today_usage_row = (
        db.query(
            func.sum(case((RequestLog.status == "success", 1), else_=0)).label("success_count"),
            func.count().label("total_count"),
        )
        .filter(*today_filters)
        .first()
    )
    today_success_count = int((today_usage_row.success_count if today_usage_row and today_usage_row.success_count is not None else 0) or 0)
    today_total_count = int((today_usage_row.total_count if today_usage_row and today_usage_row.total_count is not None else 0) or 0)

    limit_rule = get_effective_user_limit_rule(current_user) or ""
    daily_limit = _extract_daily_limit(limit_rule)
    quota_snapshot = build_quota_snapshot(db, current_user)

    date_expr = func.date(RequestLog.timestamp)
    daily_rows = (
        db.query(
            date_expr.label("day"),
            func.sum(case((RequestLog.status == "success", 1), else_=0)).label("daily_success_count"),
            func.sum(case((RequestLog.status == "failed", 1), else_=0)).label("daily_failed_count"),
            func.sum(case((RequestLog.status == "rate_limited", 1), else_=0)).label("daily_rate_limited_count"),
            func.sum(RequestLog.resource_cost).label("daily_resource_cost"),
            func.sum(_daily_resource_cost_case()).label("daily_subscription_resource_cost"),
            func.sum(_addon_resource_cost_case()).label("daily_addon_resource_cost"),
            func.sum(case((RequestLog.resource_deducted == True, 1), else_=0)).label("daily_deducted_request_count"),  # noqa: E712
            func.count().label("daily_total_count"),
        )
        .filter(*range_filters)
        .group_by(date_expr)
        .order_by(date_expr.asc())
        .all()
    )

    daily_map = {}
    for row in daily_rows:
        if hasattr(row.day, "strftime"):
            day_text = row.day.strftime("%Y-%m-%d")
        else:
            day_text = str(row.day)

        day_success_count = int((row.daily_success_count or 0) or 0)
        day_resource_cost = int((row.daily_resource_cost or 0) or 0)
        day_subscription_resource_cost = int((row.daily_subscription_resource_cost or 0) or 0)
        day_addon_resource_cost = int((row.daily_addon_resource_cost or 0) or 0)
        daily_map[day_text] = {
            "date": day_text,
            "day": day_text,
            "daily_success_count": day_success_count,
            "daily_failed_count": int((row.daily_failed_count or 0) or 0),
            "daily_rate_limited_count": int((row.daily_rate_limited_count or 0) or 0),
            "daily_resource_cost": day_resource_cost,
            "daily_subscription_resource_cost": day_subscription_resource_cost,
            "daily_addon_resource_cost": day_addon_resource_cost,
            "daily_deducted_request_count": int((row.daily_deducted_request_count or 0) or 0),
            "daily_total_count": int((row.daily_total_count or 0) or 0),
            "daily_limit": daily_limit,
            "remaining_daily": None if daily_limit is None else max(0, daily_limit - day_subscription_resource_cost),
        }

    daily_stats = []
    cursor = query_start_date
    while cursor <= query_end_date:
        cursor_text = cursor.strftime("%Y-%m-%d")
        daily_stats.append(
            daily_map.get(
                cursor_text,
                {
                    "date": cursor_text,
                    "day": cursor_text,
                    "daily_success_count": 0,
                    "daily_failed_count": 0,
                    "daily_rate_limited_count": 0,
                    "daily_resource_cost": 0,
                    "daily_subscription_resource_cost": 0,
                    "daily_addon_resource_cost": 0,
                    "daily_deducted_request_count": 0,
                    "daily_total_count": 0,
                    "daily_limit": daily_limit,
                    "remaining_daily": daily_limit,
                },
            )
        )
        cursor += timedelta(days=1)

    end_date_text = query_end_date.strftime("%Y-%m-%d")
    end_day_subscription_resource_cost = int(daily_map.get(end_date_text, {}).get("daily_subscription_resource_cost", 0) or 0)
    remaining_daily = None if daily_limit is None else max(0, daily_limit - end_day_subscription_resource_cost)

    expiration_utc = None
    remaining_seconds = None
    is_expired = not bool(current_user.is_active)

    return {
        "username": current_user.username,
        "user_uuid": str(getattr(current_user, "user_uuid", None) or current_user.username or "").strip(),
        "is_test_user": bool(getattr(current_user, "is_default_test_user", False)),
        "is_active": bool(current_user.is_active),
        "is_expired": is_expired,
        "expiration_utc": expiration_utc,
        "remaining_seconds": remaining_seconds,
        "limit_rule": limit_rule,
        "daily_limit": daily_limit,
        "remaining_daily": remaining_daily,
        "daily_resource_limit": daily_limit,
        "remaining_resource": remaining_daily,
        "resource_used": resource_cost_sum,
        "subscription_resource_used": daily_resource_cost_sum,
        "addon_resource_used": addon_resource_cost_sum,
        "resource_deducted_request_count": deducted_request_count,
        "daily_success_count": success_count,
        "daily_failed_count": failed_count,
        "daily_rate_limited_count": rate_limited_count,
        "daily_total_count": total_count,
        "today_success_count": today_success_count,
        "today_total_count": today_total_count,
        "today_daily_resource_used": quota_snapshot.today_daily_used,
        "today_remaining_daily": quota_snapshot.remaining_daily,
        "addon_resource_balance": quota_snapshot.addon_resource_balance,
        "addon_resource_total": quota_snapshot.addon_resource_total,
        "addon_expiration_utc": quota_snapshot.addon_expiration_utc,
        "query_start_date": query_start_date.strftime("%Y-%m-%d"),
        "query_end_date": query_end_date.strftime("%Y-%m-%d"),
        "daily_stats": daily_stats,
    }

@router.post("/whisper/transcriptions")
async def whisper_transcriptions(
    request: Request,
    file: UploadFile = File(...),
    engine: Optional[str] = Form(None),
    asrEngine: Optional[str] = Form(None),
    current_user: User = Depends(enforce_user_rate_limit),
):
    asr_engine = _resolve_chinese_asr_engine(engine, asrEngine, "WHISPER_TRANSCRIPTIONS")
    _apply_effective_resource_cost(request, 1)
    audio_file = await file.read()
    text = await do_transcribe(audio_file, language='zh', engine=asr_engine, filename=file.filename or "audio.wav")
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}
    # 压缩重复字符
    if settings.ENABLE_TEXT_COMPRESSION:
        text = compress_repeated_chars(text, settings.TEXT_COMPRESSION_MIN_REPEAT)
    return {"text": text}

@router.post("/whisper/translations")
async def whisper_translations(
    request: Request,
    file: UploadFile = File(...),
    engine: Optional[str] = Form(None),
    asrEngine: Optional[str] = Form(None),
    current_user: User = Depends(enforce_user_rate_limit),
):
    asr_engine = _resolve_audio_translation_asr_engine(engine, asrEngine, "WHISPER_TRANSLATIONS")
    _apply_effective_resource_cost(request, 2)
    audio_file = await file.read()
    text = await do_translate_audio(audio_file, engine=asr_engine, filename=file.filename or "audio.wav")
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}
    # 压缩重复字符
    if settings.ENABLE_TEXT_COMPRESSION:
        text = compress_repeated_chars(text, settings.TEXT_COMPRESSION_MIN_REPEAT)
    return {"text": text}

@router.post("/func/translateToEnglish")
async def translate_to_english(
    request: Request,
    file: UploadFile = File(...),
    engine: Optional[str] = Form(None),
    asrEngine: Optional[str] = Form(None),
    current_user: User = Depends(enforce_user_rate_limit),
):
    asr_engine = _resolve_chinese_asr_engine(engine, asrEngine, "TRANSLATE_TO_ENGLISH")
    _apply_effective_resource_cost(request, 2)
    audio_file = await file.read()
    text = await do_transcribe(audio_file, language='zh', engine=asr_engine, filename=file.filename or "audio.wav")
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}
    # 压缩重复字符
    if settings.ENABLE_TEXT_COMPRESSION:
        text = compress_repeated_chars(text, settings.TEXT_COMPRESSION_MIN_REPEAT)
    if settings.ENABLE_WEB_TRANSLATORS:
        translated_text = do_translate(text, from_='zh', to="en")
    else:
        translated_text = await do_translate_audio(audio_file, engine=asr_engine, filename=file.filename or "audio.wav")
    return {"text": text, "translatedText": translated_text}

@router.post("/func/translateToOtherLanguage")
async def translate_to_other_language(
    request: Request,
    file: UploadFile = File(...),
    targetLanguage: str = Form(...),
    engine: Optional[str] = Form(None),
    asrEngine: Optional[str] = Form(None),
    current_user: User = Depends(enforce_user_rate_limit),
):
    asr_engine = _resolve_chinese_asr_engine(engine, asrEngine, "TRANSLATE_TO_OTHER_LANGUAGE")
    targetLanguage = _validate_audio_target_language("targetLanguage", targetLanguage, asr_engine=asr_engine)
    _apply_effective_resource_cost(request, 2)
    audio_file = await file.read()
    text = await do_transcribe(audio_file, language='zh', engine=asr_engine, filename=file.filename or "audio.wav")
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}

    if asr_engine_supports_direct_translation(asr_engine) and not settings.ENABLE_WEB_TRANSLATORS:
        trans_text = await do_transcribe(audio_file, language=targetLanguage, engine=asr_engine, filename=file.filename or "audio.wav")
    elif settings.ENABLE_WEB_TRANSLATORS:
        trans_text = do_translate(text, from_='zh', to=targetLanguage)
    else:
        translated_text = await do_translate_audio(audio_file, engine=asr_engine, filename=file.filename or "audio.wav")
        if targetLanguage == 'en':
            trans_text = translated_text
        else:
            logger.info("[TRANSLATE] LibreTranslate 已移除，回退至 Web 翻译服务")
            trans_text = do_translate(translated_text, from_="en", to=targetLanguage)
    return {"text": text, "translatedText": trans_text}

@router.post("/func/multitranslateToOtherLanguage")
async def multitranslate_to_other_language(
    request: Request,
    file: UploadFile = File(...),
    targetLanguage: str = Form(...),
    sourceLanguage: str = Form(...),
    targetLanguage2: str = Form("none"),
    targetLanguage3: str = Form("none"),
    emojiOutput: str = Form('true'),
    engine: Optional[str] = Form(None),
    asrEngine: Optional[str] = Form(None),
    current_user: User = Depends(enforce_user_rate_limit)
):
    # supportedLanguagesList 现在是硬编码的固定列表，不需要动态初始化
    logger.info(f"[MULTITRANSLATE] 开始多语言翻译请求 - 用户: {current_user.username}, 文件: {file.filename}, 大小: {len(file.file.read()) if hasattr(file, 'file') else 'unknown'} bytes")
    file.file.seek(0)  # 重置文件指针
    logger.info(f"[MULTITRANSLATE] 参数详情 - sourceLanguage: {sourceLanguage}, targetLanguage: {targetLanguage}, targetLanguage2: {targetLanguage2}, targetLanguage3: {targetLanguage3}, emojiOutput: {emojiOutput}")
    logger.info(f"[MULTITRANSLATE] 支持的语言列表长度: {len(supportedLanguagesList)}, whisper支持的语言列表长度: {len(whisperSupportedLanguageList)}")
    logger.debug(f"[MULTITRANSLATE] 支持的语言列表: {supportedLanguagesList}")
    logger.debug(f"[MULTITRANSLATE] whisper支持的语言列表: {whisperSupportedLanguageList}")

    _reject_auto_language("sourceLanguage", sourceLanguage)

    if sourceLanguage not in whisperSupportedLanguageList:
        logger.warning(f"[MULTITRANSLATE] sourceLanguage错误: {sourceLanguage} 不在支持的列表中")
        logger.debug(f"[MULTITRANSLATE] 支持的语言列表: {whisperSupportedLanguageList}")
        # 参数错误返回400，避免客户端误判为token过期
        raise HTTPException(status_code=400, detail="sourceLanguage error")

    source_asr_engine = _resolve_source_asr_engine(engine, asrEngine, sourceLanguage, "MULTITRANSLATE")
    logger.info("[MULTITRANSLATE] effective ASR engine: %s", source_asr_engine)
    targetLanguage = _validate_audio_target_language("targetLanguage", targetLanguage, asr_engine=source_asr_engine)
    targetLanguage2 = _validate_audio_target_language("targetLanguage2", targetLanguage2, allow_none=True, asr_engine=source_asr_engine)
    targetLanguage3 = _validate_audio_target_language("targetLanguage3", targetLanguage3, allow_none=True, asr_engine=source_asr_engine)

    translation_count = 1 + int(targetLanguage2 != "none") + int(targetLanguage3 != "none")
    _apply_effective_resource_cost(request, 1 + translation_count)

    logger.info(f"[MULTITRANSLATE] 开始处理音频文件 - content_type: {file.content_type}")
    audio_file = await file.read()
    logger.info(f"[MULTITRANSLATE] 音频文件读取完成 - 大小: {len(audio_file)} bytes")

    if file.content_type == 'audio/opus':
        logger.info(f"[MULTITRANSLATE] 检测到opus格式，开始转换为wav")
        audio_file = packaged_opus_stream_to_wav_bytes(audio_file, 16000)
        logger.info(f"[MULTITRANSLATE] opus转换完成 - 新大小: {len(audio_file)} bytes")

    stext = ""
    if sourceLanguage == 'zh':
        logger.info("[MULTITRANSLATE] using Chinese ASR engine for zh transcription: %s", source_asr_engine)
        stext = await do_transcribe(audio_file, language=sourceLanguage, engine=source_asr_engine, filename="audio.wav")
        logger.info("[MULTITRANSLATE] ASR result: %s", stext)
        if emojiOutput == 'true':
            stext = emoji.replace_emoji(stext, replace='')
        if stext == '。' or stext == '':
            return {"text": "", "message": "filtered"}
        if settings.ENABLE_TEXT_COMPRESSION:
            stext = compress_repeated_chars(stext, settings.TEXT_COMPRESSION_MIN_REPEAT)
        logger.info("[MULTITRANSLATE] zh transcription completed by engine=%s", source_asr_engine)
    else:
        logger.info(
            "[MULTITRANSLATE] using full Whisper ASR chain for sourceLanguage=%s engine=%s",
            sourceLanguage,
            source_asr_engine,
        )
        logger.debug(f"[MULTITRANSLATE] 先进行Whisper中文过滤检测")
        filter_text = await do_transcribe(audio_file, language="zh", engine=source_asr_engine, filename="audio.wav")
        logger.info(f"[MULTITRANSLATE] 中文过滤检测结果: '{filter_text}'")
        if (filter_text in errorFilter.get("errorResultDict", [])) or any(key in filter_text for key in errorFilter.get("errorKeyString", [])):
            logger.warning(f"[MULTITRANSLATE] 内容被过滤规则拦截 检测内容: '{filter_text}'")
            return {"text": "", "message": "filtered"}
        logger.debug(f"[MULTITRANSLATE] 进行{sourceLanguage}语言识别")
        stext = await do_transcribe(audio_file, language=sourceLanguage, engine=source_asr_engine, filename="audio.wav")
        logger.info(f"[MULTITRANSLATE] {sourceLanguage}语音识别结果: '{stext}'")
        # 压缩重复字符
        if settings.ENABLE_TEXT_COMPRESSION:
            original_stext = stext
            stext = compress_repeated_chars(stext, settings.TEXT_COMPRESSION_MIN_REPEAT)
            if original_stext != stext:
                logger.info(f"[MULTITRANSLATE] 压缩重复字符 - 原始: '{original_stext}' -> 处理后: '{stext}'")

    transText, transText2, transText3 = '', '', ''
    logger.info(f"[MULTITRANSLATE] 开始翻译流程 - ENABLE_WEB_TRANSLATORS: {settings.ENABLE_WEB_TRANSLATORS}")

    if settings.ENABLE_WEB_TRANSLATORS:
        translate_source_lang = sourceLanguage
        logger.info(f"[MULTITRANSLATE] 使用网页翻译服务 - 源语言: {translate_source_lang}")
        logger.debug(f"[MULTITRANSLATE] 开始翻译到目标语言: {targetLanguage}")
        transText = do_translate(stext, from_=translate_source_lang, to=targetLanguage)
        logger.info(f"[MULTITRANSLATE] 主目标语言翻译完成: '{transText}'")

        if targetLanguage2 != "none":
            logger.debug(f"[MULTITRANSLATE] 开始翻译到第二目标语言: {targetLanguage2}")
            transText2 = do_translate(stext, from_=translate_source_lang, to=targetLanguage2)
            logger.info(f"[MULTITRANSLATE] 第二目标语言翻译完成: '{transText2}'")
        if targetLanguage3 != "none":
            logger.debug(f"[MULTITRANSLATE] 开始翻译到第三目标语言: {targetLanguage3}")
            transText3 = do_translate(stext, from_=translate_source_lang, to=targetLanguage3)
            logger.info(f"[MULTITRANSLATE] 第三目标语言翻译完成: '{transText3}'")
    elif asr_engine_supports_direct_translation(source_asr_engine):
        logger.info("[MULTITRANSLATE] 使用 WhisperLive-TRT 内置翻译")
        transText = await do_transcribe(audio_file, language=targetLanguage, engine=source_asr_engine, filename="audio.wav")
        if targetLanguage2 != "none":
            transText2 = await do_transcribe(audio_file, language=targetLanguage2, engine=source_asr_engine, filename="audio.wav")
        if targetLanguage3 != "none":
            transText3 = await do_transcribe(audio_file, language=targetLanguage3, engine=source_asr_engine, filename="audio.wav")
    else:
        logger.info(f"[MULTITRANSLATE] 使用Whisper翻译服务")
        logger.debug(f"[MULTITRANSLATE] 创建Whisper翻译请求")
        translated_text = await do_translate_audio(audio_file, engine=source_asr_engine, filename="audio.wav")
        logger.info(f"[MULTITRANSLATE] Whisper翻译结果: '{translated_text}'")

        if targetLanguage == 'en':
            transText = translated_text
            logger.info(f"[MULTITRANSLATE] 目标语言为英文，直接使用Whisper翻译结果")
        else:
            logger.debug(f"[MULTITRANSLATE] LibreTranslate已移除，使用网页翻译从英文到{targetLanguage}")
            transText = do_translate(translated_text, from_="en", to=targetLanguage)
            logger.info(f"[MULTITRANSLATE] 网页翻译完成: '{transText}'")

        if targetLanguage2 != "none":
            logger.debug(f"[MULTITRANSLATE] 翻译到第二目标语言: {targetLanguage2}")
            transText2 = do_translate(translated_text, from_='en', to=targetLanguage2)
            logger.info(f"[MULTITRANSLATE] 第二目标语言翻译完成: '{transText2}'")
        if targetLanguage3 != "none":
            logger.debug(f"[MULTITRANSLATE] 翻译到第三目标语言: {targetLanguage3}")
            transText3 = do_translate(translated_text, from_='en', to=targetLanguage3)
            logger.info(f"[MULTITRANSLATE] 第三目标语言翻译完成: '{transText3}'")

    logger.info(f"[MULTITRANSLATE] 多语言翻译请求处理完成 - 用户: {current_user.username}  原文: '{stext}', 主译文: '{transText}', 第二译文: '{transText2}', 第三译文: '{transText3}'")

    return {'text': stext, 'translatedText': transText, 'translatedText2': transText2, 'translatedText3': transText3}

@router.post("/whisper/multitranscription")
async def multitranscription(
    request: Request,
    file: UploadFile = File(...),
    sourceLanguage: str = Form(...),
    emojiOutput: str = Form('true'),
    engine: Optional[str] = Form(None),
    asrEngine: Optional[str] = Form(None),
    current_user: User = Depends(enforce_user_rate_limit)
):
    _apply_effective_resource_cost(request, 1)
    _reject_auto_language("sourceLanguage", sourceLanguage)
    if sourceLanguage not in whisperSupportedLanguageList:
        raise HTTPException(status_code=400, detail="sourceLanguage error")

    source_asr_engine = _resolve_source_asr_engine(engine, asrEngine, sourceLanguage, "MULTITRANSCRIPTION")
    audio_file = await file.read()
    if file.content_type == 'audio/opus':
        audio_file = packaged_opus_stream_to_wav_bytes(audio_file, 16000)

    text_to_return = ""
    if sourceLanguage != 'zh':
        filter_text = await do_transcribe(audio_file, language="zh", engine=source_asr_engine, filename="audio.wav")
        if (filter_text in errorFilter.get("errorResultDict", [])) or any(key in filter_text for key in errorFilter.get("errorKeyString", [])):
            return {"text": "", "message": "filtered"}
        text_to_return = await do_transcribe(audio_file, language=sourceLanguage, engine=source_asr_engine, filename="audio.wav")
    else:
        text_to_return = await do_transcribe(audio_file, language=sourceLanguage, engine=source_asr_engine, filename="audio.wav")
        if emojiOutput == 'true': text_to_return = emoji.replace_emoji(text_to_return, replace='')
        if text_to_return == '。' or text_to_return == '': return {"text": "", "message": "filtered"}

    return {"text": text_to_return}


@router.post("/func/photoTranslate")
async def photo_translate(
    request: Request,
    file: UploadFile = File(...),
    sourceLanguage: str = Form("auto"),
    targetLanguage: str = Form(...),
    targetLanguage2: str = Form("none"),
    targetLanguage3: str = Form("none"),
    current_user: User = Depends(enforce_user_rate_limit),
):
    targetLanguage = _validate_runtime_target_language("targetLanguage", targetLanguage)
    targetLanguage2 = _validate_runtime_target_language("targetLanguage2", targetLanguage2, allow_none=True)
    targetLanguage3 = _validate_runtime_target_language("targetLanguage3", targetLanguage3, allow_none=True)

    requested_source_language = (sourceLanguage or "").strip()
    if requested_source_language and requested_source_language.lower() != "auto":
        sourceLanguage = _validate_runtime_source_language("sourceLanguage", requested_source_language)
    else:
        sourceLanguage = "auto"

    translation_count = 1 + int(targetLanguage2 != "none") + int(targetLanguage3 != "none")
    _apply_effective_resource_cost(request, 1 + translation_count)

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="image file is empty")

    try:
        result = await translate_image_to_targets_async(
            image_bytes=image_bytes,
            media_type=file.content_type,
            source_language=sourceLanguage,
            target_language=targetLanguage,
            target_language2=targetLanguage2,
            target_language3=targetLanguage3,
        )
    except RuntimeError as exc:
        logger.warning("[PHOTO_TRANSLATE] service unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[PHOTO_TRANSLATE] unexpected error")
        raise HTTPException(status_code=500, detail=f"photo translate failed: {exc}") from exc

    return result

class TTSRequest(BaseModel):
    input: str
    voice: str
    speed: float = 1.0
    response_format: Optional[str] = None
    language: Optional[str] = None
    instruct: Optional[str] = None
    volume: Optional[float] = None


def _validate_tts_volume(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if value < 0.0 or value > 4.0:
        raise HTTPException(status_code=400, detail="volume must be between 0 and 4")
    return value


def _validate_edge_tts_input(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="input text is empty")
    max_chars = max(1, int(settings.EDGE_TTS_MAX_INPUT_CHARS or 500))
    if len(normalized) > max_chars:
        raise HTTPException(status_code=400, detail=f"input text exceeds {max_chars} characters")
    return normalized


def _tts_proxy_url() -> str:
    if not settings.EDGE_TTS_ENABLED:
        raise HTTPException(status_code=403, detail="Edge TTS is disabled")
    edge_url = (settings.TTS_URL or "").strip()
    if edge_url:
        return edge_url
    raise HTTPException(status_code=500, detail="Edge TTS service not configured")


def _tts_proxy_headers() -> dict[str, str]:
    token = settings.TTS_TOKEN
    token = (token or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}

@router.post("/func/tts")
async def tts_proxy(
    request: Request,
    data: TTSRequest,
    current_user: User = Depends(enforce_user_rate_limit),
):
    _apply_effective_resource_cost(request, 1)
    text = _validate_edge_tts_input(data.input)
    volume = _validate_tts_volume(data.volume)
    tts_url = _tts_proxy_url()
    payload = {"model": "tts-1", "input": text, "voice": data.voice, "speed": data.speed}
    if data.response_format:
        payload["response_format"] = data.response_format
    if data.language:
        payload["language"] = data.language
    if data.instruct:
        payload["instruct"] = data.instruct
    if volume is not None:
        payload["volume"] = volume
    headers = _tts_proxy_headers()
    request.state.audit_payload = {
        "feature": "edge_tts",
        "engine": "edge",
        "resource_cost": 1,
        "voice": data.voice,
        "response_format": data.response_format or "",
        "volume": volume,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(tts_url, json=payload, headers=headers, timeout=settings.EDGE_TTS_TIMEOUT)
        return Response(
            content=response.content,
            media_type=response.headers.get("Content-Type", "audio/wav"),
            status_code=response.status_code,
        )

class WebTranslateRequest(BaseModel):
    text: str
    targetLanguage: str
    sourceLanguage: str
    targetLanguage2: str = "none"
    targetLanguage3: str = "none"


def _validate_web_translate_language(field_name: str, language: str, allow_none: bool = False) -> str:
    if field_name == "sourceLanguage":
        return _validate_runtime_source_language(field_name, language, allow_none=allow_none)
    return _validate_runtime_target_language(field_name, language, allow_none=allow_none)

@router.post("/func/webtranslate")
async def web_translate(
    request: Request,
    data: WebTranslateRequest,
    current_user: User = Depends(enforce_user_rate_limit),
):
    requested_source_language = (data.sourceLanguage or "").strip()
    if requested_source_language.lower() == "auto":
        source_language = "auto"
    else:
        source_language = _validate_web_translate_language("sourceLanguage", requested_source_language)
    target_language = _validate_web_translate_language("targetLanguage", data.targetLanguage)
    target_language2 = _validate_web_translate_language("targetLanguage2", data.targetLanguage2, allow_none=True)
    target_language3 = _validate_web_translate_language("targetLanguage3", data.targetLanguage3, allow_none=True)

    effective_source_language = source_language
    if source_language == "auto":
        effective_source_language = detect_translation_source_language(
            data.text,
            allowed_languages=get_runtime_translation_source_languages(),
        )
        logger.info(
            "[WEBTRANSLATE] Auto-detected source language. requested=%s, detected=%s, target=%s",
            requested_source_language,
            effective_source_language,
            target_language,
        )

    translation_count = 1 + int(data.targetLanguage2 != "none") + int(data.targetLanguage3 != "none")
    _apply_effective_resource_cost(request, translation_count)
    transText = do_translate(data.text, effective_source_language, target_language)
    transText2 = ''
    transText3 = ''
    if target_language2 != "none": transText2 = do_translate(data.text, effective_source_language, target_language2)
    if target_language3 != "none": transText3 = do_translate(data.text, effective_source_language, target_language3)
    return {'text': data.text, 'translatedText': transText, 'translatedText2': transText2, 'translatedText3': transText3}
