"""起始帧作为参考图进入视频请求：候选展开、水合、H3 渲染补写与非 H3 项目不受影响。"""

from pathlib import Path

import pytest
from PIL import Image

from lib.keyframes.consume import KEYFRAME_REFERENCE_NAME
from lib.keyframes.models import KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.store import save_board
from lib.script.reference_video.h3_prompt import H3PromptUnavailable, bind_h3_prompt, render_h3_prompt
from lib.script.reference_video.request_projection import (
    KEYFRAME_KIND,
    FilesystemReferenceAssets,
    clamp_reference_assets,
    hydrate_reference_assets,
    resolve_reference_assets,
    unit_reference_declarations,
)
from lib.script.script_models import ReferenceResource

TEXT = "@[车内] @[甲] 看向 @[乙]。\n@[甲]{你好。}"
SECTIONS = {
    "subject_definitions": (
        "@[车内] is the setting in <Picture @[车内]>.\n"
        "@[甲] is the friend in <Picture @[甲]>.\n"
        "@[乙] is the protagonist in <Picture @[乙]>."
    ),
    "summary": "[reference generation] @[甲] looks at @[乙] in @[车内].",
    "retention_analysis": "@[甲] (appears in [Shot 1]): fully_preserved - the reference picture defines his appearance.",
    "detailed_description": "Realistic style.\n[Shot 1] A close-up of @[甲] (S1): <d>[中文] 你好。</d>",
    "overall_soundscape": "Engine hum.",
    "non_diegetic_music": "N/A",
}


def _project(*, h3: bool = True) -> dict:
    project: dict = {
        "content_mode": "drama",
        "generation_mode": "reference_video",
        "characters": {
            "甲": {"description": "a", "character_sheet": "characters/甲.png"},
            "乙": {"description": "b", "character_sheet": "characters/乙.png"},
        },
        "scenes": {"车内": {"description": "bus", "scene_sheet": "scenes/车内.png"}},
        "props": {},
    }
    if h3:
        project["video_prompt_profile"] = "h3_ref2va"
    return project


def _image(root: Path, relpath: str) -> Path:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "gray").save(path)
    return path


@pytest.fixture
def board_project(tmp_path: Path) -> Path:
    for relpath in ("characters/甲.png", "characters/乙.png", "scenes/车内.png"):
        _image(tmp_path, relpath)
    _image(tmp_path, "keyframes/episode_1/K01.png")
    cell = KeyframeCell(
        id="K01",
        label="甲近景",
        scene="车内",
        staging="甲左乙右",
        framing="近景",
        characters=["甲"],
        unit_ids=["E1U10"],
        status="approved",
        image="keyframes/episode_1/K01.png",
    )
    save_board(
        tmp_path,
        KeyframeBoard(episode=1, plan_fingerprint="x", sheets=[KeyframeSheet(id="S1", scene="车内", cells=[cell])]),
    )
    return tmp_path


UNIT = {"unit_id": "E1U10", "text": TEXT}


def test_the_keyframe_is_appended_after_the_asset_images(board_project: Path) -> None:
    assets = resolve_reference_assets(_project(), board_project, UNIT)

    assert [(a.reference.type, a.kind) for a in assets] == [
        ("scene", "sheet"),
        ("character", "sheet"),
        ("character", "sheet"),
        ("keyframe", KEYFRAME_KIND),
    ]
    assert assets[-1].reference.name == KEYFRAME_REFERENCE_NAME
    assert assets[-1].path.name == "K01.png"


def test_a_unit_without_a_cell_gets_no_keyframe(board_project: Path) -> None:
    assets = resolve_reference_assets(_project(), board_project, {"unit_id": "E1U11", "text": TEXT})

    assert all(a.reference.type != "keyframe" for a in assets)


def test_a_project_without_the_h3_profile_never_sends_the_keyframe(board_project: Path) -> None:
    assets = resolve_reference_assets(_project(h3=False), board_project, UNIT)

    assert all(a.reference.type != "keyframe" for a in assets)


def test_the_keyframe_survives_hydration_without_being_declared_and_is_never_reported_missing(
    board_project: Path,
) -> None:
    project = _project()
    assets = resolve_reference_assets(project, board_project, UNIT)

    hydration = hydrate_reference_assets(
        unit_reference_declarations(project, UNIT), assets, FilesystemReferenceAssets(board_project)
    )

    assert [a.reference.type for a in hydration.available][-1] == "keyframe"
    assert hydration.missing == ()


def test_a_keyframe_over_the_model_limit_is_the_first_image_dropped(board_project: Path) -> None:
    assets = resolve_reference_assets(_project(), board_project, UNIT)

    kept = clamp_reference_assets(assets, 3)

    assert [a.reference.type for a in kept] == ["scene", "character", "character"]


def test_h3_render_numbers_the_keyframe_and_states_that_the_shot_begins_from_it() -> None:
    payload = bind_h3_prompt(TEXT, SECTIONS)
    references = [
        ReferenceResource(type="scene", name="车内"),
        ReferenceResource(type="character", name="甲"),
        ReferenceResource(type="character", name="乙"),
        ReferenceResource(type="keyframe", name=KEYFRAME_REFERENCE_NAME),
    ]

    prompt = render_h3_prompt(payload, TEXT, _project(), references, [])

    assert "<Picture 4> is the opening frame of this unit." in prompt
    assert "[Shot 1] The shot begins from <Picture 4>" in prompt
    assert "@[" not in prompt


def test_h3_render_without_a_keyframe_is_unchanged() -> None:
    payload = bind_h3_prompt(TEXT, SECTIONS)
    references = [
        ReferenceResource(type="scene", name="车内"),
        ReferenceResource(type="character", name="甲"),
        ReferenceResource(type="character", name="乙"),
    ]

    prompt = render_h3_prompt(payload, TEXT, _project(), references, [])

    assert "opening frame" not in prompt
    assert "The shot begins from" not in prompt


def test_h3_render_still_refuses_a_reference_trimmed_from_the_request() -> None:
    payload = bind_h3_prompt(TEXT, SECTIONS)

    with pytest.raises(H3PromptUnavailable):
        render_h3_prompt(
            payload, TEXT, _project(), [ReferenceResource(type="keyframe", name=KEYFRAME_REFERENCE_NAME)], []
        )
