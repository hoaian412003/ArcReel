"""视频请求侧取用起始帧：只认已审核且图在盘上的格子，缺失一律当作没有。"""

from pathlib import Path

import pytest
from PIL import Image

from lib.keyframes.consume import (
    KEYFRAME_REFERENCE_NAME,
    approved_keyframe,
    episode_of_unit,
    with_opening_frame,
)
from lib.keyframes.models import CellStatus, KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.store import board_path, save_board


def _board(*, status: CellStatus = "approved", image: str | None = "keyframes/episode_1/K01.png") -> KeyframeBoard:
    cell = KeyframeCell(
        id="K01",
        label="甲近景",
        scene="车内",
        staging="甲左乙右",
        framing="近景",
        characters=["甲"],
        unit_ids=["E1U10", "E1U11"],
        status=status,
        image=image,
    )
    return KeyframeBoard(episode=1, plan_fingerprint="x", sheets=[KeyframeSheet(id="S1", scene="车内", cells=[cell])])


def _write_image(project_path: Path, relpath: str = "keyframes/episode_1/K01.png") -> Path:
    path = project_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "gray").save(path)
    return path


@pytest.mark.parametrize(
    ("unit_id", "episode"), [("E1U10", 1), ("E12U03", 12), ("E1U08a", 1), ("U10", None), ("scene_1", None)]
)
def test_episode_of_unit_follows_the_unit_id_convention(unit_id: str, episode: int | None) -> None:
    assert episode_of_unit(unit_id) == episode


def test_an_approved_cell_with_an_image_is_returned_for_each_of_its_units(tmp_path: Path) -> None:
    image = _write_image(tmp_path)
    save_board(tmp_path, _board())

    for unit_id in ("E1U10", "E1U11"):
        found = approved_keyframe(tmp_path, {"unit_id": unit_id})
        assert found is not None
        cell, path = found
        assert cell.id == "K01"
        assert path == image


@pytest.mark.parametrize("unit", [{"unit_id": "E1U12"}, {"unit_id": "bad"}, {}, {"unit_id": 7}])
def test_units_outside_every_cell_have_no_keyframe(tmp_path: Path, unit: dict) -> None:
    _write_image(tmp_path)
    save_board(tmp_path, _board())

    assert approved_keyframe(tmp_path, unit) is None


def test_a_draft_cell_is_not_used(tmp_path: Path) -> None:
    _write_image(tmp_path)
    save_board(tmp_path, _board(status="draft"))

    assert approved_keyframe(tmp_path, {"unit_id": "E1U10"}) is None


def test_a_missing_image_file_means_no_keyframe(tmp_path: Path) -> None:
    save_board(tmp_path, _board())

    assert approved_keyframe(tmp_path, {"unit_id": "E1U10"}) is None


def test_a_missing_or_corrupt_board_means_no_keyframe(tmp_path: Path) -> None:
    assert approved_keyframe(tmp_path, {"unit_id": "E1U10"}) is None
    path = board_path(tmp_path, 1)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    assert approved_keyframe(tmp_path, {"unit_id": "E1U10"}) is None


def test_opening_frame_is_written_into_the_definition_and_the_first_shot() -> None:
    sections = {
        "subject_definitions": "@[甲] is the friend in <Picture @[甲]>.",
        "detailed_description": "Realistic style. [Shot 1] A close-up of @[甲]. [Shot 2] At 00:04.500, a cut to @[乙].",
    }

    patched = with_opening_frame(sections)

    name = KEYFRAME_REFERENCE_NAME
    assert patched["subject_definitions"].endswith(f"<Picture @[{name}]> is the opening frame of this unit.")
    assert patched["detailed_description"].startswith(
        f"Realistic style. [Shot 1] The shot begins from <Picture @[{name}]>"
    )
    assert "[Shot 2] At 00:04.500, a cut to @[乙]." in patched["detailed_description"]
    # 不改动入参
    assert "opening frame" not in sections["subject_definitions"]


def test_opening_frame_without_a_shot_marker_only_adds_the_definition() -> None:
    sections = {"subject_definitions": "@[甲] is the friend.", "detailed_description": "No shots here."}

    patched = with_opening_frame(sections)

    assert patched["detailed_description"] == "No shots here."
    assert "opening frame" in patched["subject_definitions"]
