import emoji
import httpx
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Form, Body, Response
# 移除 OAuth2PasswordRequestForm 的导入
# from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict

from core.dependencies import get_db, create_access_token, verify_password
from core.rate_limiter import enforce_user_rate_limit
from core.config import settings
from db.models import User
from core.services import (
    whisperclient, errorFilter, do_translate, translate_local,
    supportedLanguagesList, whisperSupportedLanguageList, SENSEVOICE_URL, packaged_opus_stream_to_wav_bytes,
    glm_client, codeTochinese, compress_repeated_chars, do_multi_translate_async,
    async_transcribe, async_translate_audio
)
from core.logging_config import logger
# 导入用于接收JSON体的Pydantic模型
from schemas.user import UserLogin

router = APIRouter()

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

    logger.info(f"[LOGIN] 用户登录成功: {user_credentials.username}")
    access_token = create_access_token(data={"sub": user.username})
    return {"message": "Login successful", "access_token": access_token}

@router.get("/latestVersionInfo")
async def latest_version_info():
    if settings.LATEST_VERSION and settings.PACKAGE_BASE_URL:
        return {"version": settings.LATEST_VERSION, "packgeURL": f"{settings.PACKAGE_BASE_URL}{settings.LATEST_VERSION}{settings.PACKAGE_TYPE}"}
    raise HTTPException(status_code=460, detail="version not defined")

@router.post("/whisper/transcriptions")
async def whisper_transcriptions(file: UploadFile = File(...), current_user: User = Depends(enforce_user_rate_limit)):
    audio_file = await file.read()
    text = await async_transcribe(audio_file, language='zh')
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}
    # 压缩重复字符
    if settings.ENABLE_TEXT_COMPRESSION:
        text = compress_repeated_chars(text, settings.TEXT_COMPRESSION_MIN_REPEAT)
    return {"text": text}

@router.post("/whisper/translations")
async def whisper_translations(file: UploadFile = File(...), current_user: User = Depends(enforce_user_rate_limit)):
    audio_file = await file.read()
    text = await async_translate_audio(audio_file)
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}
    # 压缩重复字符
    if settings.ENABLE_TEXT_COMPRESSION:
        text = compress_repeated_chars(text, settings.TEXT_COMPRESSION_MIN_REPEAT)
    return {"text": text}

class LibreTranslateRequest(BaseModel):
    source: str
    target: str
    text: str

@router.post("/libreTranslate")
async def libre_translate(data: LibreTranslateRequest, current_user: User = Depends(enforce_user_rate_limit)):
    res = await translate_local(data.text, data.source, data.target)
    return {"text": res}

@router.post("/func/translateToEnglish")
async def translate_to_english(file: UploadFile = File(...), current_user: User = Depends(enforce_user_rate_limit)):
    audio_file = await file.read()
    text = await async_transcribe(audio_file, language='zh')
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}
    # 压缩重复字符
    if settings.ENABLE_TEXT_COMPRESSION:
        text = compress_repeated_chars(text, settings.TEXT_COMPRESSION_MIN_REPEAT)
    if settings.ENABLE_WEB_TRANSLATORS:
        translated_text = do_translate(text, from_='zh', to="en")
    else:
        translated_text = await async_translate_audio(audio_file)
    return {"text": text, "translatedText": translated_text}

@router.post("/func/translateToOtherLanguage")
async def translate_to_other_language(file: UploadFile = File(...), targetLanguage: str = Form(...), current_user: User = Depends(enforce_user_rate_limit)):
    # supportedLanguagesList 现在是硬编码的固定列表，不需要动态初始化

    if targetLanguage not in supportedLanguagesList:
        # 与原有server.py保持一致的错误信息格式
        raise HTTPException(status_code=401, detail=f"targetLanguage error, please use following languages: {str(supportedLanguagesList)}")
    audio_file = await file.read()
    text = await async_transcribe(audio_file, language='zh')
    if (text in errorFilter.get("errorResultDict", [])) or any(key in text for key in errorFilter.get("errorKeyString", [])):
        return {"text": "", "message": "filtered"}

    trans_text = ""
    if settings.ENABLE_WEB_TRANSLATORS:
        trans_text = do_translate(text, from_='zh', to=targetLanguage)
    else:
        translated_text = await async_translate_audio(audio_file)
        if targetLanguage == 'en':
            trans_text = translated_text
        else:
            trans_text = await translate_local(translated_text, "en", targetLanguage)
    return {"text": text, "translatedText": trans_text}

@router.post("/func/multitranslateToOtherLanguage")
async def multitranslate_to_other_language(
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

    if sourceLanguage not in whisperSupportedLanguageList:
        logger.warning(f"[MULTITRANSLATE] sourceLanguage错误: {sourceLanguage} 不在支持的列表中")
        logger.debug(f"[MULTITRANSLATE] 支持的语言列表: {whisperSupportedLanguageList}")
        # 与原有server.py保持一致，返回401状态码和详细错误信息
        raise HTTPException(status_code=401, detail=f"sourceLanguage error")
    if targetLanguage not in supportedLanguagesList:
        logger.warning(f"[MULTITRANSLATE] targetLanguage错误: {targetLanguage} 不在支持的列表中")
        logger.debug(f"[MULTITRANSLATE] 支持的目标语言列表: {supportedLanguagesList}")
        # 与原有server.py保持一致，返回401状态码和详细错误信息
        raise HTTPException(status_code=401, detail=f"targetLanguage error, please use following languages: {str(supportedLanguagesList)}")

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
        filter_text = await async_transcribe(audio_file, language="zh")
        logger.info(f"[MULTITRANSLATE] 中文过滤检测结果: '{filter_text}'")
        if (filter_text in errorFilter.get("errorResultDict", [])) or any(key in filter_text for key in errorFilter.get("errorKeyString", [])):
            logger.warning(f"[MULTITRANSLATE] 内容被过滤规则拦截 检测内容: '{filter_text}'")
            return {"text": "", "message": "filtered"}
        logger.debug(f"[MULTITRANSLATE] 进行{sourceLanguage}语言识别")
        stext = await async_transcribe(audio_file, language=sourceLanguage)
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
        translate_source_lang = 'auto' if sourceLanguage == 'zh' else sourceLanguage
        logger.info(f"[MULTITRANSLATE] 使用网页翻译服务 - 源语言: {translate_source_lang}")

        # 构建需要翻译的目标语言列表
        target_languages = [targetLanguage]
        if targetLanguage2 != "none":
            target_languages.append(targetLanguage2)
        if targetLanguage3 != "none":
            target_languages.append(targetLanguage3)

        logger.info(f"[MULTITRANSLATE] 开始并发翻译到目标语言: {target_languages}")

        # 并发执行所有翻译
        results = await do_multi_translate_async(stext, translate_source_lang, target_languages)

        # 分配翻译结果
        transText = results[0] if len(results) > 0 else ''
        transText2 = results[1] if len(results) > 1 else ''
        transText3 = results[2] if len(results) > 2 else ''

        logger.info(f"[MULTITRANSLATE] 并发翻译完成 - 主译文: '{transText}', 第二译文: '{transText2}', 第三译文: '{transText3}'")
    else:
        logger.info(f"[MULTITRANSLATE] 使用Whisper翻译服务")
        logger.debug(f"[MULTITRANSLATE] 创建Whisper翻译请求")
        translated_text = await async_translate_audio(audio_file)
        logger.info(f"[MULTITRANSLATE] Whisper翻译结果: '{translated_text}'")

        if targetLanguage == 'en':
            transText = translated_text
            logger.info(f"[MULTITRANSLATE] 目标语言为英文，直接使用Whisper翻译结果")
        else:
            logger.debug(f"[MULTITRANSLATE] 使用本地翻译从英文到{targetLanguage}")
            transText = await translate_local(translated_text, "en", targetLanguage)
            logger.info(f"[MULTITRANSLATE] 本地翻译完成: '{transText}'")

        if targetLanguage2 != "none":
            logger.debug(f"[MULTITRANSLATE] 翻译到第二目标语言: {targetLanguage2}")
            transText2 = await translate_local(translated_text, 'en', targetLanguage2)
            logger.info(f"[MULTITRANSLATE] 第二目标语言翻译完成: '{transText2}'")
        if targetLanguage3 != "none":
            logger.debug(f"[MULTITRANSLATE] 翻译到第三目标语言: {targetLanguage3}")
            transText3 = await translate_local(translated_text, 'en', targetLanguage3)
            logger.info(f"[MULTITRANSLATE] 第三目标语言翻译完成: '{transText3}'")

    logger.info(f"[MULTITRANSLATE] 多语言翻译请求处理完成 - 用户: {current_user.username}  原文: '{stext}', 主译文: '{transText}', 第二译文: '{transText2}', 第三译文: '{transText3}'")

    return {'text': stext, 'translatedText': transText, 'translatedText2': transText2, 'translatedText3': transText3}

@router.post("/whisper/multitranscription")
async def multitranscription(
    file: UploadFile = File(...),
    sourceLanguage: str = Form(...),
    emojiOutput: str = Form('true'),
    current_user: User = Depends(enforce_user_rate_limit)
):
    if sourceLanguage not in whisperSupportedLanguageList:
        raise HTTPException(status_code=401, detail="sourceLanguage error")

    audio_file = await file.read()
    if file.content_type == 'audio/opus':
        audio_file = packaged_opus_stream_to_wav_bytes(audio_file, 16000)

    text_to_return = ""
    if sourceLanguage != 'zh':
        filter_text = await async_transcribe(audio_file, language="zh")
        if (filter_text in errorFilter.get("errorResultDict", [])) or any(key in filter_text for key in errorFilter.get("errorKeyString", [])):
            return {"text": "", "message": "filtered"}
        text_to_return = await async_transcribe(audio_file, language=sourceLanguage)
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
async def tts_proxy(data: TTSRequest, current_user: User = Depends(enforce_user_rate_limit)):
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

@router.post("/func/webtranslate")
async def web_translate(data: WebTranslateRequest, current_user: User = Depends(enforce_user_rate_limit)):
    # 构建需要翻译的目标语言列表
    target_languages = [data.targetLanguage]
    if data.targetLanguage2 != "none":
        target_languages.append(data.targetLanguage2)
    if data.targetLanguage3 != "none":
        target_languages.append(data.targetLanguage3)

    # 并发执行所有翻译
    results = await do_multi_translate_async(data.text, data.sourceLanguage, target_languages)

    # 分配翻译结果
    transText = results[0] if len(results) > 0 else ''
    transText2 = results[1] if len(results) > 1 else ''
    transText3 = results[2] if len(results) > 2 else ''

    return {'text': data.text, 'translatedText': transText, 'translatedText2': transText2, 'translatedText3': transText3}