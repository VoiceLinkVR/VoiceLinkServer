from typing import Any, Dict, Optional

import httpx

from core.config import settings, GEMMA_TRANSLATE_BASE_URL
from core.logging_config import logger

LANG_CODE_MAPPING = {
    "zt": "zh-Hant",
    "auto": "zh",
}

# Gemma language gating for VoiceLink currently supported translation targets.
GEMMA_SUPPORTED_LANG_CODES = {
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
    "eo", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "he",
    "hi", "hu", "id", "it", "ja", "ko", "lt", "lv", "ms", "nb",
    "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sv", "th",
    "tl", "tr", "uk", "ur", "zh", "zh-Hant",
}

QUOTE_PAIRS = [
    ('"', '"'),
    ("'", "'"),
    ("\u201c", "\u201d"),
    ("\u300c", "\u300d"),
    ("\u300e", "\u300f"),
]


def _map_lang_code(lang_code: str) -> str:
    return LANG_CODE_MAPPING.get(lang_code, lang_code)


def _normalize_lang_code(lang_code: str) -> str:
    mapped = _map_lang_code((lang_code or "").strip())
    if mapped in GEMMA_SUPPORTED_LANG_CODES:
        return mapped

    # Allow regional variants when base language is supported.
    if "-" in mapped:
        base = mapped.split("-", 1)[0]
        if base in GEMMA_SUPPORTED_LANG_CODES:
            return base
    return mapped


def is_gemma_translation_supported(source_lang: str, target_lang: str) -> bool:
    source = _normalize_lang_code(source_lang)
    target = _normalize_lang_code(target_lang)
    return source in GEMMA_SUPPORTED_LANG_CODES and target in GEMMA_SUPPORTED_LANG_CODES


def _resolve_max_new_tokens(text: str, max_new_tokens: Optional[int]) -> int:
    if max_new_tokens is not None:
        return max(1, min(max_new_tokens, 2000))
    estimated = max(len(text) * 3, settings.GEMMA_TRANSLATE_MAX_TOKENS)
    return max(1, min(estimated, 2000))


def _post_process(text: str) -> str:
    if not text:
        return text

    stripped = text.strip()
    if len(stripped) < 2:
        return stripped

    for left, right in QUOTE_PAIRS:
        if stripped.startswith(left) and stripped.endswith(right):
            inner = stripped[len(left) : len(stripped) - len(right)].strip()
            return inner

    return stripped


def _build_payload(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str],
    max_new_tokens: Optional[int],
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model or settings.GEMMA_TRANSLATE_MODEL,
        "source_lang_code": _map_lang_code(source_lang),
        "target_lang_code": _map_lang_code(target_lang),
        "text": text,
        "content_type": "text",
        "max_new_tokens": _resolve_max_new_tokens(text, max_new_tokens),
    }
    if extra_params:
        payload.update({k: v for k, v in extra_params.items() if v is not None})
    return payload


def gemma_translate(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str] = None,
    max_new_tokens: Optional[int] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not text or not text.strip():
        return ""

    payload = _build_payload(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    mapped_source = payload.get("source_lang_code", source_lang)
    mapped_target = payload.get("target_lang_code", target_lang)
    translate_url = f"{GEMMA_TRANSLATE_BASE_URL}/translate"

    try:
        logger.info(
            "[GEMMA_TRANSLATE] request %s(%s)->%s(%s), text_len=%s",
            source_lang,
            mapped_source,
            target_lang,
            mapped_target,
            len(text),
        )
        with httpx.Client(timeout=settings.GEMMA_TRANSLATE_TIMEOUT) as client:
            response = client.post(translate_url, json=payload)
            response.raise_for_status()
            data = response.json()
            translated = str(data.get("translated_text", ""))
            return _post_process(translated)
    except httpx.TimeoutException:
        logger.warning("[GEMMA_TRANSLATE] timeout calling %s", translate_url)
        return None
    except httpx.HTTPStatusError as exc:
        logger.error("[GEMMA_TRANSLATE] http error: %s, body=%s", exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("[GEMMA_TRANSLATE] unexpected error: %s", exc)
        return None


async def async_gemma_translate(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str] = None,
    max_new_tokens: Optional[int] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not text or not text.strip():
        return ""

    payload = _build_payload(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    mapped_source = payload.get("source_lang_code", source_lang)
    mapped_target = payload.get("target_lang_code", target_lang)
    translate_url = f"{GEMMA_TRANSLATE_BASE_URL}/translate"

    try:
        logger.info(
            "[GEMMA_TRANSLATE] request %s(%s)->%s(%s), text_len=%s",
            source_lang,
            mapped_source,
            target_lang,
            mapped_target,
            len(text),
        )
        async with httpx.AsyncClient(timeout=settings.GEMMA_TRANSLATE_TIMEOUT) as client:
            response = await client.post(translate_url, json=payload)
            response.raise_for_status()
            data = response.json()
            translated = str(data.get("translated_text", ""))
            return _post_process(translated)
    except httpx.TimeoutException:
        logger.warning("[GEMMA_TRANSLATE] timeout calling %s", translate_url)
        return None
    except httpx.HTTPStatusError as exc:
        logger.error("[GEMMA_TRANSLATE] http error: %s, body=%s", exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("[GEMMA_TRANSLATE] unexpected error: %s", exc)
        return None
