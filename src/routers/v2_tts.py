from __future__ import annotations

import json
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.config import settings
from core.logging_config import logger
from core.rate_limiter import enforce_user_rate_limit, set_user_context_cost
from db.models import User, V2User

router = APIRouter()

TTS_RESOURCE_COST = 1
CLONE_RESOURCE_COST = 2
SUPPORTED_RESPONSE_FORMATS = {"wav", "pcm", "mp3"}


class V2TTSRequest(BaseModel):
    input: str = Field(..., min_length=1)
    voice: str = "alloy"
    response_format: str = "wav"
    speed: float = 1.0
    chunk_size: Optional[int] = 8


def _tts_base_url() -> str:
    base_url = (settings.QWEN_TTS_BASE_URL or "").strip()
    if not base_url:
        raise HTTPException(status_code=500, detail="Qwen TTS service is not configured")
    return base_url.rstrip("/")


def _upstream_url(path: str) -> str:
    return f"{_tts_base_url()}{path}"


def _auth_headers() -> dict[str, str]:
    token = (settings.QWEN_TTS_TOKEN or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _timeout() -> httpx.Timeout:
    timeout = max(1.0, float(settings.QWEN_TTS_TIMEOUT or 300.0))
    connect_timeout = min(15.0, timeout)
    return httpx.Timeout(timeout=timeout, connect=connect_timeout, read=timeout, write=timeout)


def _normalize_response_format(response_format: str) -> str:
    fmt = (response_format or "wav").strip().lower()
    if fmt not in SUPPORTED_RESPONSE_FORMATS:
        raise HTTPException(status_code=400, detail="response_format must be one of wav, pcm, mp3")
    return fmt


def _validate_chunk_size(chunk_size: Optional[int]) -> Optional[int]:
    if chunk_size is None:
        return None
    normalized = int(chunk_size)
    if normalized < 1:
        raise HTTPException(status_code=400, detail="chunk_size must be >= 1")
    return normalized


def _upstream_error_detail(body: bytes) -> Any:
    if not body:
        return "TTS upstream request failed"
    text = body.decode("utf-8", "ignore")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text[:1000]


async def _open_upstream_stream(
    *,
    method: str,
    url: str,
    json_payload: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> tuple[httpx.AsyncClient, httpx.Response]:
    client = httpx.AsyncClient(timeout=_timeout())
    try:
        request = client.build_request(
            method,
            url,
            headers=_auth_headers(),
            json=json_payload,
            data=data,
            files=files,
        )
        response = await client.send(request, stream=True)
    except httpx.TimeoutException as exc:
        await client.aclose()
        logger.warning("[V2-TTS] upstream timeout url=%s error=%s", url, exc)
        raise HTTPException(status_code=504, detail="TTS upstream timeout") from exc
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("[V2-TTS] upstream unavailable url=%s error=%s", url, exc)
        raise HTTPException(status_code=503, detail="TTS upstream unavailable") from exc

    if response.status_code >= 400:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        logger.warning("[V2-TTS] upstream failed status=%s body=%s", response.status_code, body[:300])
        raise HTTPException(status_code=response.status_code, detail=_upstream_error_detail(body))

    return client, response


async def _iter_upstream_bytes(client: httpx.AsyncClient, response: httpx.Response):
    try:
        async for chunk in response.aiter_bytes():
            if chunk:
                yield chunk
    finally:
        await response.aclose()
        await client.aclose()


def _media_type(response: httpx.Response, fallback_format: str) -> str:
    content_type = response.headers.get("content-type", "")
    if content_type:
        return content_type.split(";")[0].strip()
    return {
        "wav": "audio/wav",
        "pcm": "audio/pcm",
        "mp3": "audio/mpeg",
    }[fallback_format]


@router.post("/tts")
async def v2_tts(
    request: Request,
    payload: V2TTSRequest,
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    text = payload.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="input text is empty")
    response_format = _normalize_response_format(payload.response_format)
    chunk_size = _validate_chunk_size(payload.chunk_size)

    set_user_context_cost(request, TTS_RESOURCE_COST)
    request.state.audit_payload = {
        "feature": "tts",
        "resource_cost": TTS_RESOURCE_COST,
        "response_format": response_format,
        "voice": payload.voice,
    }

    upstream_payload: dict[str, Any] = {
        "model": "tts-1",
        "input": text,
        "voice": payload.voice,
        "response_format": response_format,
        "speed": payload.speed,
    }
    if chunk_size is not None:
        upstream_payload["chunk_size"] = chunk_size

    client, response = await _open_upstream_stream(
        method="POST",
        url=_upstream_url("/v1/audio/speech"),
        json_payload=upstream_payload,
    )
    return StreamingResponse(
        _iter_upstream_bytes(client, response),
        media_type=_media_type(response, response_format),
        status_code=response.status_code,
    )


@router.post("/clone")
async def v2_clone(
    request: Request,
    input: str = Form(...),
    ref_audio: UploadFile = File(...),
    ref_text: str = Form(...),
    language: str = Form("Auto"),
    response_format: str = Form("wav"),
    chunk_size: int = Form(8),
    xvec_only: bool = Form(False),
    non_streaming_mode: Optional[bool] = Form(None),
    instruct: Optional[str] = Form(None),
    append_silence: bool = Form(True),
    current_user: User | V2User = Depends(enforce_user_rate_limit),
):
    del current_user
    text = (input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="input text is empty")
    if not (ref_text or "").strip() and not xvec_only:
        raise HTTPException(status_code=400, detail="ref_text is required unless xvec_only=true")

    response_format = _normalize_response_format(response_format)
    normalized_chunk_size = _validate_chunk_size(chunk_size) or 8

    set_user_context_cost(request, CLONE_RESOURCE_COST)
    request.state.audit_payload = {
        "feature": "tts_clone",
        "resource_cost": CLONE_RESOURCE_COST,
        "response_format": response_format,
        "language": language,
        "xvec_only": xvec_only,
    }

    data: dict[str, Any] = {
        "input": text,
        "ref_text": ref_text,
        "language": language,
        "response_format": response_format,
        "chunk_size": str(normalized_chunk_size),
        "xvec_only": "true" if xvec_only else "false",
        "append_silence": "true" if append_silence else "false",
    }
    if non_streaming_mode is not None:
        data["non_streaming_mode"] = "true" if non_streaming_mode else "false"
    if instruct:
        data["instruct"] = instruct

    files = {
        "ref_audio": (
            ref_audio.filename or "reference.wav",
            ref_audio.file,
            ref_audio.content_type or "application/octet-stream",
        )
    }
    client, response = await _open_upstream_stream(
        method="POST",
        url=_upstream_url("/v1/audio/voice-clone"),
        data=data,
        files=files,
    )
    return StreamingResponse(
        _iter_upstream_bytes(client, response),
        media_type=_media_type(response, response_format),
        status_code=response.status_code,
    )
