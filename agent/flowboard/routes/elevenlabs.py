from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flowboard.services import elevenlabs
from flowboard.services.llm import secrets

router = APIRouter(prefix="/api/elevenlabs", tags=["elevenlabs"])


class ElevenLabsKeyBody(BaseModel):
    apiKey: Optional[str] = None


class TextToSpeechBody(BaseModel):
    voice_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=5000)
    model_id: str = elevenlabs.DEFAULT_TTS_MODEL
    output_format: str = elevenlabs.DEFAULT_OUTPUT_FORMAT
    voice_settings: Optional[dict[str, Any]] = None


@router.get("/status")
def status() -> dict:
    env_key = elevenlabs.get_env_api_key()
    local_key = secrets.get_api_key("elevenlabs")
    return {
        "configured": bool(env_key or local_key),
        "source": "env" if env_key else ("local" if local_key else "missing"),
    }

@router.put("/key")
def set_key(body: ElevenLabsKeyBody) -> dict:
    secrets.set_api_key("elevenlabs", body.apiKey)
    return status() | {"ok": True}


@router.get("/voices")
async def list_voices() -> dict:
    try:
        return await elevenlabs.list_voices()
    except elevenlabs.ElevenLabsError as exc:
        status = 401 if str(exc) == "elevenlabs_api_key_missing" else 502
        raise HTTPException(status, str(exc)) from exc


@router.post("/text-to-speech")
async def text_to_speech(body: TextToSpeechBody) -> dict:
    try:
        return await elevenlabs.text_to_speech(
            voice_id=body.voice_id,
            text=body.text,
            model_id=body.model_id,
            output_format=body.output_format,
            voice_settings=body.voice_settings,
        )
    except elevenlabs.ElevenLabsError as exc:
        status = 401 if str(exc) == "elevenlabs_api_key_missing" else 502
        raise HTTPException(status, str(exc)) from exc
