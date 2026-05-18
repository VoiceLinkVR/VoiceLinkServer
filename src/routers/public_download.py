from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from core.app_release_service import get_latest_windows_app_release
from core.logging_config import logger


router = APIRouter()


@router.get("/releases/vrcls/windows/latest")
async def latest_windows_release_download(refresh: bool = Query(default=False, include_in_schema=False)):
    try:
        release = await get_latest_windows_app_release(force_refresh=refresh)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to resolve latest Windows app release")
        raise HTTPException(status_code=503, detail="Latest Windows release is unavailable") from exc

    download_url = str(release.get("downloadUrl") or "").strip()
    if not download_url:
        raise HTTPException(status_code=503, detail="Latest Windows release download URL is unavailable")

    return RedirectResponse(url=download_url, status_code=302, headers={"Cache-Control": "no-store"})


@router.get("/releases/vrcls/windows/latest.json")
async def latest_windows_release_metadata(refresh: bool = Query(default=False, include_in_schema=False)):
    try:
        release = await get_latest_windows_app_release(force_refresh=refresh)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to resolve latest Windows app release metadata")
        raise HTTPException(status_code=503, detail="Latest Windows release is unavailable") from exc

    return {
        "product": release.get("product") or "VRCLS",
        "version": release.get("version") or "",
        "displayVersion": release.get("displayVersion") or "",
        "channel": release.get("channel") or "",
        "runtime": release.get("runtime") or "",
        "downloadUrl": release.get("downloadUrl") or "",
        "fileName": release.get("fileName") or "",
        "fileSize": int(release.get("fileSize") or 0),
        "sha256": release.get("sha256") or "",
        "publishedAt": release.get("publishedAt") or "",
        "source": release.get("source") or {},
    }
