from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from codecs import BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from core.config import settings


class VoiceClonePresetServiceError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


_PRESET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_MANIFEST_FILE_NAME = "manifest.json"
_PACKAGE_FILE_NAME = "package.zip"
_MARKET_FILE_NAME = "market.json"


def list_market_presets(include_disabled: bool = False) -> list[dict[str, Any]]:
    root = _get_root()
    items: list[dict[str, Any]] = []
    for preset_dir in root.iterdir():
        if not preset_dir.is_dir():
            continue

        metadata_path = preset_dir / _MARKET_FILE_NAME
        package_path = preset_dir / _PACKAGE_FILE_NAME
        if not metadata_path.exists() or not package_path.exists():
            continue

        metadata = _read_json(metadata_path)
        if not include_disabled and not bool(metadata.get("enabled", True)):
            continue

        items.append(_build_market_item(metadata, package_path))

    return sorted(
        items,
        key=lambda item: ((item.get("name") or item.get("id") or "").lower(), item.get("id") or ""),
    )


def get_market_package_path(preset_id: str) -> Path:
    preset_id = _normalize_preset_id(preset_id)
    package_path = _preset_dir(preset_id) / _PACKAGE_FILE_NAME
    if not package_path.exists() or not package_path.is_file():
        raise VoiceClonePresetServiceError(404, "Voice clone preset package not found")
    return package_path


async def upsert_market_preset_from_upload(
    upload: UploadFile,
    metadata_overrides: dict[str, Any],
) -> dict[str, Any]:
    root = _get_root()
    temp_dir = Path(tempfile.mkdtemp(prefix="voice_clone_preset_upload_"))
    temp_package_path = temp_dir / _PACKAGE_FILE_NAME
    try:
        await _save_upload(upload, temp_package_path)
        manifest = _validate_package(temp_package_path)

        preset_id = _normalize_preset_id(metadata_overrides.get("id") or manifest.get("id"))
        name = _clean_text(metadata_overrides.get("name") or manifest.get("name") or preset_id)
        description = _clean_text(metadata_overrides.get("description"))
        version = _clean_text(metadata_overrides.get("version") or manifest.get("version"))

        languages = _clean_string_list(metadata_overrides.get("languages"))
        providers = _clean_string_list(metadata_overrides.get("providers"))
        inferred_languages, inferred_providers = _infer_languages_and_providers(manifest)
        if not languages:
            languages = inferred_languages
        if not providers:
            providers = inferred_providers

        enabled = bool(metadata_overrides.get("enabled", True))
        package_sha256 = _sha256_file(temp_package_path)
        package_size = temp_package_path.stat().st_size
        now = _utc_now_iso()

        destination = root / preset_id
        metadata_path = destination / _MARKET_FILE_NAME
        existing_metadata = _read_json(metadata_path) if metadata_path.exists() else {}
        destination.mkdir(parents=True, exist_ok=True)

        package_path = destination / _PACKAGE_FILE_NAME
        if package_path.exists():
            package_path.unlink()
        shutil.move(str(temp_package_path), package_path)

        metadata = {
            "id": preset_id,
            "name": name,
            "description": description,
            "version": version,
            "languages": languages,
            "providers": providers,
            "fileSize": package_size,
            "sha256": package_sha256,
            "createdAt": existing_metadata.get("createdAt") or now,
            "updatedAt": now,
            "enabled": enabled,
        }
        _write_json(metadata_path, metadata)
        return _build_market_item(metadata, package_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def delete_market_preset(preset_id: str) -> None:
    preset_id = _normalize_preset_id(preset_id)
    destination = _preset_dir(preset_id)
    if not destination.exists():
        raise VoiceClonePresetServiceError(404, "Voice clone preset not found")
    shutil.rmtree(destination)


def update_market_preset_metadata(preset_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    preset_id = _normalize_preset_id(preset_id)
    destination = _preset_dir(preset_id)
    metadata_path = destination / _MARKET_FILE_NAME
    package_path = destination / _PACKAGE_FILE_NAME
    if not metadata_path.exists() or not package_path.exists():
        raise VoiceClonePresetServiceError(404, "Voice clone preset not found")

    metadata = _read_json(metadata_path)
    metadata["id"] = preset_id
    if "name" in updates:
        metadata["name"] = _clean_text(updates.get("name")) or preset_id
    if "description" in updates:
        metadata["description"] = _clean_text(updates.get("description"))
    if "version" in updates:
        metadata["version"] = _clean_text(updates.get("version"))
    if "languages" in updates:
        metadata["languages"] = _clean_string_list(updates.get("languages"))
    if "providers" in updates:
        metadata["providers"] = _clean_string_list(updates.get("providers"))
    if "enabled" in updates:
        metadata["enabled"] = bool(updates.get("enabled"))

    metadata["fileSize"] = package_path.stat().st_size
    metadata["sha256"] = _sha256_file(package_path)
    metadata["createdAt"] = _clean_text(metadata.get("createdAt")) or _mtime_iso(package_path)
    metadata["updatedAt"] = _utc_now_iso()
    _write_json(metadata_path, metadata)
    return _build_market_item(metadata, package_path)


def _get_root() -> Path:
    root = Path(settings.VOICE_CLONE_PRESET_ROOT).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _preset_dir(preset_id: str) -> Path:
    root = _get_root()
    path = (root / preset_id).resolve()
    if root not in path.parents and path != root:
        raise VoiceClonePresetServiceError(400, "Invalid voice clone preset id")
    return path


def _normalize_preset_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9_.-]+", "-", raw).strip("-_.")
    if not normalized or not _PRESET_ID_PATTERN.match(normalized):
        raise VoiceClonePresetServiceError(400, "Invalid voice clone preset id")
    return normalized


async def _save_upload(upload: UploadFile, destination: Path) -> None:
    max_bytes = max(1, int(settings.VOICE_CLONE_PRESET_MAX_UPLOAD_MB or 1)) * 1024 * 1024
    total = 0
    with destination.open("wb") as file:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise VoiceClonePresetServiceError(413, "Voice clone preset package is too large")
            file.write(chunk)

    if total == 0:
        raise VoiceClonePresetServiceError(400, "Uploaded voice clone preset package is empty")


def _validate_package(package_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(package_path) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(entries) > 512:
                raise VoiceClonePresetServiceError(400, "Voice clone preset package contains too many files")

            entry_name_map = {_normalize_zip_path(entry.filename): entry.filename for entry in entries}
            entry_names = set(entry_name_map)
            manifest_names = [name for name in entry_names if name == _MANIFEST_FILE_NAME or name.endswith(f"/{_MANIFEST_FILE_NAME}")]
            if not manifest_names:
                raise VoiceClonePresetServiceError(400, "Voice clone preset package missing manifest.json")
            if len(manifest_names) > 1:
                raise VoiceClonePresetServiceError(400, "Voice clone preset package contains multiple manifest.json files")

            manifest_name = manifest_names[0]
            manifest = _read_manifest_from_archive(archive, entry_name_map[manifest_name])
            if not isinstance(manifest, dict):
                raise VoiceClonePresetServiceError(400, "Voice clone preset manifest must be a JSON object")

            manifest_prefix = manifest_name.rsplit("/", 1)[0] + "/" if "/" in manifest_name else ""
            profiles = manifest.get("profiles") or []
            if not isinstance(profiles, list) or not profiles:
                raise VoiceClonePresetServiceError(400, "Voice clone preset manifest must contain profiles")

            enabled_profiles = [profile for profile in profiles if isinstance(profile, dict) and bool(profile.get("enabled", True))]
            if not enabled_profiles:
                raise VoiceClonePresetServiceError(400, "Voice clone preset must contain at least one enabled profile")

            for profile in enabled_profiles:
                audio_path = _normalize_zip_path(str(profile.get("referenceAudioPath") or ""))
                if not audio_path:
                    raise VoiceClonePresetServiceError(400, "Voice clone preset profile missing referenceAudioPath")
                if f"{manifest_prefix}{audio_path}" not in entry_names:
                    raise VoiceClonePresetServiceError(400, f"Voice clone preset package missing audio file: {audio_path}")
                if _is_explicit_qwen_profile(profile) and not _clean_text(profile.get("referenceText")):
                    raise VoiceClonePresetServiceError(400, "Qwen-TTS voice clone preset profile missing referenceText")

            return manifest
    except VoiceClonePresetServiceError:
        raise
    except zipfile.BadZipFile as exc:
        raise VoiceClonePresetServiceError(400, "Voice clone preset package must be a zip file") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceClonePresetServiceError(400, "Voice clone preset manifest is not valid JSON") from exc


def _read_manifest_from_archive(archive: zipfile.ZipFile, entry_name: str) -> Any:
    raw = archive.read(entry_name)
    last_error: Exception | None = None
    for encoding in _manifest_encoding_candidates(raw):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = exc
    raise VoiceClonePresetServiceError(400, "Voice clone preset manifest is not valid JSON") from last_error


def _manifest_encoding_candidates(raw: bytes) -> list[str]:
    if raw.startswith(BOM_UTF8):
        return ["utf-8-sig"]
    if raw.startswith(BOM_UTF16_LE) or raw.startswith(BOM_UTF16_BE):
        return ["utf-16"]

    if _looks_like_utf16(raw):
        return ["utf-16-le", "utf-16-be", "utf-8-sig"]

    return ["utf-8-sig"]


def _looks_like_utf16(raw: bytes) -> bool:
    sample = raw[:200]
    if len(sample) < 4:
        return False
    even_nulls = sample[0::2].count(0)
    odd_nulls = sample[1::2].count(0)
    threshold = max(2, len(sample) // 8)
    return even_nulls >= threshold or odd_nulls >= threshold


def _normalize_zip_path(value: str) -> str:
    path = (value or "").strip().replace("\\", "/")
    if not path:
        return ""
    if path.startswith("/") or re.match(r"^[a-zA-Z]:", path):
        raise VoiceClonePresetServiceError(400, f"Voice clone preset package contains unsafe path: {value}")

    parts = [part for part in path.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise VoiceClonePresetServiceError(400, f"Voice clone preset package contains unsafe path: {value}")
    return "/".join(parts)


def _is_explicit_qwen_profile(profile: dict[str, Any]) -> bool:
    provider = _clean_text(profile.get("provider")).lower()
    return provider in {"qwen", "qwen-tts"}


def _infer_languages_and_providers(manifest: dict[str, Any]) -> tuple[list[str], list[str]]:
    languages: list[str] = []
    providers: list[str] = []
    profiles = manifest.get("profiles") or []
    if not isinstance(profiles, list):
        return languages, providers

    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        provider = _clean_text(profile.get("provider")).lower()
        if provider and provider not in providers:
            providers.append(provider)

        profile_languages = profile.get("targetLanguages") or profile.get("languages") or []
        if isinstance(profile_languages, str):
            profile_languages = [profile_languages]
        if not isinstance(profile_languages, list):
            continue
        for language in profile_languages:
            normalized_language = _clean_text(language).lower()
            if normalized_language in ("", "*", "auto", "all"):
                normalized_language = "auto"
            if normalized_language not in languages:
                languages.append(normalized_language)

    return languages, providers


def _build_market_item(metadata: dict[str, Any], package_path: Path) -> dict[str, Any]:
    item = {
        "id": _clean_text(metadata.get("id") or package_path.parent.name),
        "name": _clean_text(metadata.get("name") or package_path.parent.name),
        "description": _clean_text(metadata.get("description")),
        "version": _clean_text(metadata.get("version")),
        "languages": _clean_string_list(metadata.get("languages")),
        "providers": _clean_string_list(metadata.get("providers")),
        "fileSize": int(metadata.get("fileSize") or package_path.stat().st_size),
        "sha256": _clean_text(metadata.get("sha256") or _sha256_file(package_path)),
        "updatedAt": _clean_text(metadata.get("updatedAt") or _mtime_iso(package_path)),
        "createdAt": _clean_text(metadata.get("createdAt")),
        "enabled": bool(metadata.get("enabled", True)),
    }
    return item


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = value.split(",")
    elif isinstance(value, list):
        raw_values = value
    else:
        return []

    values: list[str] = []
    for raw_value in raw_values:
        normalized = _clean_text(raw_value)
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def _sha256_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
