"""联合图落格：切分、写格子图、重置审核；灰色占位整张作废。"""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lib.keyframes.landing import board_cells_missing_images, land_sheet, sheet_basis
from lib.keyframes.models import KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.sheet import KeyframeSheetError, SheetPrompt
from lib.keyframes.store import load_board, save_board


def _cell(cell_id: str, unit: str, **extra: object) -> KeyframeCell:
    fields: dict[str, object] = {
        "id": cell_id,
        "label": cell_id,
        "scene": "车内",
        "staging": "s",
        "framing": "f",
        "characters": ["甲"],
        "unit_ids": [unit],
    }
    fields.update(extra)
    return KeyframeCell.model_validate(fields)


def _board(project_path: Path, *cells: KeyframeCell) -> None:
    save_board(
        project_path,
        KeyframeBoard(
            episode=1, plan_fingerprint="p", sheets=[KeyframeSheet(id="S1", scene="车内", cells=list(cells))]
        ),
    )


def _grid(path: Path, *, gray: tuple[int, ...] = ()) -> Path:
    image = Image.new("RGB", (400, 225), "white")
    draw = ImageDraw.Draw(image)
    for index in range(4):
        x, y = (index % 2) * 200, (index // 2) * 112
        if index in gray:
            draw.rectangle((x, y, x + 199, y + 111), fill=(100, 100, 100))
        else:
            for step in range(0, 200, 20):
                draw.rectangle((x + step, y, x + step + 9, y + 111), fill=(40 + index * 50, 120, 200 - index * 30))
    image.save(path)
    return path


def test_landing_writes_one_image_per_cell_and_marks_them_for_review(tmp_path: Path) -> None:
    _board(tmp_path, _cell("K01", "U1"), _cell("K02", "U2"))
    sheet = _grid(tmp_path / "S1.png")

    landed = land_sheet(tmp_path, 1, "S1", sheet, aspect_ratio="16:9", basis="b1")

    assert landed == ["K01", "K02"]
    board = load_board(tmp_path, 1)
    assert board is not None
    assert [(c.status, c.image, c.basis) for c in board.cells()] == [
        ("draft", "keyframes/episode_1/K01.png", "b1"),
        ("draft", "keyframes/episode_1/K02.png", "b1"),
    ]
    assert board.sheets[0].image == "keyframes/episode_1/S1.png"
    assert (tmp_path / "keyframes/episode_1/K02.png").is_file()


def test_landing_a_regenerated_sheet_resets_an_approved_cell_to_draft(tmp_path: Path) -> None:
    _board(tmp_path, _cell("K01", "U1", status="approved", image="keyframes/episode_1/K01.png", basis="old"))

    land_sheet(tmp_path, 1, "S1", _grid(tmp_path / "S1.png"), aspect_ratio="16:9", basis="new")

    board = load_board(tmp_path, 1)
    assert board is not None
    assert board.cells()[0].status == "draft"
    assert board.cells()[0].basis == "new"


def test_a_gray_placeholder_among_the_taken_cells_leaves_the_board_untouched(tmp_path: Path) -> None:
    _board(tmp_path, _cell("K01", "U1"), _cell("K02", "U2"))
    before = load_board(tmp_path, 1)

    with pytest.raises(KeyframeSheetError) as caught:
        land_sheet(tmp_path, 1, "S1", _grid(tmp_path / "S1.png", gray=(1,)), aspect_ratio="16:9", basis="b")

    assert caught.value.code == "keyframe_cell_empty"
    assert load_board(tmp_path, 1) == before
    assert not (tmp_path / "keyframes").exists()


def test_a_sheet_the_board_no_longer_has_is_refused(tmp_path: Path) -> None:
    _board(tmp_path, _cell("K01", "U1"))

    with pytest.raises(KeyframeSheetError) as caught:
        land_sheet(tmp_path, 1, "S9", _grid(tmp_path / "S1.png"), aspect_ratio="16:9", basis="b")

    assert caught.value.code == "keyframe_sheet_missing"


def test_a_missing_board_is_refused(tmp_path: Path) -> None:
    with pytest.raises(KeyframeSheetError) as caught:
        land_sheet(tmp_path, 1, "S1", _grid(tmp_path / "S1.png"), aspect_ratio="16:9", basis="b")

    assert caught.value.code == "keyframe_board_missing"


def test_basis_follows_the_prompt_and_the_reference_bytes(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    Image.new("RGB", (4, 4), "red").save(ref)
    built = SheetPrompt(prompt="p", reference_images=(ref,), aspect_ratio="16:9")
    first = sheet_basis(built)

    assert sheet_basis(built) == first
    assert sheet_basis(SheetPrompt(prompt="changed", reference_images=(ref,), aspect_ratio="16:9")) != first
    Image.new("RGB", (4, 4), "blue").save(ref)
    assert sheet_basis(built) != first


def test_cells_missing_images_are_listed(tmp_path: Path) -> None:
    _board(tmp_path, _cell("K01", "U1"), _cell("K02", "U2", status="draft", image="keyframes/episode_1/K02.png"))
    board = load_board(tmp_path, 1)
    assert board is not None

    assert list(board_cells_missing_images(board)) == ["K01"]


def test_a_sheet_that_ignores_the_requested_ratio_is_refused_and_the_board_stays_untouched(tmp_path: Path) -> None:
    _board(tmp_path, _cell("K01", "U1"))
    before = load_board(tmp_path, 1)
    square = tmp_path / "S1.png"
    Image.new("RGB", (512, 512), "white").save(square)

    with pytest.raises(KeyframeSheetError) as caught:
        land_sheet(tmp_path, 1, "S1", square, aspect_ratio="16:9", basis="b")

    assert caught.value.code == "keyframe_sheet_wrong_ratio"
    assert load_board(tmp_path, 1) == before
    assert not (tmp_path / "keyframes").exists()


def test_a_ratio_within_tolerance_is_accepted(tmp_path: Path) -> None:
    _board(tmp_path, _cell("K01", "U1"))
    sheet = _grid(tmp_path / "S1.png")  # 400×225 ≈ 16:9

    assert land_sheet(tmp_path, 1, "S1", sheet, aspect_ratio="16:9", basis="b") == ["K01"]
