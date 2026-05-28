from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import select

from flowboard.config import STORAGE_DIR
from flowboard.db import get_session
from flowboard.db.models import Board, Node, Scenario, ScenarioScene
from flowboard.routes import scenarios
from flowboard.services import media as media_service
from flowboard.services import scenario_planner


def _seed_board_with_refs() -> dict:
    with get_session() as s:
        board = Board(name="Auto Flow")
        s.add(board)
        s.commit()
        s.refresh(board)

        bg = Node(
            board_id=board.id,
            short_id="bg01",
            type="image",
            data={
                "title": "Neon street",
                "aiBrief": "rainy neon street at night, reflective asphalt",
                "mediaId": "bg-media-1",
            },
            status="done",
        )
        char = Node(
            board_id=board.id,
            short_id="ch01",
            type="character",
            data={
                "title": "Main model",
                "aiBrief": "Vietnamese woman, neutral expression, black hair",
                "mediaId": "char-media-1",
            },
            status="done",
        )
        asset = Node(
            board_id=board.id,
            short_id="as01",
            type="visual_asset",
            data={
                "title": "White jacket",
                "aiBrief": "premium white cropped jacket with silver zipper",
                "mediaId": "asset-media-1",
            },
            status="done",
        )
        s.add_all([bg, char, asset])
        s.commit()
        return {
            "board_id": board.id,
            "bg": "bg-media-1",
            "char": "char-media-1",
            "asset": "asset-media-1",
        }


def _scene_payload(count: int = 2) -> dict:
    scenes = []
    for i in range(count):
        scenes.append(
            {
                "title": f"Scene {i + 1}",
                "background_description": f"Background {i + 1}",
                "background_image_prompt": f"Background prompt {i + 1}",
                "composition_prompt": f"Composition prompt {i + 1}",
                "motion_prompt": f"Silent motion prompt {i + 1}",
                "voice_script": f"Voice line {i + 1}",
                "voice_direction": "Vietnamese voice, calm premium tone",
                "duration_seconds": 8,
                "refs": {
                    "background_media_ids": ["bg-media-1"],
                    "character_media_ids": ["char-media-1"],
                    "visual_asset_media_ids": ["asset-media-1"],
                },
            }
        )
    return {"scenes": scenes}


@pytest.mark.asyncio
async def test_generate_scenario_plan_calls_planner_with_ref_context(
    client,
    monkeypatch,
):
    ids = _seed_board_with_refs()
    captured: dict = {}

    async def stub_run_llm(feature, user_prompt, *, system_prompt=None, timeout=0):
        captured["feature"] = feature
        captured["user_prompt"] = user_prompt
        captured["system_prompt"] = system_prompt
        captured["timeout"] = timeout
        return "```json\n" + json.dumps(_scene_payload(2)) + "\n```"

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)

    with get_session() as s:
        out = await scenario_planner.generate_scenario_plan(
            s,
            ids["board_id"],
            scenario_planner.ScenarioPlanInput(
                theme="Premium streetwear launch",
                extra_description="Vietnamese voice-over",
                scene_count=2,
                content_style="Luxury editorial",
                refs={
                    "background_media_ids": [ids["bg"]],
                    "character_media_ids": [ids["char"]],
                    "visual_asset_media_ids": [ids["asset"]],
                },
            ),
        )

    assert captured["feature"] == "planner"
    assert captured["timeout"] == 150.0
    assert "Premium streetwear launch" in captured["user_prompt"]
    assert "rainy neon street" in captured["user_prompt"]
    assert "Return ONLY valid JSON" in captured["system_prompt"]
    assert len(out) == 2
    assert out[0].background_image_prompt == "Background prompt 1"
    assert out[0].refs["character_media_ids"] == [ids["char"]]


def test_plan_scenario_route_persists_scenario_and_scenes(client, monkeypatch):
    ids = _seed_board_with_refs()

    async def stub_run_llm(feature, user_prompt, *, system_prompt=None, timeout=0):
        assert feature == "planner"
        assert "white cropped jacket" in user_prompt
        return json.dumps(_scene_payload(2))

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)

    r = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={
            "theme": "Premium streetwear launch",
            "extra_description": "Voice in Vietnamese",
            "scene_count": 2,
            "content_style": "Luxury editorial",
            "video_audio_mode": "veo_dialogue",
            "refs": {
                "background_media_ids": [ids["bg"]],
                "character_media_ids": [ids["char"]],
                "visual_asset_media_ids": [ids["asset"]],
            },
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"]["status"] == "planned"
    assert body["scenario"]["scene_count"] == 2
    assert body["scenario"]["video_audio_mode"] == "veo_dialogue"
    assert body["scenario"]["generation_summary"]["stage"] == "needs_background"
    assert len(body["scenes"]) == 2
    assert body["scenes"][0]["idx"] == 0
    assert body["scenes"][1]["voice_script"] == "Voice line 2"

    scenario_id = body["scenario"]["id"]
    with get_session() as s:
        scenario = s.get(Scenario, scenario_id)
        assert scenario is not None
        assert scenario.video_audio_mode == "veo_dialogue"
        rows = s.exec(
            select(ScenarioScene)
            .where(ScenarioScene.scenario_id == scenario_id)
            .order_by(ScenarioScene.idx)
        ).all()
        assert [row.title for row in rows] == ["Scene 1", "Scene 2"]


def test_plan_scenario_route_rejects_bad_scene_count(client):
    ids = _seed_board_with_refs()
    r = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={"theme": "x", "scene_count": 0},
    )
    assert r.status_code == 400


def test_plan_scenario_route_rejects_bad_video_audio_mode(client):
    ids = _seed_board_with_refs()
    r = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={
            "theme": "x",
            "scene_count": 1,
            "video_audio_mode": "karaoke",
        },
    )
    assert r.status_code == 400


def test_plan_scenario_route_returns_502_on_mismatched_llm_count(
    client,
    monkeypatch,
):
    ids = _seed_board_with_refs()

    async def stub_run_llm(*args, **kwargs):
        return json.dumps(_scene_payload(1))

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)

    r = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={
            "theme": "Needs two scenes",
            "scene_count": 2,
            "refs": {
                "background_media_ids": [ids["bg"]],
                "character_media_ids": [ids["char"]],
                "visual_asset_media_ids": [ids["asset"]],
            },
        },
    )
    assert r.status_code == 502
    assert "expected 2" in r.json()["detail"]

def test_update_scenario_scene_persists_editable_plan_fields(client, monkeypatch):
    ids = _seed_board_with_refs()

    async def stub_run_llm(*args, **kwargs):
        return json.dumps(_scene_payload(1))

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)

    create = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={
            "theme": "Editable scenario",
            "scene_count": 1,
            "refs": {
                "background_media_ids": [ids["bg"]],
                "character_media_ids": [ids["char"]],
                "visual_asset_media_ids": [ids["asset"]],
            },
        },
    )
    assert create.status_code == 200, create.text
    created = create.json()
    scenario_id = created["scenario"]["id"]
    scene_id = created["scenes"][0]["id"]

    patch = client.patch(
        f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}/scenes/{scene_id}",
        json={
            "title": "Edited opening",
            "background_image_prompt": "Edited background prompt",
            "background_media_id": "bg-generated-1",
            "image_media_id": "scene-image-1",
            "video_media_id": "scene-video-1",
            "voice_media_id": "scene-voice-1",
            "motion_prompt": "Edited silent motion",
            "voice_script": "Edited voice line",
            "duration_seconds": 9.5,
            "status": "voice_done",
            "error": None,
        },
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["title"] == "Edited opening"
    assert body["background_image_prompt"] == "Edited background prompt"
    assert body["background_media_id"] == "bg-generated-1"
    assert body["image_media_id"] == "scene-image-1"
    assert body["video_media_id"] == "scene-video-1"
    assert body["voice_media_id"] == "scene-voice-1"
    assert body["motion_prompt"] == "Edited silent motion"
    assert body["duration_seconds"] == 9.5
    assert body["status"] == "voice_done"

    detail = client.get(f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["scenario"]["generation_summary"]["stage"] == "video_done"
    assert detail_body["scenario"]["generation_summary"]["video_done"] == 1
    saved = detail_body["scenes"][0]
    assert saved["voice_script"] == "Edited voice line"
    assert saved["image_media_id"] == "scene-image-1"
    assert saved["video_media_id"] == "scene-video-1"
    assert saved["voice_media_id"] == "scene-voice-1"

    listed = client.get(f"/api/boards/{ids['board_id']}/scenarios")
    assert listed.status_code == 200, listed.text
    listed_scenario = next(
        item for item in listed.json() if item["id"] == scenario_id
    )
    assert listed_scenario["generation_summary"]["stage"] == "video_done"


def test_update_scenario_audio_settings_persists_voice_id(client, monkeypatch):
    ids = _seed_board_with_refs()

    async def stub_run_llm(*args, **kwargs):
        return json.dumps(_scene_payload(1))

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)

    create = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={"theme": "Audio settings", "scene_count": 1},
    )
    assert create.status_code == 200, create.text
    scenario_id = create.json()["scenario"]["id"]

    patch = client.patch(
        f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}",
        json={
            "video_audio_mode": "veo_dialogue_elevenlabs_replace",
            "voice_id": "voice_saved",
        },
    )
    assert patch.status_code == 200, patch.text
    scenario = patch.json()["scenario"]
    assert scenario["video_audio_mode"] == "veo_dialogue_elevenlabs_replace"
    assert scenario["voice_id"] == "voice_saved"

    detail = client.get(f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["scenario"]["voice_id"] == "voice_saved"


def test_delete_scenario_removes_scenario_and_scenes(client, monkeypatch):
    ids = _seed_board_with_refs()

    async def stub_run_llm(*args, **kwargs):
        return json.dumps(_scene_payload(2))

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)

    create = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={"theme": "Delete me", "scene_count": 2},
    )
    assert create.status_code == 200, create.text
    created = create.json()
    scenario_id = created["scenario"]["id"]

    deleted = client.delete(f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["ok"] is True

    detail = client.get(f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}")
    assert detail.status_code == 404
    with get_session() as s:
        assert s.get(Scenario, scenario_id) is None
        rows = s.exec(
            select(ScenarioScene).where(ScenarioScene.scenario_id == scenario_id)
        ).all()
        assert rows == []


def test_export_scenario_merges_scene_videos(client, monkeypatch):
    ids = _seed_board_with_refs()

    async def stub_run_llm(*args, **kwargs):
        return json.dumps(_scene_payload(2))

    def stub_ffmpeg_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"merged mp4")
        import subprocess

        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)
    monkeypatch.setattr("flowboard.routes.scenarios.subprocess.run", stub_ffmpeg_run)

    assert media_service.ingest_inline_bytes("aa11", b"scene one", mime="video/mp4")
    assert media_service.ingest_inline_bytes("bb22", b"scene two", mime="video/mp4")

    create = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={"theme": "Export me", "scene_count": 2},
    )
    assert create.status_code == 200, create.text
    created = create.json()
    scenario_id = created["scenario"]["id"]
    for scene, media_id in zip(created["scenes"], ["aa11", "bb22"]):
        patch = client.patch(
            f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}"
            f"/scenes/{scene['id']}",
            json={"video_media_id": media_id, "status": "video_done"},
        )
        assert patch.status_code == 200, patch.text

    exported = client.post(f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}/export")
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["media_id"]
    assert body["scenario"]["final_video_media_id"] == body["media_id"]
    assert body["scenario"]["status"] == "done"
    export_dir = Path(body["export_dir"])
    file_path = Path(body["file_path"])
    assert export_dir.parent == STORAGE_DIR / "exports"
    assert export_dir.name.startswith(f"scenario_{scenario_id}_export_me_")
    assert file_path.parent == export_dir
    assert file_path.is_file()
    assert file_path.read_bytes() == b"merged mp4"
    cached = media_service.cached_path(body["media_id"])
    assert cached is not None
    assert cached.resolve() == file_path.resolve()


def test_export_selected_scenarios_writes_batch_folder(client, monkeypatch):
    ids = _seed_board_with_refs()

    async def stub_run_llm(*args, **kwargs):
        return json.dumps(_scene_payload(1))

    def stub_ffmpeg_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"selected export")
        import subprocess

        return subprocess.CompletedProcess(cmd, 0, "", "")

    def create_ready_scenario(theme: str, media_id: str) -> int:
        created = client.post(
            f"/api/boards/{ids['board_id']}/scenarios/plan",
            json={"theme": theme, "scene_count": 1},
        )
        assert created.status_code == 200, created.text
        body = created.json()
        scenario_id = body["scenario"]["id"]
        patch = client.patch(
            f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}"
            f"/scenes/{body['scenes'][0]['id']}",
            json={"video_media_id": media_id, "status": "video_done"},
        )
        assert patch.status_code == 200, patch.text
        return scenario_id

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)
    monkeypatch.setattr("flowboard.routes.scenarios.subprocess.run", stub_ffmpeg_run)

    assert media_service.ingest_inline_bytes("cc33", b"scene one", mime="video/mp4")
    assert media_service.ingest_inline_bytes("dd44", b"scene two", mime="video/mp4")
    scenario_a = create_ready_scenario("Batch one", "cc33")
    scenario_b = create_ready_scenario("Batch two", "dd44")

    exported = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/export-selected",
        json={"scenario_ids": [scenario_a, scenario_b]},
    )
    assert exported.status_code == 200, exported.text
    body = exported.json()
    export_dir = Path(body["export_dir"])
    assert export_dir.parent == STORAGE_DIR / "exports"
    assert export_dir.name.startswith("export_selected_")
    assert body["failed"] == []
    assert [item["scenario_id"] for item in body["exported"]] == [scenario_a, scenario_b]
    for item in body["exported"]:
        file_path = Path(item["file_path"])
        assert item["scenario"]["final_video_media_id"] == item["media_id"]
        assert file_path.parent == export_dir
        assert file_path.is_file()
        assert file_path.read_bytes() == b"selected export"


def test_export_voice_changer_separates_vocals_and_keeps_background(client, monkeypatch):
    ids = _seed_board_with_refs()
    calls: list[list[str]] = []

    async def stub_run_llm(*args, **kwargs):
        return json.dumps(_scene_payload(1))

    async def stub_speech_to_speech_file(**kwargs):
        assert kwargs["voice_id"] == "voice123"
        assert kwargs["audio_path"].name == "vocals.wav"
        return b"changed vocal"

    def stub_run(cmd, **kwargs):
        calls.append([str(part) for part in cmd])
        output = Path(cmd[-1])
        import subprocess

        if "--two-stems" in cmd and "vocals" in cmd:
            out_dir = Path(cmd[cmd.index("-o") + 1])
            stem_dir = out_dir / "htdemucs" / "source_audio"
            stem_dir.mkdir(parents=True)
            (stem_dir / "vocals.wav").write_bytes(b"vocals")
            (stem_dir / "no_vocals.wav").write_bytes(b"background")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        output.write_bytes(b"video" if output.suffix == ".mp4" else b"audio")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)
    monkeypatch.setattr("flowboard.routes.scenarios.subprocess.run", stub_run)
    monkeypatch.setattr(
        "flowboard.routes.scenarios.elevenlabs.speech_to_speech_file",
        stub_speech_to_speech_file,
    )

    assert media_service.ingest_inline_bytes("ee55", b"scene video", mime="video/mp4")

    create = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={
            "theme": "Voice changed",
            "scene_count": 1,
            "video_audio_mode": "veo_dialogue_elevenlabs_replace",
            "voice_id": "voice123",
        },
    )
    assert create.status_code == 200, create.text
    created = create.json()
    scenario_id = created["scenario"]["id"]
    patch = client.patch(
        f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}"
        f"/scenes/{created['scenes'][0]['id']}",
        json={"video_media_id": "ee55", "status": "video_done"},
    )
    assert patch.status_code == 200, patch.text

    exported = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/{scenario_id}/export",
        json={},
    )
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert Path(body["file_path"]).is_file()
    assert any("demucs" in part for cmd in calls for part in cmd)
    mux_calls = [
        cmd for cmd in calls
        if any("amix=inputs=2:duration=first" in part for part in cmd)
    ]
    assert mux_calls
    assert any("no_vocals.wav" in part for part in mux_calls[0])
    assert any("changed_vocals.wav" in part for part in mux_calls[0])


def test_demucs_command_uses_packaged_executable_dir_when_frozen(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    tools_dir = install_dir / "tools"
    tools_dir.mkdir(parents=True)
    exe_name = "demucs_soundfile.exe" if scenarios.os.name == "nt" else "demucs_soundfile"
    demucs_exe = tools_dir / exe_name
    demucs_exe.write_bytes(b"stub")

    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    monkeypatch.delenv("FLOWBOARD_DEMUCS_CMD", raising=False)
    monkeypatch.delenv("FLOWBOARD_INSTALL_DIR", raising=False)
    monkeypatch.delattr(scenarios.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(scenarios.sys, "frozen", True, raising=False)
    monkeypatch.setattr(scenarios.sys, "executable", str(install_dir / "Flowboard.exe"))
    monkeypatch.setattr(scenarios.shutil, "which", lambda _name: None)

    assert scenarios._demucs_command() == [str(demucs_exe)]

def test_update_scenario_scene_rejects_wrong_board(client, monkeypatch):
    ids = _seed_board_with_refs()
    other = client.post("/api/boards", json={"name": "Other"}).json()

    async def stub_run_llm(*args, **kwargs):
        return json.dumps(_scene_payload(1))

    monkeypatch.setattr(scenario_planner, "run_llm", stub_run_llm)

    create = client.post(
        f"/api/boards/{ids['board_id']}/scenarios/plan",
        json={"theme": "Editable scenario", "scene_count": 1},
    )
    assert create.status_code == 200, create.text
    created = create.json()

    r = client.patch(
        f"/api/boards/{other['id']}/scenarios/{created['scenario']['id']}"
        f"/scenes/{created['scenes'][0]['id']}",
        json={"title": "Should not apply"},
    )
    assert r.status_code == 404
