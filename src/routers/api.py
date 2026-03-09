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
    create_access_token,
    verify_password,
    get_current_user_for_status,
    is_user_expired,
    get_effective_user_limit_rule,
)
from core.rate_limiter import enforce_user_rate_limit, set_user_context_cost
from core.config import settings
from db.models import User, RequestLog
from core.services import (
    errorFilter, do_translate,
    supportedLanguagesList, whisperSupportedLanguageList, SENSEVOICE_URL, packaged_opus_stream_to_wav_bytes,
    compress_repeated_chars, do_transcribe, do_translate_audio
)
from core.logging_config import logger
# 导入用于接收JSON体的Pydantic模型
from schemas.user import UserLogin

router = APIRouter()
DAY_LIMIT_RULE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*day\s*$", re.IGNORECASE)
DEFAULT_USAGE_DAYS = 7


def _extract_daily_limit(limit_rule: str) -> Optional[int]:
    if not limit_rule:
        return None
    for part in (segment.strip() for segment in limit_rule.split(";") if segment.strip()):
        matched = DAY_LIMIT_RULE_PATTERN.match(part)
        if matched:
            return int(matched.group(1))
    return None


def _is_test_user(username: str) -> bool:
    configured = (settings.LIMIT_PUBLIC_TEST_USER or "").strip()
    if configured and username == configured:
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

# --- API 路由 ---

@router.post("/login")
# 将函数签名修改为接收 UserLogin 模型作为请求体
async def login(db: Session = Depends(get_db), user_credentials: UserLogin = Body(...)):
    # 从 Pydantic 模型中获取数据
    logger.info(f"[LOGIN] 尝试登录用户: {user_credentials.username}")

    user = db.query(User).filter(User.username == user_credentials.username).first()

    if not user:
        logger.warning(f"[LOGIN] 用户不存在: {user_credentials.username}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    logger.info(f"[LOGIN] 找到用户: {user.username}, is_active: {user.is_active}, is_admin: {user.is_admin}")
    logger.info(f"[LOGIN] 数据库存储的密码哈希: {user.password[:20]}...")  # 只显示前20个字符用于调试

    if not verify_password(user_credentials.password, user.password):
        logger.warning(f"[LOGIN] 密码验证失败 for user: {user_credentials.username}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        logger.warning(f"[LOGIN] 用户被禁用: {user_credentials.username}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已被禁用")

    expired, expiration_utc = is_user_expired(user)
    if expired:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "account_expired",
                "message": "用户已过期",
                "expiration_utc": expiration_utc.isoformat().replace("+00:00", "Z") if expiration_utc else None,
            },
        )

    logger.info(f"[LOGIN] 用户登录成功: {user_credentials.username}")
    access_token = create_access_token(data={"sub": user.username})
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
async def account_status(current_user: User = Depends(get_current_user_for_status)):
    if _is_test_user(current_user.username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "test_user_unsupported", "message": "测试账户不支持查询账户到期信息"},
        )

    expires_at = current_user.expiration_date
    boost_expires_at = current_user.boost_expiration_date
    now = datetime.now(timezone.utc)
    expiration_utc = None
    remaining_seconds = None
    is_expired = False
    boost_expiration_utc = None
    boost_remaining_seconds = None

    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)

        expiration_utc = expires_at.isoformat().replace("+00:00", "Z")
        remaining_seconds = int((expires_at - now).total_seconds())
        is_expired = remaining_seconds <= 0

    if boost_expires_at is not None:
        if boost_expires_at.tzinfo is None:
            boost_expires_at = boost_expires_at.replace(tzinfo=timezone.utc)
        else:
            boost_expires_at = boost_expires_at.astimezone(timezone.utc)
        boost_expiration_utc = boost_expires_at.isoformat().replace("+00:00", "Z")
        boost_remaining_seconds = int((boost_expires_at - now).total_seconds())

    return {
        "username": current_user.username,
        "is_active": bool(current_user.is_active),
        "is_expired": is_expired,
        "expiration_utc": expiration_utc,
        "remaining_seconds": remaining_seconds,
        "boost_expiration_utc": boost_expiration_utc,
        "boost_remaining_seconds": boost_remaining_seconds,
        "boost_limit_rule": current_user.boost_limit_rule,
    }


@router.get("/account/usage")
async def account_usage(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_for_status),
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

    usage_row = (
        db.query(
            func.sum(case((RequestLog.status == "success", 1), else_=0)).label("success_count"),
            func.sum(case((RequestLog.status == "failed", 1), else_=0)).label("failed_count"),
            func.sum(case((RequestLog.status == "rate_limited", 1), else_=0)).label("rate_limited_count"),
            func.sum(RequestLog.resource_cost).label("resource_cost_sum"),
            func.sum(case((RequestLog.resource_deducted == True, 1), else_=0)).label("deducted_request_count"),  # noqa: E712
            func.count().label("total_count"),
        )
        .filter(
            RequestLog.username == current_user.username,
            RequestLog.timestamp >= range_start,
            RequestLog.timestamp < range_end_exclusive,
        )
        .first()
    )

    success_count = int((usage_row.success_count if usage_row and usage_row.success_count is not None else 0) or 0)
    failed_count = int((usage_row.failed_count if usage_row and usage_row.failed_count is not None else 0) or 0)
    rate_limited_count = int((usage_row.rate_limited_count if usage_row and usage_row.rate_limited_count is not None else 0) or 0)
    resource_cost_sum = int((usage_row.resource_cost_sum if usage_row and usage_row.resource_cost_sum is not None else 0) or 0)
    deducted_request_count = int((usage_row.deducted_request_count if usage_row and usage_row.deducted_request_count is not None else 0) or 0)
    total_count = int((usage_row.total_count if usage_row and usage_row.total_count is not None else 0) or 0)

    limit_rule = get_effective_user_limit_rule(current_user) or ""
    daily_limit = _extract_daily_limit(limit_rule)

    date_expr = func.date(RequestLog.timestamp)
    daily_rows = (
        db.query(
            date_expr.label("day"),
            func.sum(case((RequestLog.status == "success", 1), else_=0)).label("daily_success_count"),
            func.sum(case((RequestLog.status == "failed", 1), else_=0)).label("daily_failed_count"),
            func.sum(case((RequestLog.status == "rate_limited", 1), else_=0)).label("daily_rate_limited_count"),
            func.sum(RequestLog.resource_cost).label("daily_resource_cost"),
            func.sum(case((RequestLog.resource_deducted == True, 1), else_=0)).label("daily_deducted_request_count"),  # noqa: E712
            func.count().label("daily_total_count"),
        )
        .filter(
            RequestLog.username == current_user.username,
            RequestLog.timestamp >= range_start,
            RequestLog.timestamp < range_end_exclusive,
        )
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
        daily_map[day_text] = {
            "date": day_text,
            "day": day_text,
            "daily_success_count": day_success_count,
            "daily_failed_count": int((row.daily_failed_count or 0) or 0),
            "daily_rate_limited_count": int((row.daily_rate_limited_count or 0) or 0),
            "daily_resource_cost": day_resource_cost,
            "daily_deducted_request_count": int((row.daily_deducted_request_count or 0) or 0),
            "daily_total_count": int((row.daily_total_count or 0) or 0),
            "daily_limit": daily_limit,
            "remaining_daily": None if daily_limit is None else max(0, daily_limit - day_resource_cost),
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
                    "daily_deducted_request_count": 0,
                    "daily_total_count": 0,
                    "daily_limit": daily_limit,
                    "remaining_daily": daily_limit,
                },
            )
        )
        cursor += timedelta(days=1)

    end_date_text = query_end_date.strftime("%Y-%m-%d")
    end_day_resource_cost = int(daily_map.get(end_date_text, {}).get("daily_resource_cost", 0) or 0)
    remaining_daily = None if daily_limit is None else max(0, daily_limit - end_day_resource_cost)

    expires_at = current_user.expiration_date
    expiration_utc = None
    remaining_seconds = None
    is_expired = False
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)
        expiration_utc = expires_at.isoformat().replace("+00:00", "Z")
        remaining_seconds = int((expires_at - now).total_seconds())
        is_expired = remaining_seconds <= 0

    return {
        "username": current_user.username,
        "is_test_user": False,
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
        "resource_deducted_request_count": deducted_request_count,
        "daily_success_count": success_count,
        "daily_failed_count": failed_count,
        "daily_rate_limited_count": rate_limited_count,
        "daily_total_count": total_count,
        "query_start_date": query_start_date.strftime("%Y-%m-%d"),
        "query_end_date": query_end_date.strftime("%Y-%m-%d"),
        "daily_stats": daily_stats,
    }

@router.post("/whisper/transcriptions")
async def whisper_transcriptions(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(enforce_user_rate_limit),
):
    _apply_effective_resource_cost(request, 1)
    audio_file = await file.read()
    text = await do_transcribe(audio_file, language='zh')
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
    current_user: User = Depends(enforce_user_rate_limit),
):
    _apply_effective_resource_cost(request, 2)
    audio_file = await file.read()
    text = await do_translate_audio(audio_file)
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
    current_user: User = Depends(enforce_user_rate_limit),
):
    _apply_effective_resource_cost(request, 2)
    audio_file = await file.read()
    text = await do_transcribe(audio_file, language='zh')
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}
    # 压缩重复字符
    if settings.ENABLE_TEXT_COMPRESSION:
        text = compress_repeated_chars(text, settings.TEXT_COMPRESSION_MIN_REPEAT)
    if settings.ENABLE_WEB_TRANSLATORS:
        translated_text = do_translate(text, from_='zh', to="en")
    else:
        translated_text = await do_translate_audio(audio_file)
    return {"text": text, "translatedText": translated_text}

@router.post("/func/translateToOtherLanguage")
async def translate_to_other_language(
    request: Request,
    file: UploadFile = File(...),
    targetLanguage: str = Form(...),
    current_user: User = Depends(enforce_user_rate_limit),
):
    _reject_auto_language("targetLanguage", targetLanguage)

    # supportedLanguagesList 现在是硬编码的固定列表，不需要动态初始化

    if targetLanguage not in supportedLanguagesList:
        # 与原有server.py保持一致的错误信息格式
        raise HTTPException(status_code=400, detail=f"targetLanguage error, please use following languages: {str(supportedLanguagesList)}")
    _apply_effective_resource_cost(request, 2)
    audio_file = await file.read()
    text = await do_transcribe(audio_file, language='zh')
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}

    if settings.WHISPER_BACKEND == "whisper-trt":
        trans_text = await do_transcribe(audio_file, language=targetLanguage)
    elif settings.ENABLE_WEB_TRANSLATORS:
        trans_text = do_translate(text, from_='zh', to=targetLanguage)
    else:
        translated_text = await do_translate_audio(audio_file)
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
    _reject_auto_language("targetLanguage", targetLanguage)
    if targetLanguage2 != "none":
        _reject_auto_language("targetLanguage2", targetLanguage2)
    if targetLanguage3 != "none":
        _reject_auto_language("targetLanguage3", targetLanguage3)

    if sourceLanguage not in whisperSupportedLanguageList:
        logger.warning(f"[MULTITRANSLATE] sourceLanguage错误: {sourceLanguage} 不在支持的列表中")
        logger.debug(f"[MULTITRANSLATE] 支持的语言列表: {whisperSupportedLanguageList}")
        # 参数错误返回400，避免客户端误判为token过期
        raise HTTPException(status_code=400, detail="sourceLanguage error")
    if targetLanguage not in supportedLanguagesList:
        logger.warning(f"[MULTITRANSLATE] targetLanguage错误: {targetLanguage} 不在支持的列表中")
        logger.debug(f"[MULTITRANSLATE] 支持的目标语言列表: {supportedLanguagesList}")
        # 参数错误返回400，避免客户端误判为token过期
        raise HTTPException(status_code=400, detail=f"targetLanguage error, please use following languages: {str(supportedLanguagesList)}")

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
        logger.info(f"[MULTITRANSLATE] 使用中文语音识别(SenseVoice)")
        async with httpx.AsyncClient() as client:
            files = {'file': ('audio.wav', audio_file, 'audio/wav')}
            logger.debug(f"[MULTITRANSLATE] 发送请求到SenseVoice - URL: {SENSEVOICE_URL}")
            response = await client.post(SENSEVOICE_URL, files=files)
            logger.info(f"[MULTITRANSLATE] SenseVoice响应状态码: {response.status_code}")
            text_json = response.json()
            stext = text_json.get('text', '')
            logger.info(f"[MULTITRANSLATE] 识别结果 - stext: '{stext}'")
            if emojiOutput == 'true':
                original_stext = stext
                stext = emoji.replace_emoji(stext, replace='')
                if original_stext != stext:
                    logger.info(f"[MULTITRANSLATE] 移除emoji - 原始: '{original_stext}' -> 处理后: '{stext}'")
            if stext == '。' or stext == '':
                logger.info(f"[MULTITRANSLATE] 识别结果为空或只有句号，返回过滤")
                return {"text": "", "message": "filtered"}
            # 压缩重复字符
            if settings.ENABLE_TEXT_COMPRESSION:
                original_stext = stext
                stext = compress_repeated_chars(stext, settings.TEXT_COMPRESSION_MIN_REPEAT)
                if original_stext != stext:
                    logger.info(f"[MULTITRANSLATE] 压缩重复字符 - 原始: '{original_stext}' -> 处理后: '{stext}'")
    else:
        logger.info(f"[MULTITRANSLATE] 使用Whisper进行{sourceLanguage}语音识别")
        logger.debug(f"[MULTITRANSLATE] 先进行中文过滤检测")
        filter_text = await do_transcribe(audio_file, language="zh")
        logger.info(f"[MULTITRANSLATE] 中文过滤检测结果: '{filter_text}'")
        if (filter_text in errorFilter.get("errorResultDict", [])) or any(key in filter_text for key in errorFilter.get("errorKeyString", [])):
            logger.warning(f"[MULTITRANSLATE] 内容被过滤规则拦截 检测内容: '{filter_text}'")
            return {"text": "", "message": "filtered"}
        logger.debug(f"[MULTITRANSLATE] 进行{sourceLanguage}语言识别")
        stext = await do_transcribe(audio_file, language=sourceLanguage)
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
    elif settings.WHISPER_BACKEND == "whisper-trt":
        logger.info("[MULTITRANSLATE] 使用 WhisperLive-TRT 内置翻译")
        transText = await do_transcribe(audio_file, language=targetLanguage)
        if targetLanguage2 != "none":
            transText2 = await do_transcribe(audio_file, language=targetLanguage2)
        if targetLanguage3 != "none":
            transText3 = await do_transcribe(audio_file, language=targetLanguage3)
    else:
        logger.info(f"[MULTITRANSLATE] 使用Whisper翻译服务")
        logger.debug(f"[MULTITRANSLATE] 创建Whisper翻译请求")
        translated_text = await do_translate_audio(audio_file)
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
    current_user: User = Depends(enforce_user_rate_limit)
):
    _apply_effective_resource_cost(request, 1)
    _reject_auto_language("sourceLanguage", sourceLanguage)
    if sourceLanguage not in whisperSupportedLanguageList:
        raise HTTPException(status_code=400, detail="sourceLanguage error")

    audio_file = await file.read()
    if file.content_type == 'audio/opus':
        audio_file = packaged_opus_stream_to_wav_bytes(audio_file, 16000)

    text_to_return = ""
    if sourceLanguage != 'zh':
        filter_text = await do_transcribe(audio_file, language="zh")
        if (filter_text in errorFilter.get("errorResultDict", [])) or any(key in filter_text for key in errorFilter.get("errorKeyString", [])):
            return {"text": "", "message": "filtered"}
        text_to_return = await do_transcribe(audio_file, language=sourceLanguage)
    else:
        async with httpx.AsyncClient() as client:
            files = {'file': ('audio.wav', audio_file, 'audio/wav')}
            response = await client.post(SENSEVOICE_URL, files=files)
            text_json = response.json()
            text_to_return = text_json.get('text', '')
            if emojiOutput == 'true': text_to_return = emoji.replace_emoji(text_to_return, replace='')
            if text_to_return == '。' or text_to_return == '': return {"text": "", "message": "filtered"}

    return {"text": text_to_return}

class TTSRequest(BaseModel):
    input: str
    voice: str
    speed: float

@router.post("/func/tts")
async def tts_proxy(
    request: Request,
    data: TTSRequest,
    current_user: User = Depends(enforce_user_rate_limit),
):
    _apply_effective_resource_cost(request, 1)
    if not settings.TTS_URL or not settings.TTS_TOKEN:
        raise HTTPException(status_code=500, detail="TTS service not configured")
    payload = {"model": "tts-1", "input": data.input, "voice": data.voice, "speed": data.speed}
    headers = {"Authorization": f"Bearer {settings.TTS_TOKEN}"}

    async with httpx.AsyncClient() as client:
        response = await client.post(settings.TTS_URL, json=payload, headers=headers, timeout=60.0)
        return Response(content=response.content, media_type=response.headers['Content-Type'], status_code=response.status_code)

class WebTranslateRequest(BaseModel):
    text: str
    targetLanguage: str
    sourceLanguage: str
    targetLanguage2: str = "none"
    targetLanguage3: str = "none"


def _validate_web_translate_language(field_name: str, language: str, allow_none: bool = False) -> str:
    normalized = (language or "").strip()
    if allow_none and normalized == "none":
        return normalized
    if normalized.lower() == "auto":
        raise HTTPException(status_code=400, detail=f"{field_name} 'auto' is no longer supported")
    if normalized not in supportedLanguagesList:
        raise HTTPException(status_code=400, detail=f"{field_name} error, please use following languages: {str(supportedLanguagesList)}")
    return normalized

@router.post("/func/webtranslate")
async def web_translate(
    request: Request,
    data: WebTranslateRequest,
    current_user: User = Depends(enforce_user_rate_limit),
):
    source_language = _validate_web_translate_language("sourceLanguage", data.sourceLanguage)
    target_language = _validate_web_translate_language("targetLanguage", data.targetLanguage)
    target_language2 = _validate_web_translate_language("targetLanguage2", data.targetLanguage2, allow_none=True)
    target_language3 = _validate_web_translate_language("targetLanguage3", data.targetLanguage3, allow_none=True)

    translation_count = 1 + int(data.targetLanguage2 != "none") + int(data.targetLanguage3 != "none")
    _apply_effective_resource_cost(request, translation_count)
    transText = do_translate(data.text, source_language, target_language)
    transText2 = ''
    transText3 = ''
    if target_language2 != "none": transText2 = do_translate(data.text, source_language, target_language2)
    if target_language3 != "none": transText3 = do_translate(data.text, source_language, target_language3)
    return {'text': data.text, 'translatedText': transText, 'translatedText2': transText2, 'translatedText3': transText3}
