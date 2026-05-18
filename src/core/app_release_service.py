from __future__ import annotations

import asyncio
import json
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from core.config import settings
from core.logging_config import logger


SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
SPARKLE_VERSION = f"{{{SPARKLE_NS}}}version"
SPARKLE_SHORT_VERSION = f"{{{SPARKLE_NS}}}shortVersionString"
SPARKLE_OS = f"{{{SPARKLE_NS}}}os"

_release_cache: dict[str, Any] = {
    "expires_at": 0.0,
    "data": None,
}
_release_cache_lock = asyncio.Lock()


async def get_latest_windows_app_release(force_refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    cached = _release_cache.get("data")
    if cached and not force_refresh and now < float(_release_cache.get("expires_at") or 0.0):
        return dict(cached)

    async with _release_cache_lock:
        now = time.time()
        cached = _release_cache.get("data")
        if cached and not force_refresh and now < float(_release_cache.get("expires_at") or 0.0):
            return dict(cached)

        release = await _resolve_latest_windows_app_release()
        _release_cache["data"] = release
        _release_cache["expires_at"] = now + max(1, int(settings.UPDATE_APP_RELEASE_CACHE_SECONDS or 1))
        return dict(release)


async def _resolve_latest_windows_app_release() -> dict[str, Any]:
    errors: list[str] = []
    loaders = (
        ("upload manifest", settings.UPDATE_APP_RELEASE_MANIFEST_URL, _load_from_upload_manifest),
        ("appcast", settings.UPDATE_APP_RELEASE_APPCAST_URL, _load_from_appcast),
    )

    for label, url, loader in loaders:
        release_url = str(url or "").strip()
        if not release_url:
            continue
        try:
            return await loader(release_url)
        except Exception as exc:  # pylint: disable=broad-except
            errors.append(f"{label}: {exc}")
            logger.warning("Failed to resolve public app release from %s %s: %s", label, release_url, exc)

    fallback_url = str(settings.UPDATE_APP_RELEASE_FALLBACK_URL or "").strip()
    if fallback_url:
        return {
            "product": "VRCLS",
            "version": "",
            "displayVersion": "",
            "channel": "",
            "runtime": "win-x64",
            "downloadUrl": fallback_url,
            "fileName": _file_name_from_url(fallback_url),
            "fileSize": 0,
            "sha256": "",
            "publishedAt": "",
            "source": {
                "type": "fallback",
                "url": fallback_url,
                "errors": errors,
            },
        }

    raise RuntimeError("; ".join(errors) or "no public app release source configured")


async def _load_from_upload_manifest(manifest_url: str) -> dict[str, Any]:
    text = await _fetch_text(manifest_url, "application/json")
    payload = json.loads(_strip_bom(text))
    if not isinstance(payload, dict):
        raise ValueError("upload manifest root is not an object")

    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    installer = _find_installer_artifact(artifacts)
    if not installer:
        raise ValueError("installer artifact not found")

    download_url = _artifact_download_url(payload, installer, manifest_url)
    if not download_url:
        raise ValueError("installer download url not found")

    return {
        "product": str(payload.get("product") or "VRCLS"),
        "version": str(payload.get("internalVersion") or installer.get("version") or ""),
        "displayVersion": str(payload.get("displayVersion") or installer.get("displayVersion") or ""),
        "channel": str(payload.get("channel") or ""),
        "runtime": str(payload.get("runtime") or ""),
        "downloadUrl": download_url,
        "fileName": _file_name_from_url(download_url),
        "fileSize": _to_int(installer.get("sizeBytes") or installer.get("length") or 0),
        "sha256": str(installer.get("sha256") or ""),
        "publishedAt": str(payload.get("generatedUtc") or ""),
        "source": {
            "type": "upload-manifest",
            "url": manifest_url,
        },
    }


async def _load_from_appcast(appcast_url: str) -> dict[str, Any]:
    text = await _fetch_text(appcast_url, "application/xml,text/xml")
    root = ET.fromstring(_strip_bom(text))
    item = root.find("./channel/item")
    if item is None:
        raise ValueError("appcast item not found")

    enclosure = item.find("enclosure")
    if enclosure is None:
        raise ValueError("appcast enclosure not found")

    download_url = str(enclosure.get("url") or "").strip()
    if not download_url:
        raise ValueError("appcast enclosure url not found")

    return {
        "product": "VRCLS",
        "version": str(enclosure.get(SPARKLE_VERSION) or item.findtext(f"{{{SPARKLE_NS}}}version") or ""),
        "displayVersion": str(
            enclosure.get(SPARKLE_SHORT_VERSION)
            or item.findtext(f"{{{SPARKLE_NS}}}shortVersionString")
            or item.findtext("title")
            or ""
        ),
        "channel": "",
        "runtime": str(enclosure.get(SPARKLE_OS) or ""),
        "downloadUrl": download_url,
        "fileName": _file_name_from_url(download_url),
        "fileSize": _to_int(enclosure.get("length") or 0),
        "sha256": "",
        "publishedAt": str(item.findtext("pubDate") or ""),
        "source": {
            "type": "appcast",
            "url": appcast_url,
        },
    }


async def _fetch_text(url: str, accept: str) -> str:
    timeout = max(1.0, float(settings.UPDATE_APP_RELEASE_FETCH_TIMEOUT or 1.0))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"Accept": accept})
        response.raise_for_status()
        return response.content.decode("utf-8-sig", errors="replace")


def _find_installer_artifact(artifacts: list[Any]) -> dict[str, Any] | None:
    dict_artifacts = [item for item in artifacts if isinstance(item, dict)]

    for artifact in dict_artifacts:
        if str(artifact.get("kind") or "").strip().lower() == "installer":
            return artifact

    for artifact in dict_artifacts:
        path = str(artifact.get("relativePath") or artifact.get("remoteKey") or "").strip().lower()
        if path.endswith(".exe"):
            return artifact

    return None


def _artifact_download_url(payload: dict[str, Any], artifact: dict[str, Any], manifest_url: str) -> str:
    for key in ("url", "downloadUrl"):
        direct = str(artifact.get(key) or "").strip()
        if _is_absolute_url(direct):
            return direct

    relative_path = str(artifact.get("relativePath") or artifact.get("fileName") or "").strip()
    appcast_base_url = str(payload.get("appCastBaseUrl") or payload.get("baseUrl") or "").strip()
    if appcast_base_url and relative_path:
        return urljoin(_ensure_trailing_slash(appcast_base_url), relative_path.lstrip("/"))

    remote_key = str(artifact.get("remoteKey") or "").strip()
    if remote_key:
        return urljoin(_origin_from_url(appcast_base_url or manifest_url), remote_key.lstrip("/"))

    if relative_path:
        return urljoin(_directory_url(manifest_url), relative_path.lstrip("/"))

    return ""


def _strip_bom(text: str) -> str:
    value = str(text or "").lstrip("\ufeff")
    if value.startswith("\u8302\u7984\u9a74"):
        value = value[3:]
    return value


def _is_absolute_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _ensure_trailing_slash(value: str) -> str:
    return value if value.endswith("/") else f"{value}/"


def _directory_url(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path.rsplit("/", 1)[0]
    return f"{parsed.scheme}://{parsed.netloc}{path}/"


def _origin_from_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/"


def _file_name_from_url(value: str) -> str:
    return urlparse(value).path.rsplit("/", 1)[-1]


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
