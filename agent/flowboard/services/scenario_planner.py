"""Auto Flow scenario planner.

This service turns the Scenario Planner form into a durable multi-scene
script. It routes through the configured `planner` LLM feature, so when the
user pins OpenAI in Settings the actual transport is OpenAIProvider's Codex
CLI path.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

from sqlmodel import select

from flowboard.db.models import Node
from flowboard.services.activity import record_activity
from flowboard.services.llm import run_llm
from flowboard.services.llm.base import LLMError

logger = logging.getLogger(__name__)

REF_KEYS = (
    "background_media_ids",
    "character_media_ids",
    "visual_asset_media_ids",
)

_SCENARIO_SYSTEM_PROMPT = """You are Flowboard's Auto Flow scenario planner.

Flowboard creates AI media in stages:
1. Generate a background-only image.
2. Generate a composed scene image using background, character refs, and visual asset refs.
3. Generate an 8-second silent motion video from that scene image.
4. Add voice, lipsync, and final merge later.

Return ONLY valid JSON, no markdown and no commentary. Shape:
{
  "scenes": [
    {
      "title": "short scene title",
      "background_description": "plain-language setting description",
      "background_image_prompt": "prompt for a background-only image",
      "composition_prompt": "prompt for composing characters/assets into the scene",
      "motion_prompt": "silent 8-second video motion prompt",
      "voice_script": "spoken line for later TTS/lipsync",
      "voice_direction": "voice performance direction",
      "duration_seconds": 8,
      "refs": {
        "background_media_ids": [],
        "character_media_ids": [],
        "visual_asset_media_ids": []
      }
    }
  ]
}

Rules:
- Output exactly the requested number of scenes.
- Make every scene advance the story; avoid interchangeable beats.
- Keep identity, wardrobe, product, and brand continuity across scenes.
- background_image_prompt must describe the environment only unless the user explicitly asks for product/background signage.
- composition_prompt must be image-generation ready and may reference selected refs by role, not by internal ids.
- motion_prompt is for Veo-style image-to-video and must be silent: no speech, no voice-over, no lip-sync, no singing, no subtitles, no text overlays.
- Put spoken words only in voice_script. Voice is added after video generation.
- voice_direction must not impersonate a real person. Use generic delivery, language, pace, tone, and emotion.
- refs arrays may contain only exact media ids provided in the user context. Use empty arrays when a bucket is unused.
"""

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


class ScenarioPlannerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScenarioPlanInput:
    theme: str
    extra_description: str
    scene_count: int
    content_style: str
    refs: dict[str, list[str]]


@dataclass(frozen=True)
class ScenarioScenePlan:
    title: str
    background_description: str
    background_image_prompt: str
    composition_prompt: str
    motion_prompt: str
    voice_script: str
    voice_direction: str
    duration_seconds: float
    refs: dict[str, list[str]]


def normalize_refs(refs: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """Return de-duped media-id arrays for every scenario ref bucket."""
    out: dict[str, list[str]] = {key: [] for key in REF_KEYS}
    if not isinstance(refs, Mapping):
        return out
    for key in REF_KEYS:
        raw = refs.get(key)
        if not isinstance(raw, list):
            continue
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            mid = item.strip()
            if mid.startswith("media/"):
                mid = mid[6:]
            if not mid or mid in seen:
                continue
            seen.add(mid)
            cleaned.append(mid)
        out[key] = cleaned
    return out


async def generate_scenario_plan(
    session,
    board_id: int,
    plan_input: ScenarioPlanInput,
) -> list[ScenarioScenePlan]:
    """Ask the configured planner LLM for a validated scene list."""
    clean_theme = plan_input.theme.strip()
    if not clean_theme:
        raise ScenarioPlannerError("theme is required")
    if not 1 <= plan_input.scene_count <= 12:
        raise ScenarioPlannerError("scene_count must be 1..12")

    clean_input = ScenarioPlanInput(
        theme=clean_theme,
        extra_description=plan_input.extra_description.strip(),
        scene_count=plan_input.scene_count,
        content_style=plan_input.content_style.strip(),
        refs=normalize_refs(plan_input.refs),
    )
    user_prompt = _build_user_prompt(session, board_id, clean_input)

    try:
        async with record_activity(
            "planner",
            params={
                "kind": "scenario",
                "board_id": board_id,
                "theme": clean_input.theme[:200],
                "scene_count": clean_input.scene_count,
                "content_style": clean_input.content_style[:120],
            },
        ) as activity:
            try:
                raw = await run_llm(
                    "planner",
                    user_prompt,
                    system_prompt=_SCENARIO_SYSTEM_PROMPT,
                    timeout=150.0,
                )
            except LLMError as exc:
                raise ScenarioPlannerError(
                    f"scenario planner provider failed: {exc}"
                ) from exc

            scenes = _parse_scene_plan(raw, clean_input.scene_count, clean_input.refs)
            activity.set_result(
                {
                    "kind": "scenario",
                    "scene_count": len(scenes),
                    "raw_length": len(raw or ""),
                }
            )
            return scenes
    except ScenarioPlannerError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("scenario planner failed unexpectedly")
        raise ScenarioPlannerError(f"scenario planner failed: {exc}") from exc


def _build_user_prompt(session, board_id: int, plan_input: ScenarioPlanInput) -> str:
    ref_context = _selected_ref_context(session, board_id, plan_input.refs)

    parts = [
        f"Board id: {board_id}",
        f"Theme: {plan_input.theme}",
        f"Additional description: {plan_input.extra_description or '(none)'}",
        f"Scene count: {plan_input.scene_count}",
        f"Content style: {plan_input.content_style or '(unspecified)'}",
        "",
        "Selected references:",
    ]
    for key in REF_KEYS:
        items = ref_context.get(key, [])
        label = key.replace("_media_ids", "")
        parts.append(f"{label}:")
        if not items:
            parts.append("  - (none)")
            continue
        for idx, item in enumerate(items, start=1):
            parts.append(
                "  - "
                + json.dumps(
                    {
                        "label": f"{label}_ref_{idx}",
                        "media_id": item["media_id"],
                        "node_type": item["node_type"],
                        "title": item["title"],
                        "description": item["description"],
                        "aspect_ratio": item["aspect_ratio"],
                    },
                    ensure_ascii=True,
                )
            )

    parts.extend(
        [
            "",
            "Use the selected refs as creative constraints. In each scene.refs,",
            "include only exact media_id values from the selected reference lists.",
            "Do not include internal labels or node ids inside generated prompts.",
        ]
    )
    return "\n".join(parts)


def _selected_ref_context(
    session,
    board_id: int,
    refs: dict[str, list[str]],
) -> dict[str, list[dict[str, Any]]]:
    nodes = session.exec(select(Node).where(Node.board_id == board_id)).all()
    by_media: dict[str, list[Node]] = {}
    for node in nodes:
        data = node.data or {}
        media_ids: list[str] = []
        media_id = data.get("mediaId")
        if isinstance(media_id, str) and media_id:
            media_ids.append(media_id)
        variants = data.get("mediaIds")
        if isinstance(variants, list):
            media_ids.extend([m for m in variants if isinstance(m, str) and m])
        for mid in media_ids:
            by_media.setdefault(mid, []).append(node)

    out: dict[str, list[dict[str, Any]]] = {key: [] for key in REF_KEYS}
    for key in REF_KEYS:
        for media_id in refs.get(key, []):
            node = by_media.get(media_id, [None])[0]
            data = node.data if node is not None and isinstance(node.data, dict) else {}
            prompt = data.get("prompt") if isinstance(data.get("prompt"), str) else ""
            brief = data.get("aiBrief") if isinstance(data.get("aiBrief"), str) else ""
            title = data.get("title") if isinstance(data.get("title"), str) else ""
            description = prompt or brief or title or "(no description)"
            aspect = data.get("aspectRatio") if isinstance(data.get("aspectRatio"), str) else None
            out[key].append(
                {
                    "media_id": media_id,
                    "node_type": node.type if node is not None else "unknown",
                    "title": title or (node.type if node is not None else "Unknown ref"),
                    "description": description,
                    "aspect_ratio": aspect,
                }
            )
    return out


def _parse_scene_plan(
    raw: str,
    expected_count: int,
    scenario_refs: dict[str, list[str]],
) -> list[ScenarioScenePlan]:
    payload = _loads_json_object(raw)
    scenes_raw = payload.get("scenes")
    if scenes_raw is None:
        scenes_raw = payload.get("scene_plans")
    if not isinstance(scenes_raw, list):
        raise ScenarioPlannerError("scenario planner response missing scenes[]")
    if len(scenes_raw) != expected_count:
        raise ScenarioPlannerError(
            f"scenario planner returned {len(scenes_raw)} scenes, expected {expected_count}"
        )

    scenes: list[ScenarioScenePlan] = []
    for idx, item in enumerate(scenes_raw):
        if not isinstance(item, dict):
            raise ScenarioPlannerError(f"scene {idx} is not an object")
        scenes.append(_coerce_scene(item, scenario_refs, idx))
    return scenes


def _loads_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ScenarioPlannerError("scenario planner returned empty response")

    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    if text.startswith("["):
        end = text.rfind("]")
        if end != -1:
            try:
                arr = json.loads(text[: end + 1])
            except json.JSONDecodeError as exc:
                raise ScenarioPlannerError("scenario planner returned invalid JSON") from exc
            return {"scenes": arr}

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ScenarioPlannerError("scenario planner returned non-JSON text")
        text = text[start : end + 1]

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioPlannerError("scenario planner returned invalid JSON") from exc
    if not isinstance(obj, dict):
        raise ScenarioPlannerError("scenario planner JSON is not an object")
    return obj


def _coerce_scene(
    scene: dict[str, Any],
    scenario_refs: dict[str, list[str]],
    idx: int,
) -> ScenarioScenePlan:
    duration_raw = scene.get("duration_seconds", 8.0)
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        duration = 8.0
    duration = max(3.0, min(15.0, duration))

    return ScenarioScenePlan(
        title=_text_field(scene, ("title", "scene_title", "name"), idx),
        background_description=_text_field(
            scene,
            ("background_description", "background", "setting"),
            idx,
        ),
        background_image_prompt=_text_field(
            scene,
            ("background_image_prompt", "background_prompt"),
            idx,
        ),
        composition_prompt=_text_field(
            scene,
            ("composition_prompt", "image_prompt", "scene_image_prompt"),
            idx,
        ),
        motion_prompt=_text_field(
            scene,
            ("motion_prompt", "video_prompt"),
            idx,
        ),
        voice_script=_text_field(
            scene,
            ("voice_script", "voiceover", "voice_over", "dialogue"),
            idx,
        ),
        voice_direction=_text_field(
            scene,
            ("voice_direction", "voice_style", "voice_notes"),
            idx,
        ),
        duration_seconds=duration,
        refs=_coerce_scene_refs(scene.get("refs"), scenario_refs),
    )


def _text_field(scene: dict[str, Any], keys: tuple[str, ...], idx: int) -> str:
    for key in keys:
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            return _cap_text(value.strip(), 1800)
    raise ScenarioPlannerError(f"scene {idx} missing {keys[0]}")


def _coerce_scene_refs(
    raw_refs: Any,
    scenario_refs: dict[str, list[str]],
) -> dict[str, list[str]]:
    proposed = normalize_refs(raw_refs if isinstance(raw_refs, Mapping) else None)
    out: dict[str, list[str]] = {key: [] for key in REF_KEYS}
    for key in REF_KEYS:
        allowed = scenario_refs.get(key, [])
        allowed_set = set(allowed)
        filtered = [mid for mid in proposed.get(key, []) if mid in allowed_set]
        out[key] = filtered if filtered else list(allowed)
    return out


def _cap_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."
