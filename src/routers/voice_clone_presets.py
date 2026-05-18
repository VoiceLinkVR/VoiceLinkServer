from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core import voice_clone_preset_service
from core.v2_dependencies import get_current_admin_v2_user, get_current_v2_user
from db.models import V2User
from schemas.voice_clone_preset import (
    VoiceClonePresetMarketItem,
    VoiceClonePresetMarketListResponse,
    VoiceClonePresetMarketUpdateRequest,
)


router = APIRouter()
admin_router = APIRouter()


@router.get("/voice-clone-presets/market", response_model=VoiceClonePresetMarketListResponse)
async def list_voice_clone_preset_market(_: V2User = Depends(get_current_v2_user)):
    return VoiceClonePresetMarketListResponse(
        items=[
            VoiceClonePresetMarketItem(**item)
            for item in voice_clone_preset_service.list_market_presets(include_disabled=False)
        ]
    )


@router.get("/voice-clone-presets/market/{preset_id}/download")
async def download_voice_clone_preset_market_package(
    preset_id: str,
    _: V2User = Depends(get_current_v2_user),
):
    try:
        package_path = voice_clone_preset_service.get_market_package_path(preset_id)
    except voice_clone_preset_service.VoiceClonePresetServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return FileResponse(
        package_path,
        media_type="application/zip",
        filename=f"{preset_id}.zip",
        headers={"Cache-Control": "no-store"},
    )


@admin_router.get("/voice-clone-presets", response_model=VoiceClonePresetMarketListResponse)
async def admin_list_voice_clone_presets(_: V2User = Depends(get_current_admin_v2_user)):
    return VoiceClonePresetMarketListResponse(
        items=[
            VoiceClonePresetMarketItem(**item)
            for item in voice_clone_preset_service.list_market_presets(include_disabled=True)
        ]
    )


@admin_router.post("/voice-clone-presets", response_model=VoiceClonePresetMarketItem)
async def admin_upload_voice_clone_preset(
    package: UploadFile = File(...),
    id: str | None = Form(None),
    name: str | None = Form(None),
    description: str | None = Form(None),
    version: str | None = Form(None),
    languages: str | None = Form(None),
    providers: str | None = Form(None),
    enabled: bool = Form(True),
    _: V2User = Depends(get_current_admin_v2_user),
):
    try:
        item = await voice_clone_preset_service.upsert_market_preset_from_upload(
            package,
            {
                "id": id,
                "name": name,
                "description": description,
                "version": version,
                "languages": languages,
                "providers": providers,
                "enabled": enabled,
            },
        )
        return VoiceClonePresetMarketItem(**item)
    except voice_clone_preset_service.VoiceClonePresetServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@admin_router.patch("/voice-clone-presets/{preset_id}", response_model=VoiceClonePresetMarketItem)
async def admin_update_voice_clone_preset(
    preset_id: str,
    payload: VoiceClonePresetMarketUpdateRequest,
    _: V2User = Depends(get_current_admin_v2_user),
):
    try:
        item = voice_clone_preset_service.update_market_preset_metadata(
            preset_id,
            payload.model_dump(exclude_unset=True),
        )
        return VoiceClonePresetMarketItem(**item)
    except voice_clone_preset_service.VoiceClonePresetServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@admin_router.delete("/voice-clone-presets/{preset_id}")
async def admin_delete_voice_clone_preset(
    preset_id: str,
    _: V2User = Depends(get_current_admin_v2_user),
):
    try:
        voice_clone_preset_service.delete_market_preset(preset_id)
    except voice_clone_preset_service.VoiceClonePresetServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True}
