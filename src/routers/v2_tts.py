from __future__ import annotations

import io
import json
import wave
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from core.config import settings
from core.logging_config import logger
from core.rate_limiter import enforce_user_rate_limit, set_user_context_cost
from core.services import packaged_opus_stream_to_wav_bytes
from db.models import User, V2User

router = APIRouter()

EDGE_TTS_RESOURCE_COST = 1
TTS_RESOURCE_COST = 1
CLONE_RESOURCE_COST = 2
MOSS_TTS_RESOURCE_COST = 1
MOSS_CLONE_RESOURCE_COST = 2
OMNIVOICE_TTS_RESOURCE_COST = 1
OMNIVOICE_CLONE_RESOURCE_COST = 2
QWEN_DEFAULT_INSTRUCT: Optional[str] = None
QWEN_DEFAULT_VOICE = "vivian"
QWEN_SUPPORTED_VOICES = (
    "aiden",
    "dylan",
    "eric",
    "ono_anna",
    "ryan",
    "serena",
    "sohee",
    "uncle_fu",
    "vivian",
)
QWEN_VOICE_ALIASES = {
    voice.lower(): voice for voice in QWEN_SUPPORTED_VOICES
}
QWEN_VOICE_ALIASES.update(
    {
        "ono-anna": "ono_anna",
        "ono anna": "ono_anna",
        "onoanna": "ono_anna",
        "uncle-fu": "uncle_fu",
        "uncle fu": "uncle_fu",
        "unclefu": "uncle_fu",
        "cherry": QWEN_DEFAULT_VOICE,
        "chelsie": QWEN_DEFAULT_VOICE,
        "ethan": "aiden",
    }
)
QWEN_DISPLAY_VOICE_ALIASES = {
    "芊悦": "Cherry",
    "苏瑶": "Serena",
    "晨煦": "Ethan",
    "千雪": "Chelsie",
}
QWEN_LEGACY_OPENAI_VOICE_ALIASES = {
    "alloy": QWEN_DEFAULT_VOICE,
    "ash": QWEN_DEFAULT_VOICE,
    "ballad": QWEN_DEFAULT_VOICE,
    "coral": QWEN_DEFAULT_VOICE,
    "echo": "Ethan",
    "fable": "Ethan",
    "nova": "Serena",
    "onyx": "Ethan",
    "sage": QWEN_DEFAULT_VOICE,
    "shimmer": "Serena",
    "verse": QWEN_DEFAULT_VOICE,
}
QWEN_UNSUPPORTED_OFFICIAL_VOICE_FALLBACKS = {
    "jada": QWEN_DEFAULT_VOICE,
    "dylan": "Ethan",
    "sunny": "Serena",
}
SUPPORTED_RESPONSE_FORMATS = {"wav", "pcm", "mp3"}
MOSS_PROMPT_AUDIO_FIELDS = {"prompt_audio", "prompt_speech", "reference_audio", "file"}
MOSS_STABLE_SAMPLE_MODE = "fixed"
MOSS_STABLE_DO_SAMPLE = True
MOSS_STABLE_SEED = 1234
MOSS_DEFAULT_VOICE_CLONE_MAX_TEXT_TOKENS = 220
MOSS_DEFAULT_INTER_CHUNK_PAUSE_SECONDS = 0.0
MOSS_DEFAULT_TRIM_LONG_SILENCE = True
MOSS_DEFAULT_MAX_SILENCE_SECONDS = 0.08
MOSS_DEFAULT_SILENCE_THRESHOLD = 0.003
MOSS_JAPANESE_BUILTIN_VOICE_CLONE_MAX_TEXT_TOKENS = 40
MOSS_JAPANESE_BUILTIN_VOICES = {
    "soyo",
    "saki",
    "mortis",
    "umiri",
    "mei",
    "anon",
    "arisa",
}
QWEN_CLONE_REF_AUDIO_SAMPLE_RATE = 24000
QWEN_CLONE_REF_AUDIO_ALLOWED_SAMPLE_RATES = {8000, 12000, 16000, 24000, 48000}
QWEN_CLONE_REF_AUDIO_MIN_SECONDS = 3.0
QWEN_CLONE_REF_AUDIO_RECOMMENDED_MIN_SECONDS = 10.0
QWEN_CLONE_REF_AUDIO_RECOMMENDED_MAX_SECONDS = 20.0
QWEN_CLONE_REF_AUDIO_MAX_SECONDS = 60.0
QWEN_CLONE_PACKAGED_OPUS_ENCODINGS = {
    "length_prefixed_opus",
    "length-prefixed-opus",
    "packaged_opus",
    "packaged-opus",
    "vrcls_opus",
    "vrcls-opus",
}
MOSS_BUILTIN_VOICE_ALIASES = {
    "sakura": "Soyo",
    "yui": "Saki",
    "aoi": "Mortis",
    "hina": "Umiri",
}
QWEN_LANGUAGE_ALIASES = {
    "auto": "Auto",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-hans": "Chinese",
    "zh-hant": "Chinese",
    "cn": "Chinese",
    "chinese": "Chinese",
    "zho": "Chinese",
    "cmn": "Chinese",
    "ja": "Japanese",
    "jp": "Japanese",
    "jpn": "Japanese",
    "japanese": "Japanese",
    "en": "English",
    "en-us": "English",
    "en-gb": "English",
    "eng": "English",
    "english": "English",
    "ko": "Korean",
    "kr": "Korean",
    "kor": "Korean",
    "korean": "Korean",
    "fr": "French",
    "fra": "French",
    "fre": "French",
    "french": "French",
    "de": "German",
    "deu": "German",
    "ger": "German",
    "german": "German",
    "it": "Italian",
    "ita": "Italian",
    "italian": "Italian",
    "pt": "Portuguese",
    "pt-br": "Portuguese",
    "pt-pt": "Portuguese",
    "por": "Portuguese",
    "portuguese": "Portuguese",
    "ru": "Russian",
    "rus": "Russian",
    "russian": "Russian",
    "es": "Spanish",
    "spa": "Spanish",
    "spanish": "Spanish",
}
OMNIVOICE_LANGUAGE_ALIASES = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh",
    "cn": "zh",
    "chinese": "zh",
    "zho": "zh",
    "cmn": "zh",
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "eng": "en",
    "english": "en",
    "ko": "ko",
    "kr": "ko",
    "kor": "ko",
    "korean": "ko",
    "fr": "fr",
    "fra": "fr",
    "fre": "fr",
    "french": "fr",
    "de": "de",
    "deu": "de",
    "ger": "de",
    "german": "de",
    "it": "it",
    "ita": "it",
    "italian": "it",
    "pt": "pt",
    "pt-br": "pt",
    "pt-pt": "pt",
    "por": "pt",
    "portuguese": "pt",
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
    "es": "es",
    "spa": "es",
    "spanish": "es",
}


class V2EdgeTTSRequest(BaseModel):
    input: str = Field(..., min_length=1)
    voice: str = Field(..., min_length=1, max_length=128)
    response_format: str = "wav"
    speed: float = Field(1.0, ge=0.25, le=4.0)
    language: Optional[str] = Field(None, max_length=64)
    volume: Optional[float] = Field(None, ge=0.0, le=4.0)


class V2TTSRequest(BaseModel):
    input: str = Field(..., min_length=1)
    voice: str = Field(QWEN_DEFAULT_VOICE, min_length=1, max_length=64)
    response_format: str = "wav"
    speed: float = Field(1.0, ge=0.25, le=4.0)
    chunk_size: Optional[int] = Field(8, ge=1, le=128)
    instruct: Optional[str] = Field(QWEN_DEFAULT_INSTRUCT, max_length=300)
    temperature: Optional[float] = Field(None, ge=0.1, le=1.5)
    volume: Optional[float] = Field(None, ge=0.0, le=4.0)


class V2OmniVoiceTTSRequest(BaseModel):
    input: Optional[str] = Field(None, min_length=1)
    text: Optional[str] = Field(None, min_length=1)
    voice: Optional[str] = Field(None, max_length=300)
    language: Optional[str] = Field(None, max_length=64)
    response_format: str = "wav"
    stream: bool = True
    duration: Optional[float] = Field(None, gt=0, le=300)
    speed: Optional[float] = Field(None, gt=0, le=4)
    num_step: int = Field(32, ge=1, le=128)
    guidance_scale: float = Field(2.0, ge=0, le=20)
    t_shift: float = Field(0.1, gt=0, le=10)
    denoise: bool = True
    preprocess_prompt: bool = True
    postprocess_output: bool = True
    layer_penalty_factor: float = Field(5.0, ge=0, le=50)
    position_temperature: float = Field(5.0, ge=0, le=50)
    class_temperature: float = Field(0.0, ge=0, le=10)
    instruct: Optional[str] = Field(None, max_length=300)
    volume: Optional[float] = Field(None, ge=0.0, le=4.0)


def _edge_tts_available() -> bool:
    return bool(settings.EDGE_TTS_ENABLED and (settings.TTS_URL or "").strip())


def _qwen_tts_available() -> bool:
    return bool(settings.QWEN_TTS_ENABLED and (settings.QWEN_TTS_BASE_URL or "").strip())


def _qwen_builtin_voices_available() -> bool:
    return bool(_qwen_tts_available() and settings.QWEN_TTS_BUILTIN_VOICES_ENABLED)


def _moss_tts_available() -> bool:
    return bool(settings.MOSS_TTS_ENABLED and (settings.MOSS_TTS_BASE_URL or "").strip())


def _omnivoice_tts_available() -> bool:
    return bool(settings.OMNIVOICE_TTS_ENABLED and (settings.OMNIVOICE_TTS_BASE_URL or "").strip())


def _edge_tts_url() -> str:
    if not settings.EDGE_TTS_ENABLED:
        raise HTTPException(status_code=403, detail="Edge TTS is disabled")
    url = (settings.TTS_URL or "").strip()
    if not url:
        raise HTTPException(status_code=500, detail="Edge TTS service is not configured")
    return url


def _ensure_qwen_builtin_voices_enabled() -> None:
    if not settings.QWEN_TTS_BUILTIN_VOICES_ENABLED:
        raise HTTPException(status_code=403, detail="Qwen builtin voices are disabled")


def _tts_base_url() -> str:
    if not settings.QWEN_TTS_ENABLED:
        raise HTTPException(status_code=403, detail="Qwen TTS is disabled")
    base_url = (settings.QWEN_TTS_BASE_URL or "").strip()
    if not base_url:
        raise HTTPException(status_code=500, detail="Qwen TTS service is not configured")
    return base_url.rstrip("/")


def _moss_tts_enabled() -> bool:
    return bool(settings.MOSS_TTS_ENABLED)


def _ensure_moss_tts_enabled() -> None:
    if not _moss_tts_enabled():
        raise HTTPException(status_code=403, detail="MOSS TTS is disabled")


def _moss_base_url() -> str:
    _ensure_moss_tts_enabled()
    base_url = (settings.MOSS_TTS_BASE_URL or "").strip()
    if not base_url:
        raise HTTPException(status_code=500, detail="MOSS TTS service is not configured")
    return base_url.rstrip("/")


def _omnivoice_base_url() -> str:
    if not settings.OMNIVOICE_TTS_ENABLED:
        raise HTTPException(status_code=403, detail="OmniVoice TTS is disabled")
    base_url = (settings.OMNIVOICE_TTS_BASE_URL or "").strip()
    if not base_url:
        raise HTTPException(status_code=500, detail="OmniVoice TTS service is not configured")
    return base_url.rstrip("/")


def _tts_engine_status() -> dict[str, Any]:
    return {
        "engines": {
            "edge": {
                "enabled": _edge_tts_available(),
                "speech_path": "/v2/api/tts/edge",
                "v1_speech_path": "/v1/api/func/tts",
                "supports_clone": False,
            },
            "qwen": {
                "enabled": _qwen_builtin_voices_available(),
                "service_enabled": _qwen_tts_available(),
                "speech_enabled": _qwen_builtin_voices_available(),
                "clone_enabled": _qwen_tts_available(),
                "builtin_voices_enabled": bool(settings.QWEN_TTS_BUILTIN_VOICES_ENABLED),
                "speech_path": "/v2/api/tts/qwen",
                "clone_path": "/v2/api/clone/qwen",
                "supports_clone": True,
            },
            "moss": {
                "enabled": _moss_tts_available(),
                "speech_path": "/v2/api/tts/moss",
                "clone_path": "/v2/api/clone/moss",
                "voices_path": "/v2/api/tts/moss/voices",
                "supports_clone": True,
            },
            "omnivoice": {
                "enabled": _omnivoice_tts_available(),
                "speech_path": "/v2/api/tts/omnivoice",
                "clone_path": "/v2/api/clone/omnivoice",
                "languages_path": "/v2/api/tts/omnivoice/languages",
                "supports_clone": True,
            },
        },
    }


def _upstream_url(path: str) -> str:
    return f"{_tts_base_url()}{path}"


def _moss_upstream_url(path: str) -> str:
    return f"{_moss_base_url()}{path}"


def _omnivoice_upstream_url(path: str) -> str:
    return f"{_omnivoice_base_url()}{path}"


def _bearer_headers(token: Optional[str]) -> dict[str, str]:
    normalized = (token or "").strip()
    return {"Authorization": f"Bearer {normalized}"} if normalized else {}


def _auth_headers() -> dict[str, str]:
    return _bearer_headers(settings.QWEN_TTS_TOKEN)


def _edge_auth_headers() -> dict[str, str]:
    return _bearer_headers(settings.TTS_TOKEN)


def _moss_auth_headers() -> dict[str, str]:
    return _bearer_headers(settings.MOSS_TTS_TOKEN)


def _omnivoice_auth_headers() -> dict[str, str]:
    return _bearer_headers(settings.OMNIVOICE_TTS_TOKEN)


def _timeout_from_value(value: float | int | str | None) -> httpx.Timeout:
    timeout = max(1.0, float(value or 300.0))
    connect_timeout = min(15.0, timeout)
    return httpx.Timeout(timeout=timeout, connect=connect_timeout, read=timeout, write=timeout)


def _timeout() -> httpx.Timeout:
    return _timeout_from_value(settings.QWEN_TTS_TIMEOUT)


def _edge_timeout() -> httpx.Timeout:
    return _timeout_from_value(settings.EDGE_TTS_TIMEOUT)


def _moss_timeout() -> httpx.Timeout:
    return _timeout_from_value(settings.MOSS_TTS_TIMEOUT)


def _omnivoice_timeout() -> httpx.Timeout:
    return _timeout_from_value(settings.OMNIVOICE_TTS_TIMEOUT)


def _normalize_response_format(response_format: str) -> str:
    fmt = (response_format or "wav").strip().lower()
    if fmt not in SUPPORTED_RESPONSE_FORMATS:
        raise HTTPException(status_code=400, detail="response_format must be one of wav, pcm, mp3")
    return fmt


def _normalize_moss_response_format(response_format: str) -> str:
    fmt = (response_format or "wav").strip().lower()
    if fmt in {"wav", "wave", "audio/wav"}:
        return "wav"
    if fmt in {"pcm", "pcm16", "pcm_s16le", "s16le", "raw"}:
        return "pcm"
    raise HTTPException(status_code=400, detail="response_format must be one of wav, pcm")


def _normalize_omnivoice_response_format(response_format: str) -> str:
    fmt = (response_format or "wav").strip().lower()
    if fmt in {"wav", "wave", "audio/wav"}:
        return "wav"
    if fmt in {"pcm", "pcm16", "pcm_s16le", "s16le", "raw"}:
        return "pcm"
    if fmt in {"f32", "float32", "float"}:
        return "f32"
    raise HTTPException(status_code=400, detail="response_format must be one of wav, pcm, f32")


def _normalize_moss_voice(voice: Any) -> str:
    normalized = str(voice or "").strip()
    if not normalized:
        return ""
    return MOSS_BUILTIN_VOICE_ALIASES.get(normalized.lower(), normalized)


def _normalize_moss_language(value: Any) -> str:
    normalized = str(value or "").strip().replace("_", "-").lower()
    if not normalized or normalized == "auto":
        return ""
    if normalized == "jp":
        return "ja"
    return normalized.split("-", 1)[0]


def _is_moss_japanese_builtin_request(
    raw_payload: dict[str, Any],
    *,
    voice: str,
    has_prompt_audio: bool,
) -> bool:
    if has_prompt_audio:
        return False
    language = _normalize_moss_language(_payload_get(raw_payload, "language", "lang", default=""))
    normalized_voice = str(voice or "").strip().lower()
    return language == "ja" or normalized_voice in MOSS_JAPANESE_BUILTIN_VOICES


def _clamp_moss_japanese_builtin_text_tokens(payload: dict[str, Any]) -> None:
    raw_value = payload.get("voice_clone_max_text_tokens", MOSS_DEFAULT_VOICE_CLONE_MAX_TEXT_TOKENS)
    try:
        current_value = max(1, int(raw_value))
    except (TypeError, ValueError):
        current_value = MOSS_DEFAULT_VOICE_CLONE_MAX_TEXT_TOKENS
    payload["voice_clone_max_text_tokens"] = min(
        current_value,
        MOSS_JAPANESE_BUILTIN_VOICE_CLONE_MAX_TEXT_TOKENS,
    )


def _validate_chunk_size(chunk_size: Optional[int]) -> Optional[int]:
    if chunk_size is None:
        return None
    normalized = int(chunk_size)
    if normalized < 1:
        raise HTTPException(status_code=400, detail="chunk_size must be >= 1")
    if normalized > 128:
        raise HTTPException(status_code=400, detail="chunk_size must be <= 128")
    return normalized


def _validate_tts_input(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="input text is empty")
    max_chars = max(1, int(settings.QWEN_TTS_MAX_INPUT_CHARS or 500))
    if len(normalized) > max_chars:
        raise HTTPException(status_code=400, detail=f"input text exceeds {max_chars} characters")
    return normalized


def _validate_edge_tts_input(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="input text is empty")
    max_chars = max(1, int(settings.EDGE_TTS_MAX_INPUT_CHARS or 500))
    if len(normalized) > max_chars:
        raise HTTPException(status_code=400, detail=f"input text exceeds {max_chars} characters")
    return normalized


def _validate_moss_tts_input(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="input text is empty")
    max_chars = max(1, int(settings.MOSS_TTS_MAX_INPUT_CHARS or 500))
    if len(normalized) > max_chars:
        raise HTTPException(status_code=400, detail=f"input text exceeds {max_chars} characters")
    return normalized


def _validate_omnivoice_tts_input(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="input text is empty")
    max_chars = max(1, int(settings.OMNIVOICE_TTS_MAX_INPUT_CHARS or 500))
    if len(normalized) > max_chars:
        raise HTTPException(status_code=400, detail=f"input text exceeds {max_chars} characters")
    return normalized


def _normalize_qwen_voice(voice: Optional[str]) -> str:
    normalized = str(voice or "").strip()
    if not normalized:
        return QWEN_DEFAULT_VOICE
    candidates = [
        normalized,
        normalized.split("/", 1)[0].strip(),
        normalized.split("／", 1)[0].strip(),
        normalized.split("(", 1)[0].strip(),
        normalized.split("（", 1)[0].strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        lookup_key = candidate.lower()
        if lookup_key in QWEN_VOICE_ALIASES:
            return QWEN_VOICE_ALIASES[lookup_key]
        if candidate in QWEN_DISPLAY_VOICE_ALIASES:
            display_voice = QWEN_DISPLAY_VOICE_ALIASES[candidate]
            return QWEN_VOICE_ALIASES.get(display_voice.lower(), QWEN_DEFAULT_VOICE)
        if lookup_key in QWEN_LEGACY_OPENAI_VOICE_ALIASES:
            legacy_voice = QWEN_LEGACY_OPENAI_VOICE_ALIASES[lookup_key]
            return QWEN_VOICE_ALIASES.get(legacy_voice.lower(), QWEN_DEFAULT_VOICE)
        if lookup_key in QWEN_UNSUPPORTED_OFFICIAL_VOICE_FALLBACKS:
            fallback_voice = QWEN_UNSUPPORTED_OFFICIAL_VOICE_FALLBACKS[lookup_key]
            return QWEN_VOICE_ALIASES.get(fallback_voice.lower(), QWEN_DEFAULT_VOICE)
    logger.warning(
        "[V2-TTS] unsupported Qwen voice requested, falling back to default. requested=%s fallback=%s supported=%s",
        normalized,
        QWEN_DEFAULT_VOICE,
        ",".join(QWEN_SUPPORTED_VOICES),
    )
    return QWEN_DEFAULT_VOICE


def _normalize_qwen_language(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    lowered = normalized.replace("_", "-").lower()
    if lowered in {"none", "null"}:
        return None
    if lowered in QWEN_LANGUAGE_ALIASES:
        return QWEN_LANGUAGE_ALIASES[lowered]
    base_language = lowered.split("-", 1)[0]
    if base_language in QWEN_LANGUAGE_ALIASES:
        return QWEN_LANGUAGE_ALIASES[base_language]
    return normalized


def _resolve_clone_synthesis_language(*values: Optional[str]) -> str:
    for value in values:
        normalized = _normalize_qwen_language(value)
        if normalized:
            return normalized
    return "Auto"


def _resolve_optional_qwen_language(*values: Optional[str]) -> Optional[str]:
    for value in values:
        normalized = _normalize_qwen_language(value)
        if normalized:
            return normalized
    return None


def _normalize_omnivoice_language(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    lowered = normalized.replace("_", "-").lower()
    if lowered in {"auto", "none", "null"}:
        return None
    if lowered in OMNIVOICE_LANGUAGE_ALIASES:
        return OMNIVOICE_LANGUAGE_ALIASES[lowered]
    base_language = lowered.split("-", 1)[0]
    if base_language in OMNIVOICE_LANGUAGE_ALIASES:
        return OMNIVOICE_LANGUAGE_ALIASES[base_language]
    return lowered


def _resolve_optional_omnivoice_language(*values: Optional[str]) -> Optional[str]:
    for value in values:
        normalized = _normalize_omnivoice_language(value)
        if normalized:
            return normalized
    return None


def _first_nonempty_text(*values: Optional[str]) -> Optional[str]:
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized.lower() not in {"none", "null"}:
            return normalized
    return None


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _validate_optional_volume(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="volume must be a number") from exc
    if normalized < 0.0 or normalized > 4.0:
        raise HTTPException(status_code=400, detail="volume must be between 0 and 4")
    return normalized


def _get_compat_resource_cost(cost: int) -> int:
    if settings.LEGACY_CLIENT_COMPAT:
        return 1
    return max(1, int(cost or 1))


def _apply_effective_resource_cost(request: Request, cost: int) -> int:
    effective_cost = _get_compat_resource_cost(cost)
    set_user_context_cost(request, effective_cost)
    return effective_cost


def _upstream_error_detail(body: bytes) -> Any:
    if not body:
        return "TTS upstream request failed"
    text = body.decode("utf-8", "ignore")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text[:1000]


async def _open_upstream_stream(
    *,
    method: str,
    url: str,
    json_payload: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> tuple[httpx.AsyncClient, httpx.Response]:
    client = httpx.AsyncClient(timeout=timeout or _timeout())
    try:
        request = client.build_request(
            method,
            url,
            headers=_auth_headers() if headers is None else headers,
            json=json_payload,
            data=data,
            files=files,
        )
        response = await client.send(request, stream=True)
    except httpx.TimeoutException as exc:
        await client.aclose()
        logger.warning("[V2-TTS] upstream timeout url=%s error=%s", url, exc)
        raise HTTPException(status_code=504, detail="TTS upstream timeout") from exc
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("[V2-TTS] upstream unavailable url=%s error=%s", url, exc)
        raise HTTPException(status_code=503, detail="TTS upstream unavailable") from exc

    if response.status_code >= 400:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        logger.warning("[V2-TTS] upstream failed status=%s body=%s", response.status_code, body[:300])
        raise HTTPException(status_code=response.status_code, detail=_upstream_error_detail(body))

    return client, response


async def _iter_upstream_bytes(client: httpx.AsyncClient, response: httpx.Response):
    try:
        async for chunk in response.aiter_bytes():
            if chunk:
                yield chunk
    finally:
        await response.aclose()
        await client.aclose()


def _media_type(response: httpx.Response, fallback_format: str) -> str:
    content_type = response.headers.get("content-type", "")
    if content_type:
        return content_type.strip()
    return {
        "wav": "audio/wav",
        "pcm": "audio/pcm",
        "mp3": "audio/mpeg",
        "f32": "application/octet-stream",
    }[fallback_format]


def _audio_headers(response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in (
        "X-Audio-Sample-Rate",
        "X-Audio-Channels",
        "X-Audio-Sample-Format",
        "X-Audio-Max-New-Tokens",
        "X-Audio-Do-Sample",
        "X-Audio-Resolved-Voice",
        "X-MOSS-TTS-Backend",
        "X-Sample-Rate",
        "X-Audio-Format",
        "Cache-Control",
        "X-Accel-Buffering",
    ):
        value = response.headers.get(name)
        if value:
            headers[name] = value
    return headers


def _normalize_qwen_instruct(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized or normalized.lower() in {"none", "null"}:
        return None
    if normalized in {"指定", "鎸囧畾"}:
        return None
    return normalized


def _normalized_upload_content_type(upload: UploadFile) -> str:
    return (upload.content_type or "").split(";", 1)[0].strip().lower()


def _is_packaged_opus_reference(upload: UploadFile, encoding: Optional[str]) -> bool:
    normalized_encoding = (encoding or "").strip().lower()
    if normalized_encoding in QWEN_CLONE_PACKAGED_OPUS_ENCODINGS:
        return True
    filename = (upload.filename or "").strip().lower()
    return _normalized_upload_content_type(upload) == "audio/opus" and filename.endswith(".opus")


def _validate_qwen_clone_ref_audio_sample_rate(sample_rate: int) -> int:
    normalized = int(sample_rate or QWEN_CLONE_REF_AUDIO_SAMPLE_RATE)
    if normalized not in QWEN_CLONE_REF_AUDIO_ALLOWED_SAMPLE_RATES:
        raise HTTPException(
            status_code=400,
            detail="ref_audio_sample_rate must be one of 8000, 12000, 16000, 24000, 48000",
        )
    return normalized


def _wav_duration_seconds(wav_data: bytes) -> float:
    if not wav_data:
        raise HTTPException(status_code=400, detail="decoded ref_audio is empty")
    try:
        with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frames = wav_file.getnframes()
    except (EOFError, wave.Error) as exc:
        raise HTTPException(status_code=400, detail="decoded ref_audio is not a valid WAV file") from exc
    if frame_rate <= 0:
        raise HTTPException(status_code=400, detail="decoded ref_audio has invalid sample rate")
    return frames / float(frame_rate)


def _validate_qwen_clone_ref_audio_duration(duration_seconds: float) -> None:
    if duration_seconds < QWEN_CLONE_REF_AUDIO_MIN_SECONDS:
        raise HTTPException(
            status_code=400,
            detail="ref_audio must contain at least 3 seconds of continuous clear speech",
        )
    if duration_seconds > QWEN_CLONE_REF_AUDIO_MAX_SECONDS:
        raise HTTPException(
            status_code=400,
            detail="ref_audio must be 60 seconds or shorter; 10-20 seconds is recommended",
        )
    if (
        duration_seconds < QWEN_CLONE_REF_AUDIO_RECOMMENDED_MIN_SECONDS
        or duration_seconds > QWEN_CLONE_REF_AUDIO_RECOMMENDED_MAX_SECONDS
    ):
        logger.info(
            "[V2-TTS] Qwen clone ref_audio duration outside recommended range duration=%.2fs recommended=10-20s",
            duration_seconds,
        )


async def _prepare_qwen_clone_ref_audio_file(
    ref_audio: UploadFile,
    ref_audio_encoding: Optional[str],
    ref_audio_sample_rate: int,
) -> tuple[str, Any, str]:
    if not _is_packaged_opus_reference(ref_audio, ref_audio_encoding):
        await ref_audio.seek(0)
        return (
            ref_audio.filename or "reference.wav",
            ref_audio.file,
            ref_audio.content_type or "application/octet-stream",
        )

    sample_rate = _validate_qwen_clone_ref_audio_sample_rate(ref_audio_sample_rate)
    packaged_opus = await ref_audio.read()
    if not packaged_opus:
        raise HTTPException(status_code=400, detail="ref_audio is empty")

    try:
        wav_data = packaged_opus_stream_to_wav_bytes(packaged_opus, sample_rate)
    except Exception as exc:
        logger.warning("[V2-TTS] failed to decode packaged Opus ref_audio error=%s", exc)
        raise HTTPException(status_code=400, detail="invalid packaged Opus ref_audio") from exc

    duration_seconds = _wav_duration_seconds(wav_data)
    _validate_qwen_clone_ref_audio_duration(duration_seconds)
    logger.info(
        "[V2-TTS] decoded Qwen clone ref_audio packaged_opus_bytes=%s wav_bytes=%s duration=%.2fs sample_rate=%s",
        len(packaged_opus),
        len(wav_data),
        duration_seconds,
        sample_rate,
    )
    return ("reference.wav", io.BytesIO(wav_data), "audio/wav")


def _payload_get(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return default


def _payload_has_value(payload: dict[str, Any], *names: str) -> bool:
    value = _payload_get(payload, *names, default=None)
    if value is None:
        return False
    return bool(str(value).strip())


async def _parse_moss_request(request: Request) -> tuple[dict[str, Any], UploadFile | None, bool]:
    content_type = request.headers.get("content-type", "").lower()
    payload: dict[str, Any] = {}
    prompt_audio: UploadFile | None = None
    is_form_request = "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type

    if is_form_request:
        form = await request.form()
        for key, value in form.multi_items():
            if isinstance(value, UploadFile) or (hasattr(value, "filename") and hasattr(value, "read")):
                if key in MOSS_PROMPT_AUDIO_FIELDS and prompt_audio is None:
                    prompt_audio = value
                continue
            payload[key] = value
        return payload, prompt_audio, True

    try:
        raw_payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON or multipart/form-data") from exc
    if not isinstance(raw_payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    return raw_payload, None, False


def _copy_moss_value(target: dict[str, Any], source: dict[str, Any], target_key: str, *source_keys: str) -> None:
    value = _payload_get(source, *source_keys, default=None)
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    target[target_key] = value


def _build_moss_payload(
    raw_payload: dict[str, Any],
    *,
    text: str,
    response_format: str,
    stream_enabled: bool,
    as_form: bool,
    has_prompt_audio: bool,
) -> dict[str, Any]:
    stream_value: bool | str = stream_enabled
    if as_form:
        stream_value = "true" if stream_enabled else "false"
    payload: dict[str, Any] = {
        "input": text,
        "response_format": response_format,
        "stream": stream_value,
        "sample_mode": MOSS_STABLE_SAMPLE_MODE,
        "do_sample": MOSS_STABLE_DO_SAMPLE,
        "seed": MOSS_STABLE_SEED,
        "voice_clone_max_text_tokens": MOSS_DEFAULT_VOICE_CLONE_MAX_TEXT_TOKENS,
        "voice_clone_inter_chunk_pause_seconds": MOSS_DEFAULT_INTER_CHUNK_PAUSE_SECONDS,
        "trim_long_silence": MOSS_DEFAULT_TRIM_LONG_SILENCE,
        "max_silence_seconds": MOSS_DEFAULT_MAX_SILENCE_SECONDS,
        "silence_threshold": MOSS_DEFAULT_SILENCE_THRESHOLD,
    }
    _copy_moss_value(payload, raw_payload, "language", "language", "lang")
    voice = _normalize_moss_voice(_payload_get(raw_payload, "voice", default=None))
    if voice:
        payload["voice"] = voice
    is_japanese_builtin = _is_moss_japanese_builtin_request(
        raw_payload,
        voice=voice,
        has_prompt_audio=has_prompt_audio,
    )
    _copy_moss_value(
        payload,
        raw_payload,
        "prompt_audio_path",
        "prompt_audio_path",
        "prompt_speech",
        "reference_audio_path",
    )
    _copy_moss_value(payload, raw_payload, "sample_mode", "sample_mode")
    _copy_moss_value(payload, raw_payload, "do_sample", "do_sample")
    _copy_moss_value(payload, raw_payload, "max_new_frames", "max_new_frames")
    _copy_moss_value(payload, raw_payload, "voice_clone_max_text_tokens", "voice_clone_max_text_tokens")
    _copy_moss_value(
        payload,
        raw_payload,
        "voice_clone_inter_chunk_pause_seconds",
        "voice_clone_inter_chunk_pause_seconds",
    )
    _copy_moss_value(payload, raw_payload, "trim_long_silence", "trim_long_silence")
    _copy_moss_value(payload, raw_payload, "max_silence_seconds", "max_silence_seconds")
    _copy_moss_value(payload, raw_payload, "silence_threshold", "silence_threshold")
    _copy_moss_value(payload, raw_payload, "cpu_threads", "cpu_threads")
    _copy_moss_value(
        payload,
        raw_payload,
        "enable_text_normalization",
        "enable_text_normalization",
        "enable_wetext_processing",
    )
    _copy_moss_value(payload, raw_payload, "enable_normalize_tts_text", "enable_normalize_tts_text")
    _copy_moss_value(payload, raw_payload, "text_temperature", "text_temperature")
    _copy_moss_value(payload, raw_payload, "text_top_p", "text_top_p")
    _copy_moss_value(payload, raw_payload, "text_top_k", "text_top_k")
    _copy_moss_value(payload, raw_payload, "audio_temperature", "audio_temperature")
    _copy_moss_value(payload, raw_payload, "audio_top_p", "audio_top_p")
    _copy_moss_value(payload, raw_payload, "audio_top_k", "audio_top_k")
    _copy_moss_value(payload, raw_payload, "audio_repetition_penalty", "audio_repetition_penalty")
    _copy_moss_value(payload, raw_payload, "volume", "volume")
    _copy_moss_value(payload, raw_payload, "seed", "seed")
    if is_japanese_builtin:
        _clamp_moss_japanese_builtin_text_tokens(payload)
    return payload


def _stringify_form_payload(payload: dict[str, Any]) -> dict[str, str]:
    form_payload: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(value, bool):
            form_payload[key] = "true" if value else "false"
        else:
            form_payload[key] = str(value)
    return form_payload


def _build_omnivoice_speech_payload(
    payload: V2OmniVoiceTTSRequest,
    *,
    text: str,
    response_format: str,
    language: Optional[str],
) -> dict[str, Any]:
    upstream_payload: dict[str, Any] = {
        "input": text,
        "response_format": response_format,
        "stream": payload.stream,
        "num_step": payload.num_step,
        "guidance_scale": payload.guidance_scale,
        "t_shift": payload.t_shift,
        "denoise": payload.denoise,
        "preprocess_prompt": payload.preprocess_prompt,
        "postprocess_output": payload.postprocess_output,
        "layer_penalty_factor": payload.layer_penalty_factor,
        "position_temperature": payload.position_temperature,
        "class_temperature": payload.class_temperature,
    }
    if language:
        upstream_payload["language"] = language
    if _first_nonempty_text(payload.voice):
        upstream_payload["voice"] = payload.voice
    if _first_nonempty_text(payload.instruct):
        upstream_payload["instruct"] = payload.instruct
    if payload.duration is not None:
        upstream_payload["duration"] = payload.duration
    if payload.speed is not None:
        upstream_payload["speed"] = payload.speed
    if payload.volume is not None:
        upstream_payload["volume"] = payload.volume
    return upstream_payload


@router.get("/tts/engines")
async def v2_tts_engines(
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    return _tts_engine_status()


@router.post("/tts/edge")
async def v2_edge_tts(
    request: Request,
    payload: V2EdgeTTSRequest,
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    text = _validate_edge_tts_input(payload.input)
    response_format = _normalize_response_format(payload.response_format)

    effective_cost = _apply_effective_resource_cost(request, EDGE_TTS_RESOURCE_COST)
    request.state.audit_payload = {
        "feature": "edge_tts",
        "engine": "edge",
        "resource_cost": effective_cost,
        "response_format": response_format,
        "voice": payload.voice,
        "volume": payload.volume,
    }

    upstream_payload: dict[str, Any] = {
        "model": "tts-1",
        "input": text,
        "voice": payload.voice,
        "response_format": response_format,
        "speed": payload.speed,
    }
    if payload.language:
        upstream_payload["language"] = payload.language
    if payload.volume is not None:
        upstream_payload["volume"] = payload.volume

    async with httpx.AsyncClient(timeout=_edge_timeout()) as client:
        response = await client.post(
            _edge_tts_url(),
            json=upstream_payload,
            headers=_edge_auth_headers(),
        )
    return StreamingResponse(
        iter([response.content]),
        media_type=response.headers.get("Content-Type", _media_type(response, response_format)),
        headers=_audio_headers(response),
        status_code=response.status_code,
    )


@router.post("/tts/qwen")
async def v2_tts(
    request: Request,
    payload: V2TTSRequest,
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    _ensure_qwen_builtin_voices_enabled()
    text = _validate_tts_input(payload.input)
    response_format = _normalize_response_format(payload.response_format)
    chunk_size = _validate_chunk_size(payload.chunk_size)
    qwen_voice = _normalize_qwen_voice(payload.voice)

    effective_cost = _apply_effective_resource_cost(request, TTS_RESOURCE_COST)
    request.state.audit_payload = {
        "feature": "tts",
        "engine": "qwen",
        "resource_cost": effective_cost,
        "response_format": response_format,
        "voice": qwen_voice,
        "requested_voice": payload.voice,
        "temperature": payload.temperature,
        "volume": payload.volume,
    }

    upstream_payload: dict[str, Any] = {
        "model": "tts-1",
        "input": text,
        "voice": qwen_voice,
        "response_format": response_format,
        "speed": payload.speed,
    }
    if chunk_size is not None:
        upstream_payload["chunk_size"] = chunk_size
    normalized_instruct = _normalize_qwen_instruct(payload.instruct)
    if normalized_instruct:
        upstream_payload["instruct"] = normalized_instruct
    if payload.temperature is not None:
        upstream_payload["temperature"] = payload.temperature
    if payload.volume is not None:
        upstream_payload["volume"] = payload.volume

    client, response = await _open_upstream_stream(
        method="POST",
        url=_upstream_url("/v1/audio/speech"),
        json_payload=upstream_payload,
    )
    return StreamingResponse(
        _iter_upstream_bytes(client, response),
        media_type=_media_type(response, response_format),
        headers=_audio_headers(response),
        status_code=response.status_code,
    )


@router.post("/tts/moss")
async def v2_moss_tts(
    request: Request,
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    _ensure_moss_tts_enabled()
    raw_payload, prompt_audio, is_form_request = await _parse_moss_request(request)

    text = _validate_moss_tts_input(str(_payload_get(raw_payload, "input", "text", default="") or ""))
    response_format = _normalize_moss_response_format(
        str(_payload_get(raw_payload, "response_format", "format", default="wav") or "wav")
    )
    stream_enabled = _coerce_bool(_payload_get(raw_payload, "stream", default=True), True)
    volume = _validate_optional_volume(_payload_get(raw_payload, "volume", default=None))
    if volume is not None:
        raw_payload["volume"] = volume
    has_prompt_audio = prompt_audio is not None or _payload_has_value(
        raw_payload,
        "prompt_audio_path",
        "prompt_speech",
        "reference_audio_path",
    )
    effective_cost = _apply_effective_resource_cost(
        request,
        MOSS_CLONE_RESOURCE_COST if has_prompt_audio else MOSS_TTS_RESOURCE_COST,
    )
    request.state.audit_payload = {
        "feature": "moss_tts_clone" if has_prompt_audio else "moss_tts",
        "engine": "moss",
        "resource_cost": effective_cost,
        "response_format": response_format,
        "stream": stream_enabled,
        "language": _payload_get(raw_payload, "language", "lang", default=""),
        "voice": _payload_get(raw_payload, "voice", default=""),
        "volume": volume,
        "has_prompt_audio": has_prompt_audio,
    }

    upstream_payload = _build_moss_payload(
        raw_payload,
        text=text,
        response_format=response_format,
        stream_enabled=stream_enabled,
        as_form=prompt_audio is not None or is_form_request,
        has_prompt_audio=has_prompt_audio,
    )
    upstream_path = "/v1/audio/speech/stream" if stream_enabled else "/v1/audio/speech"

    if prompt_audio is not None:
        await prompt_audio.seek(0)
        files = {
            "prompt_audio": (
                prompt_audio.filename or "prompt_audio.wav",
                prompt_audio.file,
                prompt_audio.content_type or "application/octet-stream",
            )
        }
        client, response = await _open_upstream_stream(
            method="POST",
            url=_moss_upstream_url(upstream_path),
            data=_stringify_form_payload(upstream_payload),
            files=files,
            headers=_moss_auth_headers(),
            timeout=_moss_timeout(),
        )
    else:
        client, response = await _open_upstream_stream(
            method="POST",
            url=_moss_upstream_url(upstream_path),
            json_payload=upstream_payload,
            headers=_moss_auth_headers(),
            timeout=_moss_timeout(),
        )

    return StreamingResponse(
        _iter_upstream_bytes(client, response),
        media_type=_media_type(response, response_format),
        headers=_audio_headers(response),
        status_code=response.status_code,
    )


@router.get("/tts/moss/voices")
async def v2_moss_voices(
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    _ensure_moss_tts_enabled()
    url = _moss_upstream_url("/v1/audio/voices")
    try:
        async with httpx.AsyncClient(timeout=_moss_timeout()) as client:
            response = await client.get(url, headers=_moss_auth_headers())
    except httpx.TimeoutException as exc:
        logger.warning("[V2-TTS] MOSS voices upstream timeout url=%s error=%s", url, exc)
        raise HTTPException(status_code=504, detail="MOSS TTS upstream timeout") from exc
    except httpx.HTTPError as exc:
        logger.warning("[V2-TTS] MOSS voices upstream unavailable url=%s error=%s", url, exc)
        raise HTTPException(status_code=503, detail="MOSS TTS upstream unavailable") from exc

    if response.status_code >= 400:
        logger.warning(
            "[V2-TTS] MOSS voices upstream failed status=%s body=%s",
            response.status_code,
            response.content[:300],
        )
        raise HTTPException(status_code=response.status_code, detail=_upstream_error_detail(response.content))

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="MOSS TTS upstream returned invalid voices payload") from exc
    return JSONResponse(content=payload, status_code=response.status_code)


@router.post("/tts/omnivoice")
async def v2_omnivoice_tts(
    request: Request,
    payload: V2OmniVoiceTTSRequest,
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    text = _validate_omnivoice_tts_input(payload.input or payload.text or "")
    response_format = _normalize_omnivoice_response_format(payload.response_format)
    language = _normalize_omnivoice_language(payload.language)

    effective_cost = _apply_effective_resource_cost(request, OMNIVOICE_TTS_RESOURCE_COST)
    request.state.audit_payload = {
        "feature": "omnivoice_tts",
        "engine": "omnivoice",
        "resource_cost": effective_cost,
        "response_format": response_format,
        "stream": payload.stream,
        "language": language or "",
        "voice": payload.voice or "",
        "has_instruct": bool((payload.instruct or "").strip()),
        "volume": payload.volume,
    }

    client, response = await _open_upstream_stream(
        method="POST",
        url=_omnivoice_upstream_url("/v1/audio/speech"),
        json_payload=_build_omnivoice_speech_payload(
            payload,
            text=text,
            response_format=response_format,
            language=language,
        ),
        headers=_omnivoice_auth_headers(),
        timeout=_omnivoice_timeout(),
    )
    return StreamingResponse(
        _iter_upstream_bytes(client, response),
        media_type=_media_type(response, response_format),
        headers=_audio_headers(response),
        status_code=response.status_code,
    )


@router.get("/tts/omnivoice/languages")
async def v2_omnivoice_languages(
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    url = _omnivoice_upstream_url("/v1/languages")
    try:
        async with httpx.AsyncClient(timeout=_omnivoice_timeout()) as client:
            response = await client.get(url, headers=_omnivoice_auth_headers())
    except httpx.TimeoutException as exc:
        logger.warning("[V2-TTS] OmniVoice languages upstream timeout url=%s error=%s", url, exc)
        raise HTTPException(status_code=504, detail="OmniVoice TTS upstream timeout") from exc
    except httpx.HTTPError as exc:
        logger.warning("[V2-TTS] OmniVoice languages upstream unavailable url=%s error=%s", url, exc)
        raise HTTPException(status_code=503, detail="OmniVoice TTS upstream unavailable") from exc

    if response.status_code >= 400:
        logger.warning(
            "[V2-TTS] OmniVoice languages upstream failed status=%s body=%s",
            response.status_code,
            response.content[:300],
        )
        raise HTTPException(status_code=response.status_code, detail=_upstream_error_detail(response.content))

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="OmniVoice TTS upstream returned invalid languages payload") from exc
    return JSONResponse(content=payload, status_code=response.status_code)


@router.post("/clone/omnivoice")
async def v2_omnivoice_clone(
    request: Request,
    input: Optional[str] = Form(None),
    text_input: Optional[str] = Form(None, alias="text"),
    ref_audio: UploadFile = File(...),
    ref_text: Optional[str] = Form(None),
    language: str = Form("auto"),
    target_language: Optional[str] = Form(None),
    targetLanguage: Optional[str] = Form(None),
    text_language: Optional[str] = Form(None),
    textLanguage: Optional[str] = Form(None),
    synthesis_language: Optional[str] = Form(None),
    synthesisLanguage: Optional[str] = Form(None),
    response_format: str = Form("wav"),
    stream: bool = Form(True),
    duration: Optional[float] = Form(None),
    speed: Optional[float] = Form(None),
    num_step: int = Form(32),
    guidance_scale: float = Form(2.0),
    t_shift: float = Form(0.1),
    denoise: bool = Form(True),
    preprocess_prompt: bool = Form(True),
    postprocess_output: bool = Form(True),
    layer_penalty_factor: float = Form(5.0),
    position_temperature: float = Form(5.0),
    class_temperature: float = Form(0.0),
    instruct: Optional[str] = Form(None),
    volume: Optional[float] = Form(None),
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    text = _validate_omnivoice_tts_input(input or text_input or "")
    response_format = _normalize_omnivoice_response_format(response_format)
    volume = _validate_optional_volume(volume)
    resolved_language = _resolve_optional_omnivoice_language(
        target_language,
        targetLanguage,
        text_language,
        textLanguage,
        synthesis_language,
        synthesisLanguage,
        language,
    )

    effective_cost = _apply_effective_resource_cost(request, OMNIVOICE_CLONE_RESOURCE_COST)
    request.state.audit_payload = {
        "feature": "omnivoice_tts_clone",
        "engine": "omnivoice",
        "resource_cost": effective_cost,
        "response_format": response_format,
        "stream": stream,
        "language": resolved_language or "",
        "has_ref_text": bool((ref_text or "").strip()),
        "ref_audio_content_type": _normalized_upload_content_type(ref_audio),
        "volume": volume,
    }

    data: dict[str, Any] = {
        "text": text,
        "response_format": response_format,
        "stream": stream,
        "num_step": num_step,
        "guidance_scale": guidance_scale,
        "t_shift": t_shift,
        "denoise": denoise,
        "preprocess_prompt": preprocess_prompt,
        "postprocess_output": postprocess_output,
        "layer_penalty_factor": layer_penalty_factor,
        "position_temperature": position_temperature,
        "class_temperature": class_temperature,
    }
    if resolved_language:
        data["language"] = resolved_language
    if _first_nonempty_text(ref_text):
        data["ref_text"] = ref_text
    if _first_nonempty_text(instruct):
        data["instruct"] = instruct
    if duration is not None:
        data["duration"] = duration
    if speed is not None:
        data["speed"] = speed
    if volume is not None:
        data["volume"] = volume

    await ref_audio.seek(0)
    files = {
        "ref_audio": (
            ref_audio.filename or "reference.wav",
            ref_audio.file,
            ref_audio.content_type or "application/octet-stream",
        )
    }
    client, response = await _open_upstream_stream(
        method="POST",
        url=_omnivoice_upstream_url("/v1/audio/clone"),
        data=_stringify_form_payload(data),
        files=files,
        headers=_omnivoice_auth_headers(),
        timeout=_omnivoice_timeout(),
    )
    return StreamingResponse(
        _iter_upstream_bytes(client, response),
        media_type=_media_type(response, response_format),
        headers=_audio_headers(response),
        status_code=response.status_code,
    )


@router.post("/clone/moss")
async def v2_moss_clone(
    request: Request,
    input: str = Form(...),
    ref_audio: Optional[UploadFile] = File(None),
    prompt_audio: Optional[UploadFile] = File(None),
    ref_text: Optional[str] = Form(None),
    language: str = Form(""),
    target_language: Optional[str] = Form(None),
    targetLanguage: Optional[str] = Form(None),
    text_language: Optional[str] = Form(None),
    textLanguage: Optional[str] = Form(None),
    synthesis_language: Optional[str] = Form(None),
    synthesisLanguage: Optional[str] = Form(None),
    prompt_audio_path: Optional[str] = Form(None),
    prompt_speech: Optional[str] = Form(None),
    reference_audio_path: Optional[str] = Form(None),
    response_format: str = Form("wav"),
    stream: bool = Form(True),
    sample_mode: Optional[str] = Form(None),
    do_sample: Optional[bool] = Form(None),
    max_new_frames: Optional[int] = Form(None),
    voice_clone_max_text_tokens: Optional[int] = Form(None),
    voice_clone_inter_chunk_pause_seconds: Optional[float] = Form(None),
    trim_long_silence: Optional[bool] = Form(None),
    max_silence_seconds: Optional[float] = Form(None),
    silence_threshold: Optional[float] = Form(None),
    cpu_threads: Optional[int] = Form(None),
    enable_text_normalization: Optional[bool] = Form(None),
    enable_wetext_processing: Optional[bool] = Form(None),
    enable_normalize_tts_text: Optional[bool] = Form(None),
    text_temperature: Optional[float] = Form(None),
    text_top_p: Optional[float] = Form(None),
    text_top_k: Optional[int] = Form(None),
    audio_temperature: Optional[float] = Form(None),
    audio_top_p: Optional[float] = Form(None),
    audio_top_k: Optional[int] = Form(None),
    audio_repetition_penalty: Optional[float] = Form(None),
    volume: Optional[float] = Form(None),
    seed: Optional[int] = Form(None),
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    _ensure_moss_tts_enabled()
    text = _validate_moss_tts_input(input)
    response_format = _normalize_moss_response_format(response_format)
    volume = _validate_optional_volume(volume)
    selected_prompt_audio = prompt_audio or ref_audio
    selected_prompt_path = (prompt_audio_path or prompt_speech or reference_audio_path or "").strip()
    if selected_prompt_audio is None and not selected_prompt_path:
        raise HTTPException(status_code=400, detail="prompt_audio, ref_audio, or prompt_audio_path is required")

    resolved_language = _first_nonempty_text(
        target_language,
        targetLanguage,
        text_language,
        textLanguage,
        synthesis_language,
        synthesisLanguage,
        language,
    )

    effective_cost = _apply_effective_resource_cost(request, MOSS_CLONE_RESOURCE_COST)
    request.state.audit_payload = {
        "feature": "moss_tts_clone",
        "engine": "moss",
        "resource_cost": effective_cost,
        "response_format": response_format,
        "stream": stream,
        "language": resolved_language or "",
        "has_prompt_audio": selected_prompt_audio is not None,
        "has_prompt_audio_path": bool(selected_prompt_path),
        "has_ref_text": bool((ref_text or "").strip()),
        "volume": volume,
    }

    raw_payload: dict[str, Any] = {
        "input": text,
        "response_format": response_format,
        "stream": stream,
    }
    if resolved_language:
        raw_payload["language"] = resolved_language
    if selected_prompt_path:
        raw_payload["prompt_audio_path"] = selected_prompt_path

    optional_values: dict[str, Any] = {
        "sample_mode": sample_mode,
        "do_sample": do_sample,
        "max_new_frames": max_new_frames,
        "voice_clone_max_text_tokens": voice_clone_max_text_tokens,
        "voice_clone_inter_chunk_pause_seconds": voice_clone_inter_chunk_pause_seconds,
        "trim_long_silence": trim_long_silence,
        "max_silence_seconds": max_silence_seconds,
        "silence_threshold": silence_threshold,
        "cpu_threads": cpu_threads,
        "enable_text_normalization": enable_text_normalization,
        "enable_wetext_processing": enable_wetext_processing,
        "enable_normalize_tts_text": enable_normalize_tts_text,
        "text_temperature": text_temperature,
        "text_top_p": text_top_p,
        "text_top_k": text_top_k,
        "audio_temperature": audio_temperature,
        "audio_top_p": audio_top_p,
        "audio_top_k": audio_top_k,
        "audio_repetition_penalty": audio_repetition_penalty,
        "volume": volume,
        "seed": seed,
    }
    for key, value in optional_values.items():
        if value is not None:
            raw_payload[key] = value

    upstream_payload = _build_moss_payload(
        raw_payload,
        text=text,
        response_format=response_format,
        stream_enabled=stream,
        as_form=True,
        has_prompt_audio=selected_prompt_audio is not None or bool(selected_prompt_path),
    )
    upstream_path = "/v1/audio/speech/stream" if stream else "/v1/audio/speech"

    files: dict[str, Any] | None = None
    if selected_prompt_audio is not None:
        await selected_prompt_audio.seek(0)
        files = {
            "prompt_audio": (
                selected_prompt_audio.filename or "prompt_audio.wav",
                selected_prompt_audio.file,
                selected_prompt_audio.content_type or "application/octet-stream",
            )
        }

    client, response = await _open_upstream_stream(
        method="POST",
        url=_moss_upstream_url(upstream_path),
        data=_stringify_form_payload(upstream_payload),
        files=files,
        headers=_moss_auth_headers(),
        timeout=_moss_timeout(),
    )
    return StreamingResponse(
        _iter_upstream_bytes(client, response),
        media_type=_media_type(response, response_format),
        headers=_audio_headers(response),
        status_code=response.status_code,
    )


@router.post("/clone/qwen")
async def v2_clone(
    request: Request,
    input: str = Form(...),
    ref_audio: UploadFile = File(...),
    ref_text: str = Form(...),
    language: str = Form("Auto"),
    target_language: Optional[str] = Form(None),
    targetLanguage: Optional[str] = Form(None),
    text_language: Optional[str] = Form(None),
    textLanguage: Optional[str] = Form(None),
    synthesis_language: Optional[str] = Form(None),
    synthesisLanguage: Optional[str] = Form(None),
    ref_language: Optional[str] = Form(None),
    refLanguage: Optional[str] = Form(None),
    response_format: str = Form("wav"),
    chunk_size: int = Form(8),
    xvec_only: bool = Form(False),
    non_streaming_mode: Optional[bool] = Form(None),
    instruct: Optional[str] = Form(QWEN_DEFAULT_INSTRUCT),
    append_silence: bool = Form(True),
    ref_audio_encoding: Optional[str] = Form(None),
    ref_audio_sample_rate: int = Form(QWEN_CLONE_REF_AUDIO_SAMPLE_RATE),
    ref_audio_frame_ms: Optional[int] = Form(None),
    volume: Optional[float] = Form(None),
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    text = _validate_tts_input(input)
    volume = _validate_optional_volume(volume)
    if not (ref_text or "").strip() and not xvec_only:
        raise HTTPException(status_code=400, detail="ref_text is required unless xvec_only=true")
    if ref_text and len(ref_text.strip()) > max(1, int(settings.QWEN_TTS_MAX_INPUT_CHARS or 500)):
        raise HTTPException(status_code=400, detail="ref_text is too long")

    response_format = _normalize_response_format(response_format)
    normalized_chunk_size = _validate_chunk_size(chunk_size) or 8
    resolved_language = _resolve_clone_synthesis_language(
        target_language,
        targetLanguage,
        text_language,
        textLanguage,
        synthesis_language,
        synthesisLanguage,
        language,
    )
    resolved_ref_language = _resolve_optional_qwen_language(ref_language, refLanguage)

    effective_cost = _apply_effective_resource_cost(request, CLONE_RESOURCE_COST)
    request.state.audit_payload = {
        "feature": "tts_clone",
        "engine": "qwen",
        "resource_cost": effective_cost,
        "response_format": response_format,
        "language": resolved_language,
        "ref_language": resolved_ref_language,
        "xvec_only": xvec_only,
        "ref_audio_content_type": _normalized_upload_content_type(ref_audio),
        "ref_audio_encoding": ref_audio_encoding or "",
        "volume": volume,
    }

    # Qwen uses language for the synthesized text, not the reference audio language.
    data: dict[str, Any] = {
        "input": text,
        "ref_text": ref_text,
        "language": resolved_language,
        "response_format": response_format,
        "chunk_size": str(normalized_chunk_size),
        "xvec_only": "true" if xvec_only else "false",
        "append_silence": "true" if append_silence else "false",
    }
    if non_streaming_mode is not None:
        data["non_streaming_mode"] = "true" if non_streaming_mode else "false"
    normalized_instruct = _normalize_qwen_instruct(instruct)
    if normalized_instruct:
        data["instruct"] = normalized_instruct
    if volume is not None:
        data["volume"] = str(volume)

    if ref_audio_frame_ms is not None and ref_audio_frame_ms != 20:
        raise HTTPException(status_code=400, detail="ref_audio_frame_ms must be 20 for packaged Opus ref_audio")

    files = {
        "ref_audio": await _prepare_qwen_clone_ref_audio_file(
            ref_audio,
            ref_audio_encoding,
            ref_audio_sample_rate,
        )
    }
    client, response = await _open_upstream_stream(
        method="POST",
        url=_upstream_url("/v1/audio/voice-clone"),
        data=data,
        files=files,
    )
    return StreamingResponse(
        _iter_upstream_bytes(client, response),
        media_type=_media_type(response, response_format),
        headers=_audio_headers(response),
        status_code=response.status_code,
    )
