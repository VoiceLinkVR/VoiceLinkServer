import base64
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
    "zh": "Simplified Chinese",
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


def is_dynamic_source_lang_code(lang_code: str) -> bool:
    return (lang_code or "").strip().lower() in {"auto", "multilingual"}


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
    if is_auto_lang_code(target_lang) or is_dynamic_source_lang_code(target_lang):
        return False

    if is_dynamic_source_lang_code(source_lang):
        return _normalize_lang_code(target_lang) in GEMMA_SUPPORTED_LANG_CODES

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
    target_name = _language_name(target_lang)
    mapped_target = _map_lang_code(target_lang)
    if is_dynamic_source_lang_code(source_lang):
        content = (
            f"You are a professional multilingual translator. Detect the source language or languages in the text, "
            f"then translate all meaningful content into {target_name} ({mapped_target}). Preserve names, numbers, "
            f"and intent. Produce only the {target_name} translation, without explanations or commentary.\n\n{text}"
        )
    else:
        content = text

    payload: Dict[str, Any] = {
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "chat_template_kwargs": {
            "source_lang_code": "auto" if is_dynamic_source_lang_code(source_lang) else _map_lang_code(source_lang),
            "target_lang_code": mapped_target,
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
    if is_dynamic_source_lang_code(source_lang):
        return (
            f"<start_of_turn>user\n"
            f"You are a professional multilingual translator. Detect the source language or languages in the text, "
            f"then translate all meaningful content into {target_name} ({mapped_target}). Preserve names, numbers, "
            f"and intent. Produce only the {target_name} translation, without explanations or commentary. "
            f"Translate the following text:\n\n\n"
            f"{text}<end_of_turn>\n"
            f"<start_of_turn>model\n"
        )

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


def _build_image_completion_prompt(source_lang: str, target_lang: str) -> str:
    mapped_source = _map_lang_code(source_lang)
    mapped_target = _map_lang_code(target_lang)
    target_name = _language_name(target_lang)
    target_extra_instruction = _image_target_extra_instruction(mapped_target, target_name)

    if is_auto_lang_code(source_lang):
        return (
            f"<start_of_turn>user\n"
            f"You are a professional translator. Detect the source language text in the provided image and translate it into "
            f"{target_name} ({mapped_target}). Produce only the {target_name} translation, without any additional explanations, "
            f"alternatives or commentary. Focus only on the text, do not output where the text is located, surrounding objects "
            f"or any other explanation about the picture. Ignore symbols, pictogram, and arrows! "
            f"{target_extra_instruction}\n\n\n"
            f"<__media__><end_of_turn>\n"
            f"<start_of_turn>model\n"
        )

    source_name = _language_name(source_lang)
    return (
        f"<start_of_turn>user\n"
        f"You are a professional {source_name} ({mapped_source}) to {target_name} ({mapped_target}) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {source_name} text while adhering "
        f"to {target_name} grammar, vocabulary, and cultural sensitivities.\n"
        f"Please translate the {source_name} text in the provided image into {target_name}. Produce only the {target_name} "
        f"translation, without any additional explanations, alternatives or commentary. Focus only on the text, do not "
        f"output where the text is located, surrounding objects or any other explanation about the picture. Ignore "
        f"symbols, pictogram, and arrows! {target_extra_instruction}\n\n\n"
        f"<__media__><end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def _image_target_extra_instruction(mapped_target: str, target_name: str) -> str:
    if mapped_target == "zh":
        return (
            "Use Simplified Chinese characters. If the image text is English or another Latin-script language, "
            "translate it into Simplified Chinese instead of copying the source text unchanged. "
            "For proper nouns, translate common words and transliterate names when needed."
        )

    if mapped_target == "zh-Hant":
        return (
            "Use Traditional Chinese characters. If the image text is English or another Latin-script language, "
            "translate it into Traditional Chinese instead of copying the source text unchanged. "
            "For proper nouns, translate common words and transliterate names when needed."
        )

    return f"Do not return the source text unchanged unless it is already valid {target_name}."


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


def _guess_image_media_type(image_bytes: bytes, media_type: Optional[str]) -> str:
    normalized = (media_type or "").strip().lower()
    if normalized in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        return "image/jpeg" if normalized == "image/jpg" else normalized

    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _build_image_data_url(image_bytes: bytes, media_type: Optional[str]) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{_guess_image_media_type(image_bytes, media_type)};base64,{encoded}"


def is_gemma_image_translation_supported(source_lang: str, target_lang: str) -> bool:
    if is_auto_lang_code(target_lang):
        return False

    normalized_target = _normalize_lang_code(target_lang)
    if normalized_target not in GEMMA_SUPPORTED_LANG_CODES:
        return False

    if is_auto_lang_code(source_lang):
        return True

    normalized_source = _normalize_lang_code(source_lang)
    return normalized_source in GEMMA_SUPPORTED_LANG_CODES


def _build_image_completion_payload(
    image_bytes: bytes,
    media_type: Optional[str],
    source_lang: str,
    target_lang: str,
    model: Optional[str],
    max_new_tokens: Optional[int],
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "prompt": {
            "prompt_string": _build_image_completion_prompt(source_lang, target_lang),
            "multimodal_data": [
                base64.b64encode(image_bytes).decode("ascii"),
            ],
        },
        "n_predict": _resolve_max_new_tokens("", max_new_tokens),
        "temperature": 0.2,
        "stream": False,
        "cache_prompt": True,
    }

    resolved_model = model if model is not None else settings.GEMMA_TRANSLATE_MODEL
    if resolved_model:
        payload["model"] = resolved_model

    return _apply_extra_params(payload, extra_params)


def _build_image_openai_payload(
    image_bytes: bytes,
    media_type: Optional[str],
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
                "content": [
                    {
                        "type": "image",
                        "source_lang_code": _map_lang_code(source_lang),
                        "target_lang_code": _map_lang_code(target_lang),
                        "url": _build_image_data_url(image_bytes, media_type),
                    }
                ],
            }
        ],
        "max_tokens": _resolve_max_new_tokens("", max_new_tokens),
        "temperature": 0.2,
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


async def async_gemma_image_translate(
    image_bytes: bytes,
    source_lang: str,
    target_lang: str,
    media_type: Optional[str] = None,
    model: Optional[str] = None,
    max_new_tokens: Optional[int] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not image_bytes:
        return ""

    if not is_gemma_image_translation_supported(source_lang, target_lang):
        logger.warning(
            "[GEMMA_TRANSLATE] unsupported image translation language pair: source=%s, target=%s",
            source_lang,
            target_lang,
        )
        return None

    mode = _resolve_api_mode()
    if mode in {"completion", "auto"}:
        payload = _build_image_completion_payload(
            image_bytes=image_bytes,
            media_type=media_type,
            source_lang=source_lang,
            target_lang=target_lang,
            model=model,
            max_new_tokens=max_new_tokens,
            extra_params=extra_params,
        )
        translate_url = f"{_resolve_base_url()}/completion"
        prefix = "[GEMMA_TRANSLATE][IMAGE][COMPLETION]"

        try:
            _log_request_context(prefix, source_lang, target_lang, _map_lang_code(source_lang), _map_lang_code(target_lang), "<image>")
            async with httpx.AsyncClient(timeout=settings.GEMMA_TRANSLATE_TIMEOUT) as client:
                response = await client.post(translate_url, json=payload)
                response.raise_for_status()
                translated = _extract_completion_translation(response.json())
                if translated is None:
                    logger.warning("%s invalid response payload from %s", prefix, translate_url)
                if translated is not None or mode == "completion":
                    return translated
        except httpx.TimeoutException:
            logger.warning("%s timeout calling %s", prefix, translate_url)
            if mode == "completion":
                return None
        except httpx.HTTPStatusError as exc:
            logger.warning("%s http error: %s, body=%s", prefix, exc.response.status_code, exc.response.text[:300])
            if mode == "completion":
                return None
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("%s unexpected error: %s", prefix, exc)
            if mode == "completion":
                return None

    payload = _build_image_openai_payload(
        image_bytes=image_bytes,
        media_type=media_type,
        source_lang=source_lang,
        target_lang=target_lang,
        model=model,
        max_new_tokens=max_new_tokens,
        extra_params=extra_params,
    )
    translate_url = f"{_resolve_base_url()}/v1/chat/completions"
    prefix = "[GEMMA_TRANSLATE][IMAGE][OPENAI]"

    try:
        _log_request_context(prefix, source_lang, target_lang, _map_lang_code(source_lang), _map_lang_code(target_lang), "<image>")
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
