from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from flowboard.services import media as media_service
from flowboard.services.llm import secrets

API_BASE = "https://api.elevenlabs.io"
DEFAULT_TTS_MODEL = "eleven_v3"
DEFAULT_STS_MODEL = "eleven_multilingual_sts_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsError(RuntimeError):
    pass


def get_env_api_key() -> Optional[str]:
    value = os.environ.get("ELEVENLABS_API_KEY")
    return value if value else None

def get_api_key() -> Optional[str]:
    return get_env_api_key() or secrets.get_api_key("elevenlabs")


def _headers(api_key: str, *, accept: str = "application/json") -> dict[str, str]:
    return {
        "xi-api-key": api_key,
        "Accept": accept,
        "Content-Type": "application/json",
    }

def _auth_headers(api_key: str, *, accept: str = "application/json") -> dict[str, str]:
    return {
        "xi-api-key": api_key,
        "Accept": accept,
    }


def _raise_for_error(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        detail = resp.json()
    except Exception:  # noqa: BLE001
        detail = resp.text
    if isinstance(detail, dict):
        inner = detail.get("detail")
        if isinstance(inner, dict):
            message = inner.get("message") or inner.get("status")
        else:
            message = inner
        if not message:
            message = detail.get("message")
    else:
        message = detail
    raise ElevenLabsError(str(message or f"ElevenLabs HTTP {resp.status_code}"))


def _voice_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "voice_id": item.get("voice_id"),
        "name": item.get("name"),
        "category": item.get("category"),
        "description": item.get("description"),
        "preview_url": item.get("preview_url"),
        "labels": item.get("labels") or {},
    }


async def list_voices(*, page_size: int = 100) -> dict[str, Any]:
    api_key = get_api_key()
    if not api_key:
        raise ElevenLabsError("elevenlabs_api_key_missing")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{API_BASE}/v2/voices",
            params={"page_size": page_size},
            headers=_headers(api_key),
        )
    _raise_for_error(resp)
    data = resp.json()
    voices = data.get("voices") if isinstance(data, dict) else []
    if not isinstance(voices, list):
        voices = []
    return {
        "voices": [
            v for v in (_voice_summary(item) for item in voices if isinstance(item, dict))
            if isinstance(v.get("voice_id"), str) and isinstance(v.get("name"), str)
        ],
        "has_more": bool(data.get("has_more")) if isinstance(data, dict) else False,
        "next_page_token": data.get("next_page_token") if isinstance(data, dict) else None,
    }


async def text_to_speech(
    *,
    voice_id: str,
    text: str,
    model_id: str = DEFAULT_TTS_MODEL,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    voice_settings: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    api_key = get_api_key()
    if not api_key:
        raise ElevenLabsError("elevenlabs_api_key_missing")
    voice_id = voice_id.strip()
    text = text.strip()
    if not voice_id:
        raise ElevenLabsError("voice_id_required")
    if not text:
        raise ElevenLabsError("text_required")

    payload: dict[str, Any] = {
        "text": text,
        "model_id": model_id or DEFAULT_TTS_MODEL,
    }
    if voice_settings:
        payload["voice_settings"] = voice_settings

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{API_BASE}/v1/text-to-speech/{voice_id}",
            params={"output_format": output_format or DEFAULT_OUTPUT_FORMAT},
            headers=_headers(api_key, accept="audio/mpeg"),
            json=payload,
        )
    _raise_for_error(resp)
    audio = resp.content
    if not audio:
        raise ElevenLabsError("empty_audio_response")

    media_id = str(uuid.uuid4())
    ok = media_service.ingest_inline_bytes(
        media_id,
        audio,
        kind="audio",
        mime="audio/mpeg",
    )
    if not ok:
        raise ElevenLabsError("audio_ingest_failed")
    return {
        "media_id": media_id,
        "mime": "audio/mpeg",
        "size": len(audio),
        "voice_id": voice_id,
        "model_id": payload["model_id"],
    }

async def speech_to_speech_file(
    *,
    voice_id: str,
    audio_path: Path,
    model_id: str = DEFAULT_STS_MODEL,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    remove_background_noise: bool = True,
) -> bytes:
    api_key = get_api_key()
    if not api_key:
        raise ElevenLabsError("elevenlabs_api_key_missing")
    voice_id = voice_id.strip()
    if not voice_id:
        raise ElevenLabsError("voice_id_required")
    if not audio_path.is_file():
        raise ElevenLabsError("audio_file_missing")

    data = {
        "model_id": model_id or DEFAULT_STS_MODEL,
        "remove_background_noise": "true" if remove_background_noise else "false",
    }
    params = {"output_format": output_format or DEFAULT_OUTPUT_FORMAT}
    with audio_path.open("rb") as audio_file:
        files = {"audio": (audio_path.name, audio_file, "audio/wav")}
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{API_BASE}/v1/speech-to-speech/{voice_id}",
                params=params,
                headers=_auth_headers(api_key, accept="audio/mpeg"),
                data=data,
                files=files,
            )
    _raise_for_error(resp)
    if not resp.content:
        raise ElevenLabsError("empty_audio_response")
    return resp.content
