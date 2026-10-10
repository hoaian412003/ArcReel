"""起始帧联合图的提示词、参考图顺序与切分。"""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lib.keyframes.models import KeyframeCell, KeyframeSheet
from lib.keyframes.sheet import (
    KeyframeSheetError,
    build_sheet_prompt,
    face_reference_path,
    split_sheet,
)


def _figure(path: Path, color: tuple[int, int, int]) -> Path:
    """白底三栏图，正面栏里画头与肩，足够让裁脸走轮廓路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 30, 180, 110), fill=color)
    draw.rectangle((140, 110, 160, 130), fill=color)
    draw.rectangle((70, 130, 230, 480), fill=(30, 30, 30))
    image.save(path)
    return path


@pytest.fixture
def sheet_project(tmp_path: Path) -> dict:
    _figure(tmp_path / "characters" / "甲.png", (90, 60, 40))
    _figure(tmp_path / "characters" / "derivatives" / "甲" / "Face.png", (90, 60, 40))
    _figure(tmp_path / "characters" / "derivatives" / "乙" / "少年.png", (200, 170, 150))
    _figure(tmp_path / "characters" / "乙.png", (200, 170, 150))
    Image.new("RGB", (64, 64), (120, 130, 140)).save(tmp_path / "车内.png")
    return {
        "aspect_ratio": "16:9",
        "style": "写实",
        "style_description": "柔光",
        "characters": {
            "甲": {
                "description": "肤色偏深的胖少年，黑色短发",
                "character_sheet": "characters/甲.png",
                "derivatives": {
                    "Face": {"description": "脸部特写", "character_sheet": "characters/derivatives/甲/Face.png"}
                },
            },
            "乙": {
                "description": "成年人",
                "character_sheet": "characters/乙.png",
                "derivatives": {
                    "少年": {
                        "description": "同一个人的 18 岁形态",
                        "character_sheet": "characters/derivatives/乙/少年.png",
                    }
                },
            },
        },
        "scenes": {"车内": {"description": "旧公交车内", "scene_sheet": "车内.png"}},
    }


def _sheet(*cells: KeyframeCell) -> KeyframeSheet:
    return KeyframeSheet(id="S1", scene="车内", cells=list(cells))


def _cell(cell_id: str, characters: list[str], unit: str) -> KeyframeCell:
    return KeyframeCell(
        id=cell_id,
        label=f"{cell_id}格",
        scene="车内",
        staging="甲在左、乙在右靠窗",
        framing="甲的近景",
        characters=characters,
        unit_ids=[unit],
    )


def test_only_a_character_with_a_face_asset_gets_a_face_reference(sheet_project: dict, tmp_path: Path) -> None:
    sheet = _sheet(_cell("K01", ["甲", "乙/少年"], "U1"))

    built = build_sheet_prompt(sheet, project=sheet_project, project_path=tmp_path)

    assert [path.name for path in built.reference_images] == ["车内.png", "Face.png", "甲.png", "少年.png"]
    assert "Image 1: the scene “车内”" in built.prompt
    assert "Image 2: close-up of 甲's face" in built.prompt
    assert "Image 3: full-body turnaround of 甲. The reference for build, proportions and outfit." in built.prompt
    assert (
        "Image 4: full-body turnaround of 乙/少年. The reference for face, skin tone, hair, build, proportions and outfit."
        in built.prompt
    )
    assert [label[:7] for label in built.reference_labels] == [f"Image {n}" for n in range(1, 5)]


def test_extra_canvas_references_follow_the_planned_ones_and_skip_duplicates(
    sheet_project: dict, tmp_path: Path
) -> None:
    sheet = _sheet(_cell("K01", ["甲"], "U1"))
    extra = _figure(tmp_path / "han35.png", (9, 9, 9))
    planned = next(tmp_path.rglob("甲.png"))

    built = build_sheet_prompt(
        sheet,
        project=sheet_project,
        project_path=tmp_path,
        extra_references=[(extra, "Hán Thăng, 35 tuổi."), (planned, "duplicate")],
    )

    assert [path.name for path in built.reference_images][-1] == "han35.png"
    assert built.reference_images.count(planned) == 1
    assert f"Image {len(built.reference_images)}: Hán Thăng, 35 tuổi." in built.prompt


def test_prompt_is_english_and_points_each_panel_at_its_reference_images(sheet_project: dict, tmp_path: Path) -> None:
    sheet = _sheet(_cell("K01", ["甲", "乙/少年"], "U1"), _cell("K02", ["甲"], "U2"))

    built = build_sheet_prompt(sheet, project=sheet_project, project_path=tmp_path)

    assert "甲在左、乙在右靠窗" in built.prompt
    assert (
        "- 甲: face from Image 2, build and outfit from Image 3. Appearance: 肤色偏深的胖少年，黑色短发" in built.prompt
    )
    assert "- 乙/少年: face, build and outfit from Image 4." in built.prompt
    assert (
        "People: 甲 (face: Image 2, build and outfit: Image 3); 乙/少年 (face, build and outfit: Image 4)"
        in built.prompt
    )
    assert "Panel 2 (row 1, column 2)" in built.prompt
    assert built.prompt.count("unused") == 2  # 2 格内容，其余 2 格留灰
    assert "Appearance: 成年人" not in built.prompt  # 衍生形态不带本体描述，长相与年龄随它自己的全身图
    assert built.aspect_ratio == "16:9"


def test_a_character_without_a_face_asset_is_referenced_by_the_whole_figure_only(
    sheet_project: dict, tmp_path: Path
) -> None:
    sheet = _sheet(_cell("K01", ["乙"], "U1"))

    built = build_sheet_prompt(sheet, project=sheet_project, project_path=tmp_path)

    assert [path.name for path in built.reference_images] == ["车内.png", "乙.png"]
    assert "close-up" not in built.prompt


def test_a_registered_face_without_an_image_falls_back_to_the_whole_figure(sheet_project: dict, tmp_path: Path) -> None:
    (tmp_path / "characters" / "derivatives" / "甲" / "Face.png").unlink()

    built = build_sheet_prompt(_sheet(_cell("K01", ["甲"], "U1")), project=sheet_project, project_path=tmp_path)

    assert [path.name for path in built.reference_images] == ["车内.png", "甲.png"]


def test_a_derivative_look_never_borrows_the_base_face(sheet_project: dict, tmp_path: Path) -> None:
    sheet_project["characters"]["甲"]["derivatives"]["少年"] = {
        "description": "年轻形态",
        "character_sheet": "characters/derivatives/乙/少年.png",
    }

    assert face_reference_path(sheet_project, tmp_path, "甲/少年") is None
    assert face_reference_path(sheet_project, tmp_path, "甲") == tmp_path / "characters/derivatives/甲/Face.png"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p["scenes"].pop("车内"), "keyframe_scene_sheet_missing"),
        (lambda p: p["characters"].pop("甲"), "keyframe_character_missing"),
        (
            lambda p: p["characters"]["甲"].update(character_sheet="characters/没有.png"),
            "keyframe_character_sheet_missing",
        ),
    ],
)
def test_missing_materials_are_named(sheet_project: dict, tmp_path: Path, mutate, code: str) -> None:
    mutate(sheet_project)

    with pytest.raises(KeyframeSheetError) as caught:
        build_sheet_prompt(_sheet(_cell("K01", ["甲"], "U1")), project=sheet_project, project_path=tmp_path)

    assert caught.value.code == code


def _grid(path: Path, *, gray_cells: tuple[int, ...] = ()) -> Path:
    image = Image.new("RGB", (400, 225), "white")
    draw = ImageDraw.Draw(image)
    for index in range(4):
        x, y = (index % 2) * 200, (index // 2) * 112
        if index in gray_cells:
            draw.rectangle((x, y, x + 199, y + 111), fill=(100, 100, 100))
        else:
            for step in range(0, 200, 20):
                draw.rectangle((x + step, y, x + step + 9, y + 111), fill=(40 + index * 50, 120, 200 - index * 30))
    image.save(path)
    return path


def test_split_takes_the_first_cells_in_reading_order(tmp_path: Path) -> None:
    cells = split_sheet(_grid(tmp_path / "grid.png"), 3, "16:9")

    assert len(cells) == 3
    assert all(abs(cell.width / cell.height - 16 / 9) < 0.05 for cell in cells)


def test_a_gray_placeholder_among_the_taken_cells_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(KeyframeSheetError) as caught:
        split_sheet(_grid(tmp_path / "grid.png", gray_cells=(1,)), 3, "16:9")

    assert caught.value.code == "keyframe_cell_empty"
    assert caught.value.name == "2"


def test_a_gray_cell_after_the_taken_ones_is_ignored(tmp_path: Path) -> None:
    cells = split_sheet(_grid(tmp_path / "grid.png", gray_cells=(3,)), 3, "16:9")

    assert len(cells) == 3
