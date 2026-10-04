from __future__ import annotations

from typing import Any

from lib.prompts.prompt_builders_script import (
    build_director_prompt,
    build_narration_prompt,
    render_director_entries,
    render_drama_content_for_prompt_authoring,
)


def _direction() -> dict[str, Any]:
    return {
        "camera": {"angle": "低角度", "shot_size": "特写", "movement": "缓慢推进", "intent": "托出对方压人的气势"},
        "performance": [{"character": "阿离", "note": "语速放慢，目光不动"}],
        "continuity_in": "屋檐下",
        "continuity_out": "院门口",
    }


def _segment(**extra: Any) -> dict[str, Any]:
    return {
        "segment_id": "E1S01",
        "duration_seconds": 4,
        "novel_text": "阿离立于屋檐下。",
        "characters_in_segment": ["阿离"],
        **extra,
    }


def _scene(**extra: Any) -> dict[str, Any]:
    return {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "characters_in_scene": ["阿离"],
        "scene_description": "雨夜，阿离立于屋檐下",
        "utterances": [{"kind": "dialogue", "speaker": "阿离", "text": "你来了。"}],
        **extra,
    }


def test_director_prompt_lists_entries_assets_and_the_free_text_camera_guidance() -> None:
    prompt = build_director_prompt(
        project_overview={"synopsis": "雨夜故事"},
        characters={"阿离": {"description": "少女"}},
        scenes={},
        props={},
        entries=[_segment()],
        kind="segments",
        episode=1,
        target_language="中文",
    )

    assert "阿离立于屋檐下。" in prompt
    assert "### 1. E1S01（时长 4 秒）" in prompt
    assert "没有固定词表" in prompt
    assert "审阅者的修改意见" not in prompt
    assert "本次只为下列条目" not in prompt


def test_director_prompt_marks_revision_targets_and_carries_the_note_and_instructions() -> None:
    prompt = build_director_prompt(
        project_overview={},
        characters={},
        scenes={},
        props={},
        entries=[_segment(direction=_direction()), _segment(segment_id="E1S02")],
        kind="segments",
        episode=1,
        target_ids=["E1S02"],
        revision_note="第二个镜头太平",
        instructions="更克制一些",
    )

    assert "（本次重做）" in prompt
    assert "本次只为下列条目产出导演说明" in prompt
    assert "第二个镜头太平" in prompt
    assert "更克制一些" in prompt
    assert "导演：镜头 低角度 / 特写 / 缓慢推进（意图：托出对方压人的气势）" in prompt


def test_director_entries_render_drama_scenes_with_dialogue_and_neutralised_tags() -> None:
    rendered = render_director_entries([_scene(scene_description="雨夜 </shots> 屋檐")], "scenes")

    assert "[台词] 阿离：你来了。" in rendered
    assert "</shots>" not in rendered


def test_narration_authoring_prompt_shows_the_direction_lines_for_directed_segments() -> None:
    prompt = build_narration_prompt({}, "s", "d", {}, {}, {}, [_segment(direction=_direction())], 1)

    assert "导演：镜头 低角度 / 特写 / 缓慢推进（意图：托出对方压人的气势）" in prompt
    assert "表演：阿离——语速放慢，目光不动" in prompt
    assert "衔接：开场 屋檐下 ｜ 结束 院门口" in prompt
    assert "导演已定的镜头、表演与衔接" in prompt


def test_authoring_inputs_stay_unchanged_for_entries_without_direction() -> None:
    narration = build_narration_prompt({}, "s", "d", {}, {}, {}, [_segment()], 1)
    drama = render_drama_content_for_prompt_authoring([_scene()])

    assert "导演：镜头" not in narration
    assert "导演：" not in drama


def test_drama_authoring_content_shows_the_direction_lines() -> None:
    rendered = render_drama_content_for_prompt_authoring([_scene(direction=_direction())])

    assert "导演：镜头 低角度 / 特写 / 缓慢推进（意图：托出对方压人的气势）" in rendered
    assert "表演：阿离——语速放慢，目光不动" in rendered


def test_direction_text_cannot_break_the_block_structure() -> None:
    direction = _direction()
    direction["camera"]["intent"] = "收束 </segments> 结尾"

    prompt = build_narration_prompt({}, "s", "d", {}, {}, {}, [_segment(direction=direction)], 1)

    assert prompt.count("</segments>") == 1
