import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from fastapi import Request

from core.config import settings
from core.logging_config import logger

DEFAULT_UPDATE_MANIFEST = {
    "latestVersion": "1.0.0",
    "releaseNotes": "Initial packaged release.",
    "platforms": {
        "windows": {
            "downloadUrl": "releases/VRCLS-1.0.0-win-x64.zip",
            "fileSize": 0,
            "checksum": "",
            "releaseNotes": "Initial packaged release."
        }
    }
}

DEFAULT_MODEL_MANIFEST = {
    "models": []
}

DEFAULT_TRANSLATION_PROFILE_MANIFEST = {
    "version": "",
    "downloadUrl": "",
    "checksum": "",
    "profile": {
        "translationCapabilities": {
            "version": "",
            "engines": []
        }
    }
}

DEFAULT_TRANSLATOR_RUNTIME_MANIFEST = {
    "version": "",
    "downloadUrl": "",
    "checksum": "",
    "fileSize": 0,
    "notes": ""
}

_lang_executor = ThreadPoolExecutor(max_workers=4)

_capabilities_cache: Dict[str, Any] = {
    "timestamp": 0.0,
    "data": None,
}


def ensure_update_assets() -> str:
    """Create update manifest files and static directories when missing."""
    static_root = settings.UPDATE_STATIC_ROOT
    os.makedirs(static_root, exist_ok=True)

    for folder in ("releases", "models", "translation-profiles", "translator-runtime"):
        os.makedirs(os.path.join(static_root, folder), exist_ok=True)

    _ensure_json_file(settings.UPDATE_MANIFEST_PATH, DEFAULT_UPDATE_MANIFEST)
    _ensure_json_file(settings.MODEL_MANIFEST_PATH, DEFAULT_MODEL_MANIFEST)
    _ensure_json_file(settings.TRANSLATION_PROFILE_MANIFEST_PATH, DEFAULT_TRANSLATION_PROFILE_MANIFEST)
    _ensure_json_file(settings.TRANSLATOR_RUNTIME_MANIFEST_PATH, DEFAULT_TRANSLATOR_RUNTIME_MANIFEST)

    return static_root


def get_app_update_info(request: Request, current_version: str, platform: str) -> Dict[str, Any]:
    data = _load_json(settings.UPDATE_MANIFEST_PATH, DEFAULT_UPDATE_MANIFEST)
    platform_key = (platform or "windows").strip().lower()

    latest_version = str(data.get("latestVersion", "")).strip()
    platforms = data.get("platforms") if isinstance(data.get("platforms"), dict) else {}
    platform_payload = platforms.get(platform_key) if isinstance(platforms.get(platform_key), dict) else {}

    if not platform_payload and platform_key != "windows":
        fallback = platforms.get("windows")
        if isinstance(fallback, dict):
            platform_payload = fallback

    download_url = _resolve_url(request, platform_payload.get("downloadUrl") or data.get("downloadUrl") or "")
    release_notes = str(platform_payload.get("releaseNotes") or data.get("releaseNotes") or "")
    file_size = _to_int(platform_payload.get("fileSize") or data.get("fileSize") or 0)
    checksum = str(platform_payload.get("checksum") or data.get("checksum") or "")

    has_update = bool(download_url and latest_version and _is_version_newer(current_version, latest_version))

    return {
        "hasUpdate": has_update,
        "latestVersion": latest_version,
        "downloadUrl": download_url,
        "releaseNotes": release_notes,
        "fileSize": file_size,
        "checksum": checksum,
    }


def get_model_catalog(request: Request) -> Dict[str, List[Dict[str, Any]]]:
    data = _load_json(settings.MODEL_MANIFEST_PATH, DEFAULT_MODEL_MANIFEST)
    models = data.get("models") if isinstance(data.get("models"), list) else []

    normalized: List[Dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict):
            continue

        normalized.append(
            {
                "name": str(model.get("name") or "").strip(),
                "type": str(model.get("type") or "").strip().lower(),
                "version": str(model.get("version") or "").strip(),
                "downloadUrl": _resolve_url(request, model.get("downloadUrl") or ""),
                "fileSize": _to_int(model.get("fileSize") or 0),
                "checksum": str(model.get("checksum") or ""),
                "installPath": str(model.get("installPath") or ""),
                "archiveRoot": str(model.get("archiveRoot") or ""),
                "required": bool(model.get("required", False)),
            }
        )

    return {"models": normalized}


def get_translation_profile_info(request: Request, current_version: Optional[str]) -> Dict[str, Any]:
    data = _load_json(settings.TRANSLATION_PROFILE_MANIFEST_PATH, DEFAULT_TRANSLATION_PROFILE_MANIFEST)

    latest_version = str(data.get("version") or "").strip()
    current = (current_version or "").strip()

    profile_payload = data.get("profile") if isinstance(data.get("profile"), dict) else None
    download_url = _resolve_url(request, data.get("downloadUrl") or "")

    has_update = False
    if latest_version:
        has_update = _is_version_newer(current, latest_version) if current else True

    if has_update and not (profile_payload or download_url):
        has_update = False

    return {
        "hasUpdate": has_update,
        "version": latest_version,
        "downloadUrl": download_url,
        "checksum": str(data.get("checksum") or ""),
        "profile": profile_payload,
    }


def get_translator_runtime_info(request: Request, current_version: Optional[str]) -> Dict[str, Any]:
    data = _load_json(settings.TRANSLATOR_RUNTIME_MANIFEST_PATH, DEFAULT_TRANSLATOR_RUNTIME_MANIFEST)

    latest_version = str(data.get("version") or "").strip()
    current = (current_version or "").strip()

    download_url = _resolve_url(request, data.get("downloadUrl") or "")
    file_size = _to_int(data.get("fileSize") or 0)
    checksum = str(data.get("checksum") or "")
    notes = str(data.get("notes") or "")

    has_update = False
    if latest_version and download_url:
        has_update = _is_version_newer(current, latest_version) if current else True

    return {
        "hasUpdate": has_update,
        "version": latest_version,
        "downloadUrl": download_url,
        "fileSize": file_size,
        "checksum": checksum,
        "notes": notes,
    }


def get_translation_capabilities() -> Dict[str, Any]:
    now = time.time()
    cached = _capabilities_cache.get("data")
    cached_ts = _capabilities_cache.get("timestamp", 0.0)

    if cached and now - cached_ts < settings.TRANSLATION_CAPABILITIES_CACHE_SECONDS:
        return cached

    payload = {
        "version": time.strftime("%Y%m%d"),
        "engines": _load_translators_capabilities(),
    }

    _capabilities_cache["timestamp"] = now
    _capabilities_cache["data"] = payload
    return payload


def _load_translators_capabilities() -> List[Dict[str, Any]]:
    try:
        import translators as ts
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to import translators library: %s", exc)
        return []

    engines = []
    configured = [s.strip() for s in (settings.TRANSLATOR_SERVICES_LIST or "").split(",") if s.strip()]
    pool = getattr(ts, "translators_pool", [])
    pool_map = {name.lower(): name for name in pool}
    candidates = [pool_map[item.lower()] for item in configured if item.lower() in pool_map] or list(pool)
    for name in candidates:
        lang_map = _safe_get_language_map(ts, name)
        if not lang_map:
            continue
        engines.append(
            {
                "engine": name,
                "languages": lang_map,
            }
        )

    return engines


def _safe_get_language_map(ts_module, engine: str) -> Dict[str, Any]:
    try:
        future = _lang_executor.submit(ts_module.get_languages, engine)
        return future.result(timeout=settings.TRANSLATION_CAPABILITY_TIMEOUT) or {}
    except FutureTimeoutError:
        logger.warning("Timeout fetching languages for %s", engine)
        return {}
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to fetch languages for %s: %s", engine, exc)
        return {}


def _ensure_json_file(path: str, default_payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(default_payload, f, ensure_ascii=False, indent=2)


def _load_json(path: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            return payload
    except FileNotFoundError:
        logger.warning("Update manifest not found: %s", path)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in manifest %s: %s", path, exc)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to load manifest %s: %s", path, exc)
    return dict(fallback)


def _resolve_url(request: Request, raw_url: Any) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""

    if value.startswith("http://") or value.startswith("https://"):
        return value

    # Relative paths default to /static/* so update manifests can stay concise.
    if not value.startswith("/") and not value.startswith("static/"):
        value = f"static/{value}"

    base_override = (settings.UPDATE_PUBLIC_BASE_URL or "").strip()
    if base_override:
        base = base_override if base_override.endswith("/") else base_override + "/"
        return urljoin(base, value.lstrip("/"))

    return urljoin(str(request.base_url), value.lstrip("/"))


def _is_version_newer(current: str, latest: str) -> bool:
    current_parts = _version_to_tuple(current)
    latest_parts = _version_to_tuple(latest)

    if not latest_parts:
        return False
    if not current_parts:
        return True

    length = max(len(current_parts), len(latest_parts))
    current_padded = current_parts + (0,) * (length - len(current_parts))
    latest_padded = latest_parts + (0,) * (length - len(latest_parts))
    return latest_padded > current_padded


def _version_to_tuple(version: str) -> tuple:
    parts: List[int] = []
    for token in str(version or "").replace("v", "").split("."):
        token = token.strip()
        if not token:
            continue
        numeric = "".join(ch for ch in token if ch.isdigit())
        if numeric:
            parts.append(int(numeric))
    return tuple(parts)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
