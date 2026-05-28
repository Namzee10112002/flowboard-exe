from __future__ import annotations

import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from flowboard.config import STORAGE_DIR
from flowboard.db import get_session
from flowboard.db.models import Board, Node, Scenario, ScenarioScene
from flowboard.services import elevenlabs
from flowboard.services import ffmpeg as ffmpeg_service
from flowboard.services import media as media_service
from flowboard.services import scenario_planner

router = APIRouter(prefix="/api/boards", tags=["scenarios"])

EXPORTS_DIR = STORAGE_DIR / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_AUDIO_MODES = {
    "silent",
    "veo_dialogue",
    "veo_dialogue_elevenlabs_replace",
}

VOICE_CHANGER_AUDIO_MODE = "veo_dialogue_elevenlabs_replace"


def _dump_row(row):
    return row.model_dump(mode="json")


def _scenario_generation_summary(scenes: list[ScenarioScene]) -> dict:
    total = len(scenes)
    background_done = sum(1 for scene in scenes if scene.background_media_id)
    image_done = sum(1 for scene in scenes if scene.image_media_id)
    video_done = sum(1 for scene in scenes if scene.video_media_id)
    voice_done = sum(1 for scene in scenes if scene.voice_media_id)
    error_count = sum(1 for scene in scenes if scene.status == "error" or scene.error)

    if total == 0:
        stage = "empty"
    elif error_count > 0:
        stage = "error"
    elif background_done < total:
        stage = "needs_background"
    elif image_done < total:
        stage = "needs_scene_image"
    elif video_done < total:
        stage = "needs_video"
    else:
        stage = "video_done"

    return {
        "scene_count": total,
        "background_done": background_done,
        "image_done": image_done,
        "video_done": video_done,
        "voice_done": voice_done,
        "error_count": error_count,
        "stage": stage,
    }


def _dump_scenario(row: Scenario, scenes: list[ScenarioScene]) -> dict:
    data = _dump_row(row)
    data["generation_summary"] = _scenario_generation_summary(scenes)
    return data


async def _cached_media_path(media_id: str) -> Path | None:
    normalized = media_service.normalize_media_id(media_id)
    if not media_service.is_valid_media_id(normalized):
        return None
    cached = media_service.cached_path(normalized)
    if cached is not None:
        return cached
    fetched = await media_service.fetch_and_cache(normalized)
    if fetched is None:
        return None
    _bytes, _mime, path = fetched
    return path


def _ffmpeg_concat_list_line(path: Path) -> str:
    safe_path = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{safe_path}'"


def _ffmpeg_command(*args: str) -> list[str]:
    try:
        return ffmpeg_service.command(*args)
    except ffmpeg_service.FFmpegNotFoundError as exc:
        raise HTTPException(
            502,
            "ffmpeg not found; update Flowboard to the full package",
        ) from exc


def _run_ffmpeg_concat(input_paths: list[Path], output_path: Path) -> None:
    list_path = output_path.with_suffix(".txt")
    list_path.write_text(
        "\n".join(_ffmpeg_concat_list_line(path) for path in input_paths),
        encoding="utf-8",
    )
    try:
        copy_cmd = _ffmpeg_command(
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        )
        encode_cmd = _ffmpeg_command(
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(output_path),
        )
        copy_run = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=600)
        if copy_run.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return
        encode_run = subprocess.run(encode_cmd, capture_output=True, text=True, timeout=900)
    except (FileNotFoundError, ffmpeg_service.FFmpegNotFoundError) as exc:
        raise HTTPException(502, "ffmpeg not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(502, "ffmpeg export timed out") from exc

    if encode_run.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        message = (encode_run.stderr or copy_run.stderr or "ffmpeg export failed")[-1000:]
        raise HTTPException(502, message)


def _run_command(cmd: list[str], *, timeout: int, error_label: str) -> None:
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise HTTPException(502, f"{error_label} command not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(502, f"{error_label} timed out") from exc
    if run.returncode != 0:
        message = (run.stderr or run.stdout or f"{error_label} failed")[-1000:]
        raise HTTPException(502, message)


def _extract_audio(video_path: Path, output_path: Path) -> None:
    _run_command(
        _ffmpeg_command(
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            str(output_path),
        ),
        timeout=300,
        error_label="ffmpeg audio extract",
    )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(502, "ffmpeg audio extract produced no audio")


def _convert_audio_to_wav(input_path: Path, output_path: Path) -> None:
    _run_command(
        _ffmpeg_command(
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "2",
            "-ar",
            "44100",
            str(output_path),
        ),
        timeout=300,
        error_label="ffmpeg vocal normalize",
    )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(502, "ffmpeg vocal normalize produced no audio")


def _demucs_command() -> list[str]:
    configured = os.environ.get("FLOWBOARD_DEMUCS_CMD")
    if configured:
        return shlex.split(configured)
    exe_name = "demucs_soundfile.exe" if os.name == "nt" else "demucs_soundfile"
    if getattr(sys, "frozen", False):
        install_dir = Path(sys.executable).resolve().parent
    else:
        install_dir = Path(os.getenv("FLOWBOARD_INSTALL_DIR", Path.cwd())).resolve()
    bundle_root = getattr(sys, "_MEIPASS", None)
    candidates = []
    if bundle_root:
        candidates.extend([
            Path(bundle_root) / "tools" / exe_name,
            Path(bundle_root) / exe_name,
        ])
    candidates.extend([
        install_dir / "tools" / exe_name,
        install_dir / exe_name,
    ])
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]
    cli = shutil.which("demucs")
    if cli:
        return [cli]
    wrapper = Path(__file__).resolve().parents[1] / "tools" / "demucs_soundfile.py"
    if not getattr(sys, "frozen", False) and wrapper.is_file():
        py = shutil.which("python") or sys.executable
        return [py, str(wrapper)]
    raise HTTPException(
        502,
        "demucs vocal separation tool not found; update Flowboard to the full package",
    )


def _separate_vocals(audio_path: Path, work_dir: Path) -> tuple[Path, Path]:
    stems_dir = work_dir / "stems"
    cmd = [
        *_demucs_command(),
        "--two-stems",
        "vocals",
        "-o",
        str(stems_dir),
        str(audio_path),
    ]
    _run_command(cmd, timeout=1800, error_label="demucs vocal separation")
    vocals = next(stems_dir.rglob("vocals.wav"), None)
    background = next(stems_dir.rglob("no_vocals.wav"), None)
    if vocals is None or background is None:
        raise HTTPException(502, "demucs did not produce vocals/no_vocals stems")
    return vocals, background


def _mux_video_with_background_and_vocals(
    video_path: Path,
    background_path: Path,
    vocals_path: Path,
    output_path: Path,
) -> None:
    _run_command(
        _ffmpeg_command(
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(background_path),
            "-i",
            str(vocals_path),
            "-filter_complex",
            "[1:a][2:a]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ),
        timeout=600,
        error_label="ffmpeg audio mux",
    )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(502, "ffmpeg audio mux produced no video")


async def _voice_change_scene_video(
    video_path: Path,
    voice_id: str,
    work_dir: Path,
    scene_idx: int,
) -> Path:
    scene_dir = work_dir / f"scene_{scene_idx + 1:03d}"
    scene_dir.mkdir(parents=True, exist_ok=True)
    source_audio = scene_dir / "source_audio.wav"
    changed_vocals_raw = scene_dir / "changed_vocals.mp3"
    changed_vocals = scene_dir / "changed_vocals.wav"
    output_path = scene_dir / "voice_changed.mp4"

    _extract_audio(video_path, source_audio)
    vocals, background = _separate_vocals(source_audio, scene_dir)
    try:
        audio = await elevenlabs.speech_to_speech_file(
            voice_id=voice_id,
            audio_path=vocals,
        )
    except elevenlabs.ElevenLabsError as exc:
        status = 401 if str(exc) == "elevenlabs_api_key_missing" else 502
        raise HTTPException(status, str(exc)) from exc
    changed_vocals_raw.write_bytes(audio)
    _convert_audio_to_wav(changed_vocals_raw, changed_vocals)
    _mux_video_with_background_and_vocals(
        video_path,
        background,
        changed_vocals,
        output_path,
    )
    return output_path


def _export_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_").lower()
    return (slug or fallback)[:48]


def _unique_export_dir(base_name: str) -> Path:
    path = EXPORTS_DIR / base_name
    if not path.exists():
        path.mkdir(parents=True)
        return path
    for idx in range(2, 1000):
        candidate = EXPORTS_DIR / f"{base_name}_{idx}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
    raise HTTPException(500, "could not create export folder")


async def _export_scenario_to_dir(
    board_id: int,
    scenario_id: int,
    export_dir: Path,
    voice_id: str | None = None,
) -> dict:
    with get_session() as s:
        scenario = s.get(Scenario, scenario_id)
        if scenario is None or scenario.board_id != board_id:
            raise HTTPException(404, "scenario not found")
        scenes = list(
            s.exec(
                select(ScenarioScene)
                .where(ScenarioScene.scenario_id == scenario_id)
                .order_by(ScenarioScene.idx)
            ).all()
        )
        theme = scenario.theme
        video_audio_mode = scenario.video_audio_mode
        effective_voice_id = _normalize_voice_id(voice_id) or scenario.voice_id
        if video_audio_mode == VOICE_CHANGER_AUDIO_MODE and not effective_voice_id:
            raise HTTPException(400, "scenario voice_id is required for voice changer export")

    if not scenes:
        raise HTTPException(400, "scenario has no scenes")
    missing = [scene.idx + 1 for scene in scenes if not scene.video_media_id]
    if missing:
        raise HTTPException(400, f"missing scene videos: {missing}")

    media_id = uuid.uuid4().hex
    file_stem = f"scenario_{scenario_id}_{_slugify(theme, f'scenario_{scenario_id}')}"
    output_path = export_dir / f"{file_stem}.mp4"
    input_paths: list[Path] = []
    with tempfile.TemporaryDirectory(
        prefix="flowboard_voice_work_",
        ignore_cleanup_errors=True,
    ) as temp_dir:
        work_dir = Path(temp_dir)
        for scene in scenes:
            assert scene.video_media_id is not None
            path = await _cached_media_path(scene.video_media_id)
            if path is None:
                raise HTTPException(
                    404,
                    f"scene video media not available: {scene.video_media_id}",
                )
            if video_audio_mode == VOICE_CHANGER_AUDIO_MODE and effective_voice_id:
                path = await _voice_change_scene_video(
                    path,
                    effective_voice_id,
                    work_dir,
                    scene.idx,
                )
            input_paths.append(path)
        _run_ffmpeg_concat(input_paths, output_path)

    if not media_service.register_local_file(
        media_id,
        output_path,
        kind="video",
        mime="video/mp4",
    ):
        raise HTTPException(500, "failed to save exported video")

    with get_session() as s:
        scenario = s.get(Scenario, scenario_id)
        if scenario is None or scenario.board_id != board_id:
            raise HTTPException(404, "scenario not found")
        scenario.final_video_media_id = media_id
        scenario.status = "done"
        scenario.updated_at = datetime.now(timezone.utc)
        s.add(scenario)
        s.commit()
        s.refresh(scenario)
        saved_scenes = list(
            s.exec(
                select(ScenarioScene)
                .where(ScenarioScene.scenario_id == scenario_id)
                .order_by(ScenarioScene.idx)
            ).all()
        )
        return {
            "scenario_id": scenario_id,
            "media_id": media_id,
            "export_dir": str(export_dir),
            "file_path": str(output_path),
            "scenario": _dump_scenario(scenario, saved_scenes),
        }


def _normalize_video_audio_mode(value: str | None) -> str:
    mode = (value or "silent").strip()
    if mode not in VIDEO_AUDIO_MODES:
        raise HTTPException(400, "video_audio_mode is invalid")
    return mode


def _normalize_voice_id(value: str | None) -> str | None:
    voice_id = (value or "").strip()
    return voice_id or None


def _voice_id_for_mode(mode: str, value: str | None) -> str | None:
    voice_id = _normalize_voice_id(value)
    if mode == VOICE_CHANGER_AUDIO_MODE and not voice_id:
        raise HTTPException(400, "voice_id is required for Veo + ElevenLabs mode")
    return voice_id if mode == VOICE_CHANGER_AUDIO_MODE else None


class ScenarioRefsBody(BaseModel):
    background_media_ids: list[str] = Field(default_factory=list)
    character_media_ids: list[str] = Field(default_factory=list)
    visual_asset_media_ids: list[str] = Field(default_factory=list)


class ScenarioPlanBody(BaseModel):
    theme: str
    extra_description: str = ""
    scene_count: int = 1
    content_style: str = ""
    video_audio_mode: str = "silent"
    voice_id: Optional[str] = None
    refs: ScenarioRefsBody = Field(default_factory=ScenarioRefsBody)
    node_id: Optional[int] = None


class ScenarioPatchBody(BaseModel):
    video_audio_mode: Optional[str] = None
    voice_id: Optional[str] = None


class ScenarioScenePatchBody(BaseModel):
    title: Optional[str] = None
    background_description: Optional[str] = None
    background_image_prompt: Optional[str] = None
    composition_prompt: Optional[str] = None
    motion_prompt: Optional[str] = None
    voice_script: Optional[str] = None
    voice_direction: Optional[str] = None
    duration_seconds: Optional[float] = Field(default=None, ge=1.0, le=60.0)
    status: Optional[str] = None
    error: Optional[str] = None
    background_media_id: Optional[str] = None
    image_media_id: Optional[str] = None
    video_media_id: Optional[str] = None
    voice_media_id: Optional[str] = None


class ScenarioExportBody(BaseModel):
    voice_id: Optional[str] = None


class ScenarioExportSelectedBody(BaseModel):
    scenario_ids: list[int] = Field(default_factory=list)
    voice_id: Optional[str] = None


class ScenarioBatchScanBody(BaseModel):
    folder_path: str
    input_type: Literal["auto", "images", "videos"] = "auto"


class ScenarioBatchCreateItemBody(BaseModel):
    path: str
    kind: Literal["image", "video"]


class ScenarioBatchCreateBody(BaseModel):
    scene_count: int = Field(default=5, ge=1, le=12)
    voice_id: Optional[str] = None
    video_audio_mode: str = "silent"
    items: list[ScenarioBatchCreateItemBody] = Field(default_factory=list)


class ScenarioBatchAnalyzeImagesBody(BaseModel):
    scenario_ids: list[int] = Field(default_factory=list)


class ScenarioBatchAnalyzeVideosBody(BaseModel):
    scenario_ids: list[int] = Field(default_factory=list)


@router.post("/{board_id}/scenarios/batch-scan")
def batch_scan_folder(board_id: int, body: ScenarioBatchScanBody) -> dict:
    folder_path = (body.folder_path or "").strip()
    if not folder_path:
        raise HTTPException(400, "folder_path is required")

    root = Path(folder_path).expanduser()
    if not root.exists() or not root.is_dir():
        raise HTTPException(400, "folder_path must be an existing directory")

    input_type = body.input_type
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

    items: list[dict] = []
    counts = {"images": 0, "videos": 0, "rejected": 0}

    try:
        candidates = sorted([p for p in root.iterdir() if p.is_file()], key=lambda p: p.name.lower())
    except OSError as exc:
        raise HTTPException(400, f"failed to read folder: {exc}") from exc

    for path in candidates:
        ext = path.suffix.lower()
        if ext in image_exts:
            kind = "image"
        elif ext in video_exts:
            kind = "video"
        else:
            items.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "kind": "unsupported",
                    "accepted": False,
                    "reason": f"unsupported extension: {ext or '(none)'}",
                }
            )
            counts["rejected"] += 1
            continue

        if input_type == "images" and kind != "image":
            items.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "kind": kind,
                    "accepted": False,
                    "reason": "filtered out by input_type=images",
                }
            )
            counts["rejected"] += 1
            continue
        if input_type == "videos" and kind != "video":
            items.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "kind": kind,
                    "accepted": False,
                    "reason": "filtered out by input_type=videos",
                }
            )
            counts["rejected"] += 1
            continue

        size_bytes = 0
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = 0

        items.append(
            {
                "name": path.name,
                "path": str(path.resolve()),
                "kind": kind,
                "accepted": True,
                "size_bytes": size_bytes,
            }
        )
        if kind == "image":
            counts["images"] += 1
        else:
            counts["videos"] += 1

    return {
        "folder_path": str(root.resolve()),
        "input_type": input_type,
        "items": items,
        "summary": {
            "accepted": counts["images"] + counts["videos"],
            "images": counts["images"],
            "videos": counts["videos"],
            "rejected": counts["rejected"],
            "total": len(items),
        },
    }


@router.post("/{board_id}/scenarios/batch-create")
def batch_create_scenarios(board_id: int, body: ScenarioBatchCreateBody) -> dict:
    if not body.items:
        raise HTTPException(400, "items is required")

    video_audio_mode = _normalize_video_audio_mode(body.video_audio_mode)
    voice_id = _voice_id_for_mode(video_audio_mode, body.voice_id)

    with get_session() as s:
        board = s.get(Board, board_id)
        if board is None:
            raise HTTPException(404, "board not found")

        created: list[dict] = []
        for item in body.items:
            source_path = item.path.strip()
            if not source_path:
                continue
            source_kind = item.kind
            scenario = Scenario(
                board_id=board_id,
                theme=Path(source_path).stem or f"Batch {source_kind}",
                extra_description="",
                content_style="",
                scene_count=body.scene_count,
                video_audio_mode=video_audio_mode,
                voice_id=voice_id,
                status="draft",
                refs={
                    "source_type": "image_file" if source_kind == "image" else "video_file",
                    "source_path": source_path,
                },
            )
            s.add(scenario)
            s.commit()
            s.refresh(scenario)
            created.append(_dump_row(scenario))

    return {"created": created, "count": len(created)}


@router.post("/{board_id}/scenarios/batch-analyze-images")
async def batch_analyze_image_scenarios(board_id: int, body: ScenarioBatchAnalyzeImagesBody) -> dict:
    scenario_ids = list(dict.fromkeys(body.scenario_ids))
    if not scenario_ids:
        raise HTTPException(400, "scenario_ids is required")

    saved: list[dict] = []
    failed: list[dict] = []

    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, "board not found")

        for scenario_id in scenario_ids:
            scenario = s.get(Scenario, scenario_id)
            if scenario is None or scenario.board_id != board_id:
                failed.append({"scenario_id": scenario_id, "error": "scenario not found"})
                continue

            refs = scenario.refs if isinstance(scenario.refs, dict) else {}
            source_type = refs.get("source_type")
            source_path_raw = refs.get("source_path")
            source_path = str(source_path_raw).strip() if isinstance(source_path_raw, str) else ""
            if source_type != "image_file":
                failed.append({"scenario_id": scenario_id, "error": "source_type is not image_file"})
                continue
            if not source_path:
                failed.append({"scenario_id": scenario_id, "error": "source_path is missing"})
                continue

            image_path = Path(source_path)
            if not image_path.exists() or not image_path.is_file():
                failed.append({"scenario_id": scenario_id, "error": "source image file not found"})
                continue

            image_hint = image_path.name
            extra_description = (
                "Generate a complete scenario from the provided image file as the primary reference. "
                "Keep identity, style, product, and environment continuity anchored to that image. "
                f"Image source path: {source_path}."
            )
            plan_input = scenario_planner.ScenarioPlanInput(
                theme=scenario.theme.strip() or image_path.stem,
                extra_description=extra_description,
                scene_count=max(1, min(12, int(scenario.scene_count or 1))),
                content_style=scenario.content_style.strip() or "Premium fashion ad",
                refs=scenario_planner.normalize_refs({}),
            )

            try:
                scene_plans = await scenario_planner.generate_scenario_plan(s, board_id, plan_input)
            except scenario_planner.ScenarioPlannerError as exc:
                failed.append({"scenario_id": scenario_id, "error": str(exc)})
                continue

            old_scenes = list(
                s.exec(select(ScenarioScene).where(ScenarioScene.scenario_id == scenario_id)).all()
            )
            for row in old_scenes:
                s.delete(row)
            s.commit()

            new_scenes: list[ScenarioScene] = []
            for idx, scene in enumerate(scene_plans):
                row = ScenarioScene(
                    scenario_id=scenario_id,
                    idx=idx,
                    title=scene.title,
                    background_description=scene.background_description,
                    background_image_prompt=scene.background_image_prompt,
                    composition_prompt=scene.composition_prompt,
                    motion_prompt=scene.motion_prompt,
                    voice_script=scene.voice_script,
                    voice_direction=scene.voice_direction,
                    duration_seconds=scene.duration_seconds,
                    refs={**scene.refs, "source_path": source_path, "source_image_name": image_hint},
                    status="planned",
                )
                s.add(row)
                new_scenes.append(row)

            scenario.status = "planned"
            scenario.updated_at = datetime.now(timezone.utc)
            s.add(scenario)
            s.commit()

            saved.append({"scenario_id": scenario_id, "scene_count": len(new_scenes)})

    return {"saved": saved, "failed": failed}


@router.post("/{board_id}/scenarios/batch-analyze-videos")
async def batch_analyze_video_scenarios(board_id: int, body: ScenarioBatchAnalyzeVideosBody) -> dict:
    scenario_ids = list(dict.fromkeys(body.scenario_ids))
    if not scenario_ids:
        raise HTTPException(400, "scenario_ids is required")

    saved: list[dict] = []
    failed: list[dict] = []

    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, "board not found")

        for scenario_id in scenario_ids:
            scenario = s.get(Scenario, scenario_id)
            if scenario is None or scenario.board_id != board_id:
                failed.append({"scenario_id": scenario_id, "error": "scenario not found"})
                continue

            refs = scenario.refs if isinstance(scenario.refs, dict) else {}
            source_type = refs.get("source_type")
            source_path_raw = refs.get("source_path")
            source_path = str(source_path_raw).strip() if isinstance(source_path_raw, str) else ""
            if source_type != "video_file":
                failed.append({"scenario_id": scenario_id, "error": "source_type is not video_file"})
                continue
            if not source_path:
                failed.append({"scenario_id": scenario_id, "error": "source_path is missing"})
                continue

            video_path = Path(source_path)
            if not video_path.exists() or not video_path.is_file():
                failed.append({"scenario_id": scenario_id, "error": "source video file not found"})
                continue

            transcript_hint = ""
            try:
                with tempfile.TemporaryDirectory(prefix="flowboard_batch_video_", ignore_cleanup_errors=True) as temp_dir:
                    audio_path = Path(temp_dir) / "audio.wav"
                    _extract_audio(video_path, audio_path)
                    transcript_hint = (
                        "Audio extracted successfully from source video. "
                        "Preserve the original story flow and spoken intent when writing scene voice_script."
                    )
            except HTTPException:
                transcript_hint = "Audio extraction unavailable; infer story progression from visual cues and keep voice_script natural."

            extra_description = (
                "Generate a complete scenario from the provided source video as the primary reference. "
                "Keep continuity with the original narrative arc. "
                f"Source video path: {source_path}. {transcript_hint}"
            )
            plan_input = scenario_planner.ScenarioPlanInput(
                theme=scenario.theme.strip() or video_path.stem,
                extra_description=extra_description,
                scene_count=max(1, min(12, int(scenario.scene_count or 1))),
                content_style=scenario.content_style.strip() or "Premium fashion ad",
                refs=scenario_planner.normalize_refs({}),
            )

            try:
                scene_plans = await scenario_planner.generate_scenario_plan(s, board_id, plan_input)
            except scenario_planner.ScenarioPlannerError as exc:
                failed.append({"scenario_id": scenario_id, "error": str(exc)})
                continue

            old_scenes = list(s.exec(select(ScenarioScene).where(ScenarioScene.scenario_id == scenario_id)).all())
            for row in old_scenes:
                s.delete(row)
            s.commit()

            new_scenes: list[ScenarioScene] = []
            for idx, scene in enumerate(scene_plans):
                row = ScenarioScene(
                    scenario_id=scenario_id,
                    idx=idx,
                    title=scene.title,
                    background_description=scene.background_description,
                    background_image_prompt=scene.background_image_prompt,
                    composition_prompt=scene.composition_prompt,
                    motion_prompt=scene.motion_prompt,
                    voice_script=scene.voice_script,
                    voice_direction=scene.voice_direction,
                    duration_seconds=scene.duration_seconds,
                    refs={**scene.refs, "source_path": source_path, "source_video_name": video_path.name},
                    status="planned",
                )
                s.add(row)
                new_scenes.append(row)

            scenario.status = "planned"
            scenario.updated_at = datetime.now(timezone.utc)
            s.add(scenario)
            s.commit()

            saved.append({"scenario_id": scenario_id, "scene_count": len(new_scenes)})

    return {"saved": saved, "failed": failed}


@router.post("/{board_id}/scenarios/plan")
async def plan_scenario(board_id: int, body: ScenarioPlanBody) -> dict:
    theme = body.theme.strip()
    if not theme:
        raise HTTPException(400, "theme is required")
    if not 1 <= body.scene_count <= 12:
        raise HTTPException(400, "scene_count must be 1..12")
    video_audio_mode = _normalize_video_audio_mode(body.video_audio_mode)
    voice_id = _voice_id_for_mode(video_audio_mode, body.voice_id)

    refs = scenario_planner.normalize_refs(body.refs.model_dump())
    plan_input = scenario_planner.ScenarioPlanInput(
        theme=theme,
        extra_description=body.extra_description,
        scene_count=body.scene_count,
        content_style=body.content_style,
        refs=refs,
    )

    with get_session() as s:
        board = s.get(Board, board_id)
        if board is None:
            raise HTTPException(404, "board not found")
        if body.node_id is not None:
            node = s.get(Node, body.node_id)
            if node is None or node.board_id != board_id:
                raise HTTPException(404, "node not found")
        try:
            scene_plans = await scenario_planner.generate_scenario_plan(
                s,
                board_id,
                plan_input,
            )
        except scenario_planner.ScenarioPlannerError as exc:
            raise HTTPException(502, str(exc)) from exc

        scenario = Scenario(
            board_id=board_id,
            node_id=body.node_id,
            theme=theme,
            extra_description=body.extra_description.strip(),
            content_style=body.content_style.strip(),
            scene_count=body.scene_count,
            video_audio_mode=video_audio_mode,
            voice_id=voice_id,
            status="planned",
            refs=refs,
        )
        s.add(scenario)
        s.commit()
        s.refresh(scenario)
        assert scenario.id is not None

        scenes: list[ScenarioScene] = []
        for idx, scene in enumerate(scene_plans):
            row = ScenarioScene(
                scenario_id=scenario.id,
                idx=idx,
                title=scene.title,
                background_description=scene.background_description,
                background_image_prompt=scene.background_image_prompt,
                composition_prompt=scene.composition_prompt,
                motion_prompt=scene.motion_prompt,
                voice_script=scene.voice_script,
                voice_direction=scene.voice_direction,
                duration_seconds=scene.duration_seconds,
                refs=scene.refs,
                status="planned",
            )
            s.add(row)
            scenes.append(row)
        s.commit()
        s.refresh(scenario)
        for row in scenes:
            s.refresh(row)
        return {
            "scenario": _dump_scenario(scenario, scenes),
            "scenes": [_dump_row(row) for row in scenes],
        }


@router.get("/{board_id}/scenarios")
def list_scenarios(board_id: int):
    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, "board not found")
        rows = s.exec(
            select(Scenario)
            .where(Scenario.board_id == board_id)
            .order_by(Scenario.created_at.desc())
        ).all()
        scenario_ids = [row.id for row in rows if row.id is not None]
        scenes_by_scenario: dict[int, list[ScenarioScene]] = {
            scenario_id: [] for scenario_id in scenario_ids
        }
        if scenario_ids:
            scenes = s.exec(
                select(ScenarioScene).where(ScenarioScene.scenario_id.in_(scenario_ids))
            ).all()
            for scene in scenes:
                scenes_by_scenario.setdefault(scene.scenario_id, []).append(scene)
        return [_dump_scenario(row, scenes_by_scenario.get(row.id or 0, [])) for row in rows]


@router.get("/{board_id}/scenarios/{scenario_id}")
def get_scenario(board_id: int, scenario_id: int) -> dict:
    with get_session() as s:
        scenario = s.get(Scenario, scenario_id)
        if scenario is None or scenario.board_id != board_id:
            raise HTTPException(404, "scenario not found")
        scenes = list(
            s.exec(
                select(ScenarioScene)
                .where(ScenarioScene.scenario_id == scenario_id)
                .order_by(ScenarioScene.idx)
            ).all()
        )
        return {
            "scenario": _dump_scenario(scenario, scenes),
            "scenes": [_dump_row(row) for row in scenes],
        }


@router.patch("/{board_id}/scenarios/{scenario_id}")
def patch_scenario(board_id: int, scenario_id: int, body: ScenarioPatchBody) -> dict:
    with get_session() as s:
        scenario = s.get(Scenario, scenario_id)
        if scenario is None or scenario.board_id != board_id:
            raise HTTPException(404, "scenario not found")

        next_mode = (
            _normalize_video_audio_mode(body.video_audio_mode)
            if body.video_audio_mode is not None
            else scenario.video_audio_mode
        )
        next_voice_id = (
            _normalize_voice_id(body.voice_id)
            if body.voice_id is not None
            else scenario.voice_id
        )
        scenario.video_audio_mode = next_mode
        scenario.voice_id = _voice_id_for_mode(next_mode, next_voice_id)
        scenario.updated_at = datetime.now(timezone.utc)
        s.add(scenario)
        s.commit()
        s.refresh(scenario)
        scenes = list(
            s.exec(
                select(ScenarioScene)
                .where(ScenarioScene.scenario_id == scenario_id)
                .order_by(ScenarioScene.idx)
            ).all()
        )
        return {"scenario": _dump_scenario(scenario, scenes)}


@router.post("/{board_id}/scenarios/{scenario_id}/export")
async def export_scenario(
    board_id: int,
    scenario_id: int,
    body: ScenarioExportBody | None = None,
) -> dict:
    with get_session() as s:
        scenario = s.get(Scenario, scenario_id)
        if scenario is None or scenario.board_id != board_id:
            raise HTTPException(404, "scenario not found")
        folder_name = (
            f"scenario_{scenario_id}_"
            f"{_slugify(scenario.theme, f'scenario_{scenario_id}')}_"
            f"{_export_timestamp()}"
        )
    exported = await _export_scenario_to_dir(
        board_id,
        scenario_id,
        _unique_export_dir(folder_name),
        (body.voice_id.strip() if body and body.voice_id else None),
    )
    return {
        "media_id": exported["media_id"],
        "scenario": exported["scenario"],
        "export_dir": exported["export_dir"],
        "file_path": exported["file_path"],
    }


@router.post("/{board_id}/scenarios/export-selected")
async def export_selected_scenarios(
    board_id: int,
    body: ScenarioExportSelectedBody,
) -> dict:
    scenario_ids = list(dict.fromkeys(body.scenario_ids))
    if not scenario_ids:
        raise HTTPException(400, "scenario_ids is required")
    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, "board not found")

    export_dir = _unique_export_dir(f"export_selected_{_export_timestamp()}")
    exported: list[dict] = []
    failed: list[dict] = []
    for scenario_id in scenario_ids:
        try:
            exported.append(
                await _export_scenario_to_dir(
                    board_id,
                    scenario_id,
                    export_dir,
                    body.voice_id.strip() if body.voice_id else None,
                )
            )
        except HTTPException as exc:
            failed.append({"scenario_id": scenario_id, "error": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001
            failed.append({"scenario_id": scenario_id, "error": str(exc)})
    return {
        "export_dir": str(export_dir),
        "exported": exported,
        "failed": failed,
    }


@router.delete("/{board_id}/scenarios/{scenario_id}")
def delete_scenario(board_id: int, scenario_id: int) -> dict:
    with get_session() as s:
        scenario = s.get(Scenario, scenario_id)
        if scenario is None or scenario.board_id != board_id:
            raise HTTPException(404, "scenario not found")

        scenes = s.exec(
            select(ScenarioScene).where(ScenarioScene.scenario_id == scenario_id)
        ).all()

        media_ids: set[str] = set()
        if scenario.final_video_media_id:
            media_ids.add(scenario.final_video_media_id)
        for scene in scenes:
            for media_id in [
                scene.background_media_id,
                scene.image_media_id,
                scene.video_media_id,
                scene.voice_media_id,
            ]:
                if isinstance(media_id, str) and media_id.strip():
                    media_ids.add(media_id.strip())

        for scene in scenes:
            s.delete(scene)
        s.flush()
        s.delete(scenario)
        s.commit()

    for media_id in media_ids:
        media_service.delete_media(media_id)

    return {"ok": True, "removed_media": len(media_ids)}


@router.patch("/{board_id}/scenarios/{scenario_id}/scenes/{scene_id}")
def update_scenario_scene(
    board_id: int,
    scenario_id: int,
    scene_id: int,
    body: ScenarioScenePatchBody,
) -> dict:
    with get_session() as s:
        scenario = s.get(Scenario, scenario_id)
        if scenario is None or scenario.board_id != board_id:
            raise HTTPException(404, "scenario not found")

        scene = s.get(ScenarioScene, scene_id)
        if scene is None or scene.scenario_id != scenario_id:
            raise HTTPException(404, "scene not found")

        patch = body.model_dump(exclude_unset=True)
        now = datetime.now(timezone.utc)
        for key, value in patch.items():
            setattr(scene, key, value.strip() if isinstance(value, str) else value)
        if {"background_media_id", "image_media_id", "video_media_id"} & set(patch):
            scenario.final_video_media_id = None
        scene.updated_at = now
        scenario.updated_at = now
        s.add(scenario)
        s.add(scene)
        s.commit()
        s.refresh(scene)
        return _dump_row(scene)
