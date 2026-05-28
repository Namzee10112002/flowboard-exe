from sqlalchemy import inspect
from sqlmodel import select

from flowboard.db import engine, get_session
from flowboard.db.models import Board, Scenario, ScenarioScene


def test_scenario_tables_exist():
    insp = inspect(engine)
    assert insp.has_table("scenario")
    assert insp.has_table("scenarioscene")


def test_scenario_and_scenes_roundtrip_json_and_prompts():
    with get_session() as s:
        board = Board(name="Campaign")
        s.add(board)
        s.commit()
        s.refresh(board)

        scenario = Scenario(
            board_id=board.id,
            theme="Premium streetwear launch",
            extra_description="Vietnamese voice-over, Seoul night market mood",
            content_style="fashion ad",
            scene_count=2,
            video_audio_mode="veo_dialogue_elevenlabs_replace",
            voice_id="voice-model-test",
            status="planned",
            refs={
                "background_media_ids": ["bg-1"],
                "character_media_ids": ["char-1"],
                "visual_asset_media_ids": ["shirt-1"],
            },
        )
        s.add(scenario)
        s.commit()
        s.refresh(scenario)
        scenario_id = scenario.id

        s.add(
            ScenarioScene(
                scenario_id=scenario_id,
                idx=0,
                title="Opening walk",
                background_description="Neon storefronts after rain",
                background_image_prompt="Wet Seoul street, premium fashion ad lighting",
                composition_prompt="Place the character wearing the shirt in the street",
                motion_prompt="Slow confident walk toward camera, fabric moving lightly",
                voice_script="Dem nay, phong cach toi gian tro nen noi bat.",
                voice_direction="Vietnamese female voice, calm premium tone",
                duration_seconds=8.0,
                refs={"character_media_ids": ["char-1"]},
            )
        )
        s.add(
            ScenarioScene(
                scenario_id=scenario_id,
                idx=1,
                title="Product detail",
                background_description="Close-up editorial product moment",
                background_image_prompt="Minimal boutique wall with soft side light",
                composition_prompt="Show the shirt texture clearly on the character",
                motion_prompt="Subtle collar adjustment and direct eye contact",
                voice_script="Tung chi tiet duoc tao ra de giu anh nhin lai.",
                voice_direction="Vietnamese female voice, intimate and polished",
                duration_seconds=8.0,
                status="image_done",
                image_media_id="scene-image-2",
            )
        )
        s.commit()

    with get_session() as s:
        saved = s.get(Scenario, scenario_id)
        assert saved is not None
        assert saved.refs["character_media_ids"] == ["char-1"]
        assert saved.scene_count == 2
        assert saved.video_audio_mode == "veo_dialogue_elevenlabs_replace"
        assert saved.voice_id == "voice-model-test"

        scenes = s.exec(
            select(ScenarioScene)
            .where(ScenarioScene.scenario_id == scenario_id)
            .order_by(ScenarioScene.idx)
        ).all()
        assert [scene.title for scene in scenes] == ["Opening walk", "Product detail"]
        assert scenes[0].background_image_prompt.startswith("Wet Seoul")
        assert scenes[0].motion_prompt.startswith("Slow confident")
        assert scenes[0].voice_script.startswith("Dem nay")
        assert scenes[1].status == "image_done"
        assert scenes[1].image_media_id == "scene-image-2"
