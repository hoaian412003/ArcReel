"""起始帧联合图入队：看板与供应商校验、任务形态。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.keyframes.models import KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.store import save_board
from lib.project.project_manager import ProjectManager
from server.services.project.keyframe_plan import KeyframePlanError
from server.services.project.keyframe_sheets import enqueue_keyframe_sheet


class _Queue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"task_id": "task-1", "deduped": False}


async def _provider(_project: dict[str, Any]) -> str:
    return "runninghub"


@pytest.fixture
def keyframe_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "drama")
    return manager


def _seed_board(keyframe_pm: ProjectManager) -> None:
    cell = KeyframeCell(
        id="K01",
        label="近景",
        scene="车内",
        staging="s",
        framing="f",
        characters=["阿离"],
        unit_ids=["E1U01"],
    )
    save_board(
        keyframe_pm.get_project_path("demo"),
        KeyframeBoard(episode=1, plan_fingerprint="p", sheets=[KeyframeSheet(id="S1", scene="车内", cells=[cell])]),
    )


async def test_a_sheet_is_queued_as_a_promptless_image_task_on_the_i2i_provider(keyframe_pm: ProjectManager) -> None:
    _seed_board(keyframe_pm)
    queue = _Queue()

    result = await enqueue_keyframe_sheet(
        keyframe_pm, "demo", 1, "S1", user_id="u1", queue=queue, resolve_provider=_provider
    )

    assert result == {"task_id": "task-1", "deduped": False, "resource_id": "episode_1/S1"}
    (call,) = queue.calls
    assert call["task_type"] == "keyframe_sheet"
    assert call["media_type"] == "image"
    assert call["resource_id"] == "episode_1/S1"
    assert call["payload"] == {}
    assert call["provider_id"] == "runninghub"
    assert call["user_id"] == "u1"
    assert call["source"] == "webui"


async def test_no_board_means_no_task(keyframe_pm: ProjectManager) -> None:
    queue = _Queue()

    with pytest.raises(KeyframePlanError) as caught:
        await enqueue_keyframe_sheet(
            keyframe_pm, "demo", 1, "S1", user_id="u1", queue=queue, resolve_provider=_provider
        )

    assert caught.value.code == "board_missing"
    assert queue.calls == []


async def test_an_unknown_sheet_means_no_task(keyframe_pm: ProjectManager) -> None:
    _seed_board(keyframe_pm)
    queue = _Queue()

    with pytest.raises(KeyframePlanError) as caught:
        await enqueue_keyframe_sheet(
            keyframe_pm, "demo", 1, "S7", user_id="u1", queue=queue, resolve_provider=_provider
        )

    assert caught.value.code == "sheet_missing"
    assert queue.calls == []


async def test_an_unavailable_image_provider_means_no_task(keyframe_pm: ProjectManager) -> None:
    _seed_board(keyframe_pm)
    queue = _Queue()

    async def _none(_project: dict[str, Any]) -> str:
        raise KeyframePlanError("image_provider_unavailable", "x")

    with pytest.raises(KeyframePlanError) as caught:
        await enqueue_keyframe_sheet(keyframe_pm, "demo", 1, "S1", user_id="u1", queue=queue, resolve_provider=_none)

    assert caught.value.code == "image_provider_unavailable"
    assert queue.calls == []
