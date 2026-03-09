from typing import Any, Dict, List, Optional

import httpx

from core.config import settings
from core.logging_config import logger

LANG_CODE_MAPPING = {
    "zt": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh-hans": "zh",
}

LANGUAGE_NAMES = {
    "ar": "Arabic",
    "az": "Azerbaijani",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "eo": "Esperanto",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Irish",
    "gl": "Galician",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "ms": "Malay",
    "nb": "Norwegian Bokmal",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sq": "Albanian",
    "sv": "Swedish",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "zh": "Chinese",
    "zh-Hant": "Traditional Chinese",
}

VOICE_LINK_GEMMA_LANG_CODES = {
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
    "eo", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "he",
    "hi", "hu", "id", "it", "ja", "ko", "lt", "lv", "ms", "nb",
    "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sv", "th",
    "tl", "tr", "uk", "ur", "zh", "zt",
}

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


def is_auto_lang_code(lang_code: str) -> bool:
    return (lang_code or "").strip().lower() == "auto"


def _map_lang_code(lang_code: str) -> str:
    raw = (lang_code or "").strip().lower()
    return LANG_CODE_MAPPING.get(raw, raw)


def _normalize_lang_code(lang_code: str) -> str:
    mapped = _map_lang_code(lang_code)
    if mapped in GEMMA_SUPPORTED_LANG_CODES:
        return mapped

    if "-" in mapped:
        base = mapped.split("-", 1)[0]
        if base in GEMMA_SUPPORTED_LANG_CODES:
            return base
    return mapped


def is_gemma_translation_supported(source_lang: str, target_lang: str) -> bool:
    if is_auto_lang_code(source_lang) or is_auto_lang_code(target_lang):
        return False

    source = _normalize_lang_code(source_lang)
    target = _normalize_lang_code(target_lang)
    return source in GEMMA_SUPPORTED_LANG_CODES and target in GEMMA_SUPPORTED_LANG_CODES


def get_gemma_capability_entry() -> Dict[str, Any]:
    supported_codes = sorted(VOICE_LINK_GEMMA_LANG_CODES)
    language_map = {
        source: [target for target in supported_codes if target != source]
        for source in supported_codes
    }
    return {
        "engine": "translategemma",
        "languages": language_map,
        "endpoint": "/completion" if _resolve_api_mode() != "openai" else "/v1/chat/completions",
        "apiMode": _resolve_api_mode(),
    }


def _resolve_base_url() -> str:
    raw = (settings.GEMMA_TRANSLATE_URL or "").strip().rstrip("/")
    if not raw:
        raw = "127.0.0.1:8080"
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return f"http://{raw}"


def _resolve_max_new_tokens(text: str, max_new_tokens: Optional[int]) -> int:
    if max_new_tokens is not None:
        return max(1, min(max_new_tokens, 2000))
    estimated = max(len(text) * 3, settings.GEMMA_TRANSLATE_MAX_TOKENS)
    return max(1, min(estimated, 2000))


def _resolve_api_mode() -> str:
    mode = (settings.GEMMA_TRANSLATE_API_MODE or "completion").strip().lower()
    if mode in {"completion", "openai", "auto"}:
        return mode
    logger.warning("[GEMMA_TRANSLATE] unknown API mode '%s', fallback to completion", settings.GEMMA_TRANSLATE_API_MODE)
    return "completion"


def _language_name(lang_code: str) -> str:
    normalized = _normalize_lang_code(lang_code)
    if normalized in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[normalized]
    if "-" in normalized:
        base = normalized.split("-", 1)[0]
        if base in LANGUAGE_NAMES:
            return LANGUAGE_NAMES[base]
    return normalized


def _post_process(text: str) -> str:
    if not text:
        return text

    stripped = text.strip()
    if len(stripped) < 2:
        return stripped

    for left, right in QUOTE_PAIRS:
        if stripped.startswith(left) and stripped.endswith(right):
            inner = stripped[len(left): len(stripped) - len(right)].strip()
            return inner

    return stripped


def _apply_extra_params(payload: Dict[str, Any], extra_params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not extra_params:
        return payload

    for key, value in extra_params.items():
        if value is None:
            continue
        if key == "chat_template_kwargs":
            continue
        if (
            key == "chat_template_kwargs"
            and isinstance(value, dict)
            and isinstance(payload.get("chat_template_kwargs"), dict)
        ):
            payload["chat_template_kwargs"].update(value)
            continue
        payload[key] = value
    return payload


def _build_openai_payload(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str],
    max_new_tokens: Optional[int],
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "messages": [
            {
                "role": "user",
                "content": text,
            }
        ],
        "chat_template_kwargs": {
            "source_lang_code": _map_lang_code(source_lang),
            "target_lang_code": _map_lang_code(target_lang),
        },
        "max_tokens": _resolve_max_new_tokens(text, max_new_tokens),
        "temperature": 0.2,
    }

    resolved_model = model if model is not None else settings.GEMMA_TRANSLATE_MODEL
    if resolved_model:
        payload["model"] = resolved_model

    return _apply_extra_params(payload, extra_params)


def _build_completion_prompt(text: str, source_lang: str, target_lang: str) -> str:
    mapped_source = _map_lang_code(source_lang)
    mapped_target = _map_lang_code(target_lang)
    source_name = _language_name(source_lang)
    target_name = _language_name(target_lang)
    return (
        f"<start_of_turn>user\n"
        f"You are a professional {source_name} ({mapped_source}) to {target_name} ({mapped_target}) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {source_name} text while adhering "
        f"to {target_name} grammar, vocabulary, and cultural sensitivities.\n"
        f"Produce only the {target_name} translation, without any additional explanations or commentary. Please "
        f"translate the following {source_name} text into {target_name}:\n\n\n"
        f"{text}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def _build_completion_payload(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str],
    max_new_tokens: Optional[int],
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "prompt": _build_completion_prompt(text, source_lang, target_lang),
        "n_predict": _resolve_max_new_tokens(text, max_new_tokens),
        "temperature": 0.2,
        "cache_prompt": True,
    }

    resolved_model = model if model is not None else settings.GEMMA_TRANSLATE_MODEL
    if resolved_model:
        payload["model"] = resolved_model

    return _apply_extra_params(payload, extra_params)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("text") is not None:
            return str(content.get("text"))
        return str(content)
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if item.get("text") is not None:
                    parts.append(str(item.get("text")))
                elif item.get("content") is not None:
                    parts.append(str(item.get("content")))
        return "".join(parts)
    return str(content)


def _extract_openai_translation(response_data: Any) -> Optional[str]:
    if not isinstance(response_data, dict):
        return None

    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None

    message = first_choice.get("message")
    if isinstance(message, dict):
        translated = _content_to_text(message.get("content"))
        return _post_process(translated)

    if first_choice.get("text") is not None:
        return _post_process(str(first_choice.get("text")))

    return None


def _extract_completion_translation(response_data: Any) -> Optional[str]:
    if not isinstance(response_data, dict):
        return None

    content = response_data.get("content")
    if content is None:
        return None

    return _post_process(_content_to_text(content))


def _log_request_context(prefix: str, source_lang: str, target_lang: str, mapped_source: str, mapped_target: str, text: str) -> None:
    logger.info(
        "%s request %s(%s)->%s(%s), text_len=%s",
        prefix,
        source_lang,
        mapped_source,
        target_lang,
        mapped_target,
        len(text),
    )


def _try_completion_sync(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str],
    max_new_tokens: Optional[int],
    extra_params: Optional[Dict[str, Any]],
) -> Optional[str]:
    payload = _build_completion_payload(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    mapped_source = _map_lang_code(source_lang)
    mapped_target = _map_lang_code(target_lang)
    translate_url = f"{_resolve_base_url()}/completion"
    prefix = "[GEMMA_TRANSLATE][COMPLETION]"

    try:
        _log_request_context(prefix, source_lang, target_lang, mapped_source, mapped_target, text)
        with httpx.Client(timeout=settings.GEMMA_TRANSLATE_TIMEOUT) as client:
            response = client.post(translate_url, json=payload)
            response.raise_for_status()
            translated = _extract_completion_translation(response.json())
            if translated is None:
                logger.warning("%s invalid response payload from %s", prefix, translate_url)
            return translated
    except httpx.TimeoutException:
        logger.warning("%s timeout calling %s", prefix, translate_url)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("%s http error: %s, body=%s", prefix, exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("%s unexpected error: %s", prefix, exc)
        return None


async def _try_completion_async(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str],
    max_new_tokens: Optional[int],
    extra_params: Optional[Dict[str, Any]],
) -> Optional[str]:
    payload = _build_completion_payload(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    mapped_source = _map_lang_code(source_lang)
    mapped_target = _map_lang_code(target_lang)
    translate_url = f"{_resolve_base_url()}/completion"
    prefix = "[GEMMA_TRANSLATE][COMPLETION]"

    try:
        _log_request_context(prefix, source_lang, target_lang, mapped_source, mapped_target, text)
        async with httpx.AsyncClient(timeout=settings.GEMMA_TRANSLATE_TIMEOUT) as client:
            response = await client.post(translate_url, json=payload)
            response.raise_for_status()
            translated = _extract_completion_translation(response.json())
            if translated is None:
                logger.warning("%s invalid response payload from %s", prefix, translate_url)
            return translated
    except httpx.TimeoutException:
        logger.warning("%s timeout calling %s", prefix, translate_url)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("%s http error: %s, body=%s", prefix, exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("%s unexpected error: %s", prefix, exc)
        return None


def _try_openai_sync(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str],
    max_new_tokens: Optional[int],
    extra_params: Optional[Dict[str, Any]],
) -> Optional[str]:
    payload = _build_openai_payload(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    kwargs = payload.get("chat_template_kwargs", {})
    mapped_source = str(kwargs.get("source_lang_code", source_lang))
    mapped_target = str(kwargs.get("target_lang_code", target_lang))
    translate_url = f"{_resolve_base_url()}/v1/chat/completions"
    prefix = "[GEMMA_TRANSLATE][OPENAI]"

    try:
        _log_request_context(prefix, source_lang, target_lang, mapped_source, mapped_target, text)
        with httpx.Client(timeout=settings.GEMMA_TRANSLATE_TIMEOUT) as client:
            response = client.post(translate_url, json=payload)
            response.raise_for_status()
            translated = _extract_openai_translation(response.json())
            if translated is None:
                logger.warning("%s invalid response payload from %s", prefix, translate_url)
            return translated
    except httpx.TimeoutException:
        logger.warning("%s timeout calling %s", prefix, translate_url)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("%s http error: %s, body=%s", prefix, exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("%s unexpected error: %s", prefix, exc)
        return None


async def _try_openai_async(
    text: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str],
    max_new_tokens: Optional[int],
    extra_params: Optional[Dict[str, Any]],
) -> Optional[str]:
    payload = _build_openai_payload(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    kwargs = payload.get("chat_template_kwargs", {})
    mapped_source = str(kwargs.get("source_lang_code", source_lang))
    mapped_target = str(kwargs.get("target_lang_code", target_lang))
    translate_url = f"{_resolve_base_url()}/v1/chat/completions"
    prefix = "[GEMMA_TRANSLATE][OPENAI]"

    try:
        _log_request_context(prefix, source_lang, target_lang, mapped_source, mapped_target, text)
        async with httpx.AsyncClient(timeout=settings.GEMMA_TRANSLATE_TIMEOUT) as client:
            response = await client.post(translate_url, json=payload)
            response.raise_for_status()
            translated = _extract_openai_translation(response.json())
            if translated is None:
                logger.warning("%s invalid response payload from %s", prefix, translate_url)
            return translated
    except httpx.TimeoutException:
        logger.warning("%s timeout calling %s", prefix, translate_url)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("%s http error: %s, body=%s", prefix, exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("%s unexpected error: %s", prefix, exc)
        return None


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

    if not is_gemma_translation_supported(source_lang, target_lang):
        logger.warning(
            "[GEMMA_TRANSLATE] unsupported language pair for TranslateGemma: source=%s, target=%s",
            source_lang,
            target_lang,
        )
        return None

    mode = _resolve_api_mode()
    if mode == "completion":
        return _try_completion_sync(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    if mode == "openai":
        return _try_openai_sync(text, source_lang, target_lang, model, max_new_tokens, extra_params)

    translated = _try_completion_sync(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    if translated is not None:
        return translated
    return _try_openai_sync(text, source_lang, target_lang, model, max_new_tokens, extra_params)


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

    if not is_gemma_translation_supported(source_lang, target_lang):
        logger.warning(
            "[GEMMA_TRANSLATE] unsupported language pair for TranslateGemma: source=%s, target=%s",
            source_lang,
            target_lang,
        )
        return None

    mode = _resolve_api_mode()
    if mode == "completion":
        return await _try_completion_async(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    if mode == "openai":
        return await _try_openai_async(text, source_lang, target_lang, model, max_new_tokens, extra_params)

    translated = await _try_completion_async(text, source_lang, target_lang, model, max_new_tokens, extra_params)
    if translated is not None:
        return translated
    return await _try_openai_async(text, source_lang, target_lang, model, max_new_tokens, extra_params)
