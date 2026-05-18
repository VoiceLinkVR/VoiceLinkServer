import base64
from typing import Any

import httpx

from core.config import settings
from core.gemma_translate_service import async_gemma_image_translate
from core.logging_config import logger


def _guess_media_type(image_bytes: bytes, media_type: str | None) -> str:
    normalized = (media_type or "").split(";")[0].strip().lower()
    if normalized in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        return "image/jpeg" if normalized == "image/jpg" else normalized
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _ocr_base_url() -> str:
    base_url = (settings.OCR_IMAGE_TRANSLATE_BASE_URL or "").strip()
    if not base_url:
        raise RuntimeError("OCR image translation service is not configured")
    return base_url.rstrip("/")


def _ocr_timeout() -> httpx.Timeout:
    timeout = max(1.0, float(settings.OCR_IMAGE_TRANSLATE_TIMEOUT or 180.0))
    connect_timeout = min(15.0, timeout)
    return httpx.Timeout(timeout=timeout, connect=connect_timeout, read=timeout, write=timeout)


def _build_ocr_request_payload(
    image_bytes: bytes,
    media_type: str | None,
    source_language: str,
    target_language: str,
) -> dict[str, Any]:
    resolved_media_type = _guess_media_type(image_bytes, media_type)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    payload: dict[str, Any] = {
        "image": f"data:{resolved_media_type};base64,{encoded}",
        "target_lang": target_language,
        "mime_type": resolved_media_type,
        "render": {
            "inpaint_method": settings.OCR_IMAGE_TRANSLATE_INPAINT_METHOD,
            "render_style": settings.OCR_IMAGE_TRANSLATE_RENDER_STYLE,
            "include_segments": settings.OCR_IMAGE_TRANSLATE_INCLUDE_SEGMENTS,
        },
    }
    requested_source = (source_language or "auto").strip() or "auto"
    if requested_source.lower() != "auto":
        payload["source_lang"] = requested_source
    text_fill = (settings.OCR_IMAGE_TRANSLATE_TEXT_FILL or "").strip()
    if text_fill:
        payload["render"]["text_fill"] = text_fill
    return payload


def _joined_segment_text(segments: Any, field_name: str) -> str:
    if not isinstance(segments, list):
        return ""
    values: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        value = str(segment.get(field_name) or "").strip()
        if value:
            values.append(value)
    return "\n".join(values)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _upstream_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = response.text[:300]
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message") or body.get("error")
        if detail:
            return str(detail)
    return str(body)[:300] or f"HTTP {response.status_code}"


async def _translate_image_with_ocr_backend(
    image_bytes: bytes,
    media_type: str | None,
    source_language: str,
    target_language: str,
) -> dict[str, Any]:
    payload = _build_ocr_request_payload(
        image_bytes=image_bytes,
        media_type=media_type,
        source_language=source_language,
        target_language=target_language,
    )
    url = f"{_ocr_base_url()}/v1/translate-image"
    try:
        async with httpx.AsyncClient(timeout=_ocr_timeout()) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        raise RuntimeError("OCR image translation upstream timeout") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OCR image translation upstream unavailable: {exc}") from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"OCR image translation upstream failed: {_upstream_error_detail(response)}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("OCR image translation upstream returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise RuntimeError("OCR image translation upstream returned invalid payload")
    return body


async def _translate_image_to_targets_with_ocr(
    image_bytes: bytes,
    media_type: str | None,
    source_language: str,
    target_language: str,
    target_language2: str,
    target_language3: str,
) -> dict[str, Any]:
    requested_source = (source_language or "auto").strip() or "auto"
    targets = [target_language, target_language2, target_language3]
    output_texts: list[str] = []
    output_images: list[str] = []
    output_mime_types: list[str] = []
    first_text = ""
    first_segments: Any = None
    first_detected_count = 0
    first_translated_count = 0

    for target in targets:
        normalized_target = (target or "").strip()
        if not normalized_target or normalized_target.lower() == "none":
            output_texts.append("")
            output_images.append("")
            output_mime_types.append("")
            continue

        body = await _translate_image_with_ocr_backend(
            image_bytes=image_bytes,
            media_type=media_type,
            source_language=requested_source,
            target_language=normalized_target,
        )
        segments = body.get("segments")
        translated_text = _joined_segment_text(segments, "translated_text")
        source_text = _joined_segment_text(segments, "source_text")
        output_texts.append(translated_text)
        output_images.append(str(body.get("image_processed") or ""))
        output_mime_types.append(str(body.get("mime_type") or "image/png"))

        if first_segments is None:
            first_segments = segments if isinstance(segments, list) else []
            first_text = source_text
            first_detected_count = _safe_int(body.get("detected_count"))
            first_translated_count = _safe_int(body.get("translated_count"))

    while len(output_texts) < 3:
        output_texts.append("")
    while len(output_images) < 3:
        output_images.append("")
    while len(output_mime_types) < 3:
        output_mime_types.append("")

    return {
        "text": first_text,
        "translatedText": output_texts[0],
        "translatedText2": output_texts[1],
        "translatedText3": output_texts[2],
        "detectedSourceLanguage": requested_source,
        "translatedImage": output_images[0],
        "translatedImage2": output_images[1],
        "translatedImage3": output_images[2],
        "translatedImageMimeType": output_mime_types[0] or "image/png",
        "translatedImageMimeType2": output_mime_types[1],
        "translatedImageMimeType3": output_mime_types[2],
        "ocrEngine": "paddleocr-vl",
        "detectedCount": first_detected_count,
        "translatedCount": first_translated_count,
        "segments": first_segments or [],
    }


async def translate_image_to_targets_async(
    image_bytes: bytes,
    media_type: str | None,
    source_language: str,
    target_language: str,
    target_language2: str = "none",
    target_language3: str = "none",
) -> dict[str, Any]:
    if not image_bytes:
        return {
            "text": "",
            "translatedText": "",
            "translatedText2": "",
            "translatedText3": "",
            "detectedSourceLanguage": source_language or "auto",
            "translatedImage": "",
            "translatedImage2": "",
            "translatedImage3": "",
            "translatedImageMimeType": "",
            "translatedImageMimeType2": "",
            "translatedImageMimeType3": "",
            "ocrEngine": "none",
            "detectedCount": 0,
            "translatedCount": 0,
            "segments": [],
        }

    requested_source = (source_language or "auto").strip() or "auto"
    if settings.OCR_IMAGE_TRANSLATE_ENABLED:
        try:
            return await _translate_image_to_targets_with_ocr(
                image_bytes=image_bytes,
                media_type=media_type,
                source_language=requested_source,
                target_language=target_language,
                target_language2=target_language2,
                target_language3=target_language3,
            )
        except RuntimeError:
            if not settings.OCR_IMAGE_TRANSLATE_FALLBACK_TO_GEMMA:
                raise
            logger.warning("[IMAGE_TRANSLATE] OCR backend failed; falling back to Gemma", exc_info=True)

    targets = [target_language, target_language2, target_language3]
    outputs: list[str] = []

    for target in targets:
        normalized_target = (target or "").strip()
        if not normalized_target or normalized_target.lower() == "none":
            outputs.append("")
            continue

        translated = await async_gemma_image_translate(
            image_bytes=image_bytes,
            media_type=media_type,
            source_lang=requested_source,
            target_lang=normalized_target,
        )
        if translated is None:
            logger.warning(
                "[IMAGE_TRANSLATE] direct Gemma image translation failed. source=%s target=%s",
                requested_source,
                normalized_target,
            )
            raise RuntimeError(f"Gemma image translation failed for target '{normalized_target}'")

        outputs.append(translated.strip())

    while len(outputs) < 3:
        outputs.append("")

    return {
        "text": "",
        "translatedText": outputs[0],
        "translatedText2": outputs[1],
        "translatedText3": outputs[2],
        "detectedSourceLanguage": requested_source,
        "translatedImage": "",
        "translatedImage2": "",
        "translatedImage3": "",
        "translatedImageMimeType": "",
        "translatedImageMimeType2": "",
        "translatedImageMimeType3": "",
        "ocrEngine": "gemma",
        "detectedCount": 0,
        "translatedCount": 0,
        "segments": [],
    }
