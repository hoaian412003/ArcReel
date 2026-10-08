"""起始帧看板的模型约束与落盘读写。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.keyframes.models import KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.store import board_path, cell_image_relpath, load_board, save_board, sheet_image_relpath


def _cell(cell_id: str = "K01", units: list[str] | None = None, **overrides: object) -> KeyframeCell:
    fields: dict[str, object] = {
        "id": cell_id,
        "label": "甲近景",
        "scene": "车内",
        "staging": "甲左乙右",
        "framing": "近景",
        "characters": ["甲"],
        "unit_ids": units or ["E1U01"],
    }
    fields.update(overrides)
    return KeyframeCell.model_validate(fields)


def test_a_cell_with_an_image_state_must_have_an_image() -> None:
    with pytest.raises(ValidationError, match="没有图"):
        _cell(status="draft")


def test_a_sheet_holds_at_most_four_cells_from_one_scene() -> None:
    with pytest.raises(ValidationError):
        KeyframeSheet(id="S1", scene="车内", cells=[_cell(f"K{i:02d}", [f"U{i}"]) for i in range(1, 6)])
    with pytest.raises(ValidationError, match="不属于场景"):
        KeyframeSheet(id="S1", scene="站台", cells=[_cell()])


def test_a_unit_belongs_to_at_most_one_cell_on_the_board() -> None:
    sheet = KeyframeSheet(id="S1", scene="车内", cells=[_cell("K01", ["U1"]), _cell("K02", ["U1"])])

    with pytest.raises(ValidationError, match="同时属于"):
        KeyframeBoard(episode=1, plan_fingerprint="x", sheets=[sheet])


def test_board_finds_the_cell_and_sheet_of_a_unit() -> None:
    sheet = KeyframeSheet(id="S1", scene="车内", cells=[_cell("K01", ["U1"]), _cell("K02", ["U2"])])
    board = KeyframeBoard(episode=1, plan_fingerprint="x", sheets=[sheet])

    found = board.cell_for_unit("U2")
    assert found is not None
    assert found.id == "K02"
    assert board.cell_for_unit("U9") is None
    assert board.sheet_of("K02") == sheet


def test_board_round_trips_through_the_draft_file(tmp_path: Path) -> None:
    sheet = KeyframeSheet(id="S1", scene="车内", cells=[_cell()])
    board = KeyframeBoard(episode=2, plan_fingerprint="abc", sheets=[sheet])

    path = save_board(tmp_path, board)

    assert path == board_path(tmp_path, 2)
    assert path.parent.name == "episode_2"
    assert load_board(tmp_path, 2) == board
    assert not list(path.parent.glob(".keyframes.*"))


def test_missing_board_reads_as_none_and_corrupt_board_raises(tmp_path: Path) -> None:
    assert load_board(tmp_path, 1) is None
    path = board_path(tmp_path, 1)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="无法解析"):
        load_board(tmp_path, 1)


def test_image_paths_live_under_the_episode_keyframes_directory() -> None:
    assert sheet_image_relpath(3, "S1") == "keyframes/episode_3/S1.png"
    assert cell_image_relpath(3, "K07") == "keyframes/episode_3/K07.png"
