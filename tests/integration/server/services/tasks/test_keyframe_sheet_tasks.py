"""起始帧联合图的生成任务：出站请求形态、落格、付费前的拒绝。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from lib.keyframes.models import KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.sheet import KeyframeSheetError
from lib.keyframes.store import load_board, save_board
from lib.project.project_manager import ProjectManager
from server.services.tasks import keyframe_sheet_tasks
from server.services.tasks.keyframe_sheet_tasks import MAX_RATIO_ATTEMPTS, execute_keyframe_sheet_task
from tests.integration.server.services.tasks.generation_tasks_support import fake_resolve_ctx


class _Generator:
    """记录收到的请求，并像真实后端一样把一张 2×2 联合图写到约定的落盘路径。"""

    def __init__(self, project_path: Path, *, square_first: int = 0) -> None:
        self.project_path = project_path
        self.calls: list[dict[str, Any]] = []
        #: 前几次调用出方图（模型忽略比例参数），之后才按要求的 16:9 出图。
        self.square_first = square_first

    async def generate_image_async(self, **kwargs: Any) -> tuple[Path, int]:
        self.calls.append(kwargs)
        output = self.project_path / "keyframes" / "episode_1" / "S1.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        if len(self.calls) <= self.square_first:
            Image.new("RGB", (300, 300), "white").save(output)
            return output, len(self.calls)
        image = Image.new("RGB", (400, 225), "white")
        draw = ImageDraw.Draw(image)
        for index in range(4):
            x, y = (index % 2) * 200, (index // 2) * 112
            for step in range(0, 200, 20):
                draw.rectangle((x + step, y, x + step + 9, y + 111), fill=(40 + index * 50, 120, 200 - index * 30))
        image.save(output)
        return output, 3


def _figure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 30, 180, 110), fill=(90, 60, 40))
    draw.rectangle((140, 110, 160, 130), fill=(90, 60, 40))
    draw.rectangle((70, 130, 230, 480), fill=(30, 30, 30))
    image.save(path)


@pytest.fixture
def keyframe_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "drama")
    manager.add_character("demo", "阿离", "少女，黑色短发")
    manager.add_scenes_batch("demo", {"车内": {"description": "旧公交车内"}})
    path = manager.get_project_path("demo")
    _figure(path / "characters" / "阿离.png")
    Image.new("RGB", (64, 64), (120, 130, 140)).save(path / "scenes" / "车内.png")

    def _sheets(project: dict[str, Any]) -> None:
        project["characters"]["阿离"]["character_sheet"] = "characters/阿离.png"
        project["scenes"]["车内"]["scene_sheet"] = "scenes/车内.png"
        project["aspect_ratio"] = "16:9"

    manager.update_project("demo", _sheets)
    save_board(
        path,
        KeyframeBoard(
            episode=1,
            plan_fingerprint="p",
            sheets=[
                KeyframeSheet(
                    id="S1",
                    scene="车内",
                    cells=[
                        KeyframeCell(
                            id=f"K0{n}",
                            label=f"格{n}",
                            scene="车内",
                            staging="阿离在左",
                            framing="近景",
                            characters=["阿离"],
                            unit_ids=[f"E1U0{n}"],
                        )
                        for n in (1, 2)
                    ],
                )
            ],
        ),
    )
    return manager


def _wire(monkeypatch: pytest.MonkeyPatch, keyframe_pm: ProjectManager, generator: _Generator, **lane: Any) -> None:
    monkeypatch.setattr(keyframe_sheet_tasks, "get_project_manager", lambda: keyframe_pm)
    monkeypatch.setattr(keyframe_sheet_tasks, "resolve_generation_context", fake_resolve_ctx(generator, **lane))


async def test_one_task_generates_the_sheet_and_lands_every_cell(
    keyframe_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = keyframe_pm.get_project_path("demo")
    generator = _Generator(path)
    _wire(monkeypatch, keyframe_pm, generator, image_max_reference_images=10)

    result = await execute_keyframe_sheet_task("demo", "episode_1/S1", {}, task_id="t1")

    assert result["cells"] == ["K01", "K02"]
    assert result["version"] == 3
    assert result["file_path"] == "keyframes/episode_1/S1.png"
    (call,) = generator.calls
    assert call["resource_type"] == "keyframe_sheets"
    assert call["resource_id"] == "episode_1/S1"
    assert call["aspect_ratio"] == "16:9"
    assert call["image_size"] == "2K"
    assert call["task_id"] == "t1"
    assert [Path(ref).name for ref in call["reference_images"]] == ["车内.png", "阿离.png"]
    assert "IDENTITY (highest priority)" in call["prompt"]
    board = load_board(path, 1)
    assert board is not None
    assert [(c.status, c.image) for c in board.cells()] == [
        ("draft", "keyframes/episode_1/K01.png"),
        ("draft", "keyframes/episode_1/K02.png"),
    ]
    assert (path / "keyframes" / "episode_1" / "K01.png").is_file()


async def test_the_backend_resolution_wins_over_the_fallback(
    keyframe_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = _Generator(keyframe_pm.get_project_path("demo"))
    _wire(monkeypatch, keyframe_pm, generator, image_resolution="4K")

    await execute_keyframe_sheet_task("demo", "episode_1/S1", {})

    assert generator.calls[0]["image_size"] == "4K"


async def test_too_many_references_fail_before_any_paid_request(
    keyframe_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = _Generator(keyframe_pm.get_project_path("demo"))
    _wire(monkeypatch, keyframe_pm, generator, image_max_reference_images=1)

    with pytest.raises(ValueError, match="超出图像模型上限 1"):
        await execute_keyframe_sheet_task("demo", "episode_1/S1", {})

    assert generator.calls == []


async def test_a_missing_character_sheet_fails_before_any_paid_request(
    keyframe_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = _Generator(keyframe_pm.get_project_path("demo"))
    _wire(monkeypatch, keyframe_pm, generator)
    (keyframe_pm.get_project_path("demo") / "characters" / "阿离.png").unlink()

    with pytest.raises(KeyframeSheetError) as caught:
        await execute_keyframe_sheet_task("demo", "episode_1/S1", {})

    assert caught.value.code == "keyframe_character_sheet_missing"
    assert generator.calls == []


@pytest.mark.parametrize(
    ("resource_id", "needle"),
    [
        ("episode_1/S9", "没有联合图 S9"),
        ("episode_2/S1", "第 2 集还没有起始帧看板"),
        ("bogus", "无效的起始帧联合图资源 id"),
    ],
)
async def test_unknown_sheets_and_episodes_are_refused(
    keyframe_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch, resource_id: str, needle: str
) -> None:
    generator = _Generator(keyframe_pm.get_project_path("demo"))
    _wire(monkeypatch, keyframe_pm, generator)

    with pytest.raises(ValueError, match=needle):
        await execute_keyframe_sheet_task("demo", resource_id, {})

    assert generator.calls == []


async def test_a_square_sheet_is_regenerated_until_the_ratio_is_right(
    keyframe_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = _Generator(keyframe_pm.get_project_path("demo"), square_first=2)
    _wire(monkeypatch, keyframe_pm, generator)

    result = await execute_keyframe_sheet_task("demo", "episode_1/S1", {})

    assert len(generator.calls) == 3
    assert result["attempts"] == 3
    assert result["cells"] == ["K01", "K02"]


async def test_a_sheet_that_stays_square_fails_after_the_last_attempt_and_leaves_the_board_alone(
    keyframe_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = keyframe_pm.get_project_path("demo")
    generator = _Generator(path, square_first=99)
    _wire(monkeypatch, keyframe_pm, generator)

    with pytest.raises(KeyframeSheetError) as caught:
        await execute_keyframe_sheet_task("demo", "episode_1/S1", {})

    assert caught.value.code == "keyframe_sheet_wrong_ratio"
    assert len(generator.calls) == MAX_RATIO_ATTEMPTS
    board = load_board(path, 1)
    assert board is not None
    assert [cell.status for cell in board.cells()] == ["pending", "pending"]
