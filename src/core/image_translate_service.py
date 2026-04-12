from core.gemma_translate_service import async_gemma_image_translate
from core.logging_config import logger


async def translate_image_to_targets_async(
    image_bytes: bytes,
    media_type: str | None,
    source_language: str,
    target_language: str,
    target_language2: str = "none",
    target_language3: str = "none",
) -> dict[str, str]:
    if not image_bytes:
        return {
            "text": "",
            "translatedText": "",
            "translatedText2": "",
            "translatedText3": "",
            "detectedSourceLanguage": source_language or "auto",
        }

    requested_source = (source_language or "auto").strip() or "auto"
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
    }
