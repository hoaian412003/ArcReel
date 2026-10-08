"""起始帧路由：看板状态、过期与可生成判定、人工审核、改挂单元、入队错误映射。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from lib.keyframes.landing import sheet_basis
from lib.keyframes.models import KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.sheet import build_sheet_prompt
from lib.keyframes.store import load_board, save_board
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import keyframes as keyframes_router
from server.services.project.keyframe_plan import KeyframePlanError
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

BASE = "/api/v1/projects/demo/episodes/1/keyframes"

_UNITS = [
    {"unit_id": "E1U01", "text": "@[车内] @[阿离] 看向 @[阿宁]。"},
    {"unit_id": "E1U02", "text": "@[车内] @[阿宁] 回头。"},
    {"unit_id": "E1U03", "text": "@[车内] @[阿离] 站起身。"},
]


def _figure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 30, 180, 110), fill=(90, 60, 40))
    draw.rectangle((140, 110, 160, 130), fill=(90, 60, 40))
    draw.rectangle((70, 130, 230, 480), fill=(30, 30, 30))
    image.save(path)


def _cell(cell_id: str, units: list[str], characters: list[str], **extra: Any) -> KeyframeCell:
    fields: dict[str, Any] = {
        "id": cell_id,
        "label": f"格 {cell_id}",
        "scene": "车内",
        "staging": "阿离在左、阿宁在右靠窗",
        "framing": "近景",
        "characters": characters,
        "unit_ids": units,
    }
    fields.update(extra)
    return KeyframeCell.model_validate(fields)


@pytest.fixture
def board_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "drama")
    manager.add_character("demo", "阿离", "少女，黑色短发")
    manager.add_character("demo", "阿宁", "少年，白T恤")
    manager.add_scenes_batch("demo", {"车内": {"description": "旧公交车内"}})
    manager.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    path = manager.get_project_path("demo")
    _figure(path / "characters" / "阿离.png")
    _figure(path / "characters" / "阿宁.png")
    Image.new("RGB", (64, 64), (120, 130, 140)).save(path / "scenes" / "车内.png")

    def _settings(project: dict[str, Any]) -> None:
        project["generation_mode"] = "reference_video"
        project["video_prompt_profile"] = "h3_ref2va"
        project["aspect_ratio"] = "16:9"
        project["characters"]["阿离"]["character_sheet"] = "characters/阿离.png"
        project["characters"]["阿宁"]["character_sheet"] = "characters/阿宁.png"
        project["scenes"]["车内"]["scene_sheet"] = "scenes/车内.png"

    manager.update_project("demo", _settings)
    manager.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "drama",
            "video_units": [{**unit, "duration_seconds": 8, "pending_authoring": True} for unit in _UNITS],
        },
        "episode_1.json",
    )
    return manager


@pytest.fixture
def keyframes_client(board_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(keyframes_router, "get_project_manager", lambda: board_pm)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(keyframes_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def _seed(board_pm: ProjectManager, *cells: KeyframeCell, fingerprint: str | None = None) -> Path:
    from lib.keyframes.planning import plan_fingerprint

    path = board_pm.get_project_path("demo")
    save_board(
        path,
        KeyframeBoard(
            episode=1,
            plan_fingerprint=fingerprint or plan_fingerprint(_UNITS),
            sheets=[KeyframeSheet(id="S1", scene="车内", cells=list(cells))],
        ),
    )
    return path


def _with_image(board_pm: ProjectManager, cell_id: str, units: list[str], characters: list[str], **extra: Any):
    """一格已有图，依据与当前素材一致（即未过期）。"""
    path = board_pm.get_project_path("demo")
    relative = f"keyframes/episode_1/{cell_id}.png"
    (path / "keyframes" / "episode_1").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "gray").save(path / relative)
    probe = KeyframeSheet(id="S1", scene="车内", cells=[_cell(cell_id, units, characters)])
    basis = sheet_basis(build_sheet_prompt(probe, project=board_pm.load_project("demo"), project_path=path))
    return _cell(cell_id, units, characters, status="draft", image=relative, basis=basis, **extra)


def test_no_board_yet_reports_the_units_without_cells(keyframes_client: TestClient) -> None:
    state = keyframes_client.get(BASE).json()

    assert state["applicable"] is True
    assert state["has_board"] is False
    assert state["sheets"] == []
    assert [(u["unit_id"], u["cell_id"]) for u in state["units"]] == [(u["unit_id"], None) for u in _UNITS]


def test_a_project_outside_the_h3_reference_route_is_not_applicable(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    board_pm.update_project("demo", lambda p: p.__setitem__("generation_mode", "storyboard"))

    assert keyframes_client.get(BASE).json() == {"applicable": False, "episode": 1}


def test_the_state_maps_units_to_cells_and_flags_a_changed_plan(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    _seed(board_pm, _cell("K01", ["E1U01", "E1U03"], ["阿离", "阿宁"]), fingerprint="old")

    state = keyframes_client.get(BASE).json()

    assert state["plan_stale"] is True
    assert {u["unit_id"]: u["cell_id"] for u in state["units"]} == {"E1U01": "K01", "E1U02": None, "E1U03": "K01"}
    (sheet,) = state["sheets"]
    assert sheet["problem"] is None
    assert [c["id"] for c in sheet["cells"]] == ["K01"]


def test_a_sheet_whose_materials_are_missing_says_which_one(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    path = _seed(board_pm, _cell("K01", ["E1U01"], ["阿离", "阿宁"]))
    (path / "scenes" / "车内.png").unlink()

    (sheet,) = keyframes_client.get(BASE).json()["sheets"]

    assert sheet["problem"] == {"code": "keyframe_scene_sheet_missing", "name": "车内"}


def test_the_sheet_request_lists_the_prompt_and_numbered_references(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    _seed(board_pm, _cell("K01", ["E1U01"], ["阿离"]))

    body = keyframes_client.get(f"{BASE}/sheets/S1/request").json()

    assert body["problem"] is None
    assert body["aspect_ratio"] == "16:9"
    assert "阿离" in body["prompt"]
    assert [(ref["label"][:7], ref["path"]) for ref in body["references"]] == [
        ("Image 1", "scenes/车内.png"),
        ("Image 2", "characters/阿离.png"),
    ]
    assert "face, build and outfit from Image 2" in body["prompt"]


def test_the_sheet_request_names_the_missing_material_instead_of_failing(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    path = _seed(board_pm, _cell("K01", ["E1U01"], ["阿离"]))
    (path / "scenes" / "车内.png").unlink()

    body = keyframes_client.get(f"{BASE}/sheets/S1/request").json()

    assert body == {"sheet_id": "S1", "problem": {"code": "keyframe_scene_sheet_missing", "name": "车内"}}


def test_the_request_of_an_unknown_sheet_is_not_found(keyframes_client: TestClient, board_pm: ProjectManager) -> None:
    _seed(board_pm, _cell("K01", ["E1U01"], ["阿离"]))

    assert keyframes_client.get(f"{BASE}/sheets/S9/request").status_code == 404


def test_a_cell_is_stale_once_the_reference_images_change(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    cell = _with_image(board_pm, "K01", ["E1U01"], ["阿离"])
    path = _seed(board_pm, cell)
    assert keyframes_client.get(BASE).json()["sheets"][0]["cells"][0]["stale"] is False

    Image.new("RGB", (64, 64), (1, 2, 3)).save(path / "scenes" / "车内.png")

    assert keyframes_client.get(BASE).json()["sheets"][0]["cells"][0]["stale"] is True


def test_approving_a_fresh_cell_persists_and_unapproving_returns_it_to_draft(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    path = _seed(board_pm, _with_image(board_pm, "K01", ["E1U01"], ["阿离"]))

    approved = keyframes_client.post(f"{BASE}/cells/K01/review", json={"approved": True})
    assert approved.status_code == 200
    assert approved.json()["sheets"][0]["cells"][0]["status"] == "approved"
    board = load_board(path, 1)
    assert board is not None
    assert board.cells()[0].status == "approved"

    back = keyframes_client.post(f"{BASE}/cells/K01/review", json={"approved": False})
    assert back.json()["sheets"][0]["cells"][0]["status"] == "draft"


def test_approve_all_takes_the_fresh_cells(keyframes_client: TestClient, board_pm: ProjectManager) -> None:
    path = _seed(board_pm, _with_image(board_pm, "K01", ["E1U01"], ["阿离"]))

    body = keyframes_client.post(f"{BASE}/approve-all").json()

    assert [c["status"] for sheet in body["sheets"] for c in sheet["cells"]] == ["approved"]
    board = load_board(path, 1)
    assert board is not None
    assert board.cells()[0].status == "approved"


def test_approve_all_leaves_cells_without_an_image_untouched(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    _seed(board_pm, _cell("K01", ["E1U01"], ["阿离"]))

    body = keyframes_client.post(f"{BASE}/approve-all").json()

    assert [c["status"] for sheet in body["sheets"] for c in sheet["cells"]] == ["pending"]


def test_approve_all_without_a_board_is_refused(keyframes_client: TestClient) -> None:
    assert keyframes_client.post(f"{BASE}/approve-all").status_code == 409


def test_a_cell_without_an_image_cannot_be_approved(keyframes_client: TestClient, board_pm: ProjectManager) -> None:
    _seed(board_pm, _cell("K01", ["E1U01"], ["阿离"]))

    response = keyframes_client.post(f"{BASE}/cells/K01/review", json={"approved": True})

    assert response.status_code == 409
    assert "K01" in response.json()["detail"]


def test_a_stale_cell_cannot_be_approved(keyframes_client: TestClient, board_pm: ProjectManager) -> None:
    cell = _with_image(board_pm, "K01", ["E1U01"], ["阿离"])
    path = _seed(board_pm, cell)
    Image.new("RGB", (64, 64), (1, 2, 3)).save(path / "scenes" / "车内.png")

    response = keyframes_client.post(f"{BASE}/cells/K01/review", json={"approved": True})

    assert response.status_code == 409


def test_unknown_cells_are_not_found(keyframes_client: TestClient, board_pm: ProjectManager) -> None:
    _seed(board_pm, _cell("K01", ["E1U01"], ["阿离"]))

    assert keyframes_client.post(f"{BASE}/cells/K99/review", json={"approved": True}).status_code == 404


def test_moving_a_unit_to_another_cell_drops_a_cell_left_empty(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    path = _seed(board_pm, _cell("K01", ["E1U01"], ["阿离"]), _cell("K02", ["E1U03"], ["阿离"]))

    response = keyframes_client.put(f"{BASE}/units/E1U03/cell", json={"cell_id": "K01"})

    assert response.status_code == 200
    board = load_board(path, 1)
    assert board is not None
    assert [(c.id, c.unit_ids) for c in board.cells()] == [("K01", ["E1U01", "E1U03"])]


def test_clearing_a_unit_cell_detaches_it(keyframes_client: TestClient, board_pm: ProjectManager) -> None:
    path = _seed(board_pm, _cell("K01", ["E1U01", "E1U03"], ["阿离", "阿宁"]))

    response = keyframes_client.put(f"{BASE}/units/E1U03/cell", json={"cell_id": None})

    assert response.status_code == 200
    board = load_board(path, 1)
    assert board is not None
    assert board.cells()[0].unit_ids == ["E1U01"]
    assert {u["unit_id"]: u["cell_id"] for u in response.json()["units"]}["E1U03"] is None


def test_a_unit_cannot_take_a_cell_whose_characters_it_does_not_mention(
    keyframes_client: TestClient, board_pm: ProjectManager
) -> None:
    _seed(board_pm, _cell("K01", ["E1U01"], ["阿离", "阿宁"]))

    response = keyframes_client.put(f"{BASE}/units/E1U03/cell", json={"cell_id": "K01"})

    assert response.status_code == 422


def test_assignment_errors_for_unknown_unit_and_cell(keyframes_client: TestClient, board_pm: ProjectManager) -> None:
    _seed(board_pm, _cell("K01", ["E1U01"], ["阿离"]))

    assert keyframes_client.put(f"{BASE}/units/E1U99/cell", json={"cell_id": "K01"}).status_code == 404
    assert keyframes_client.put(f"{BASE}/units/E1U01/cell", json={"cell_id": "K99"}).status_code == 404


def test_generate_queues_the_sheet_and_returns_the_task(
    keyframes_client: TestClient, board_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def _fake(pm, project_name, episode, sheet_id, *, user_id, source):
        seen.update(project=project_name, episode=episode, sheet=sheet_id, source=source)
        return {"task_id": "task-9", "deduped": False, "resource_id": "episode_1/S1"}

    monkeypatch.setattr(keyframes_router, "enqueue_keyframe_sheet", _fake)

    response = keyframes_client.post(f"{BASE}/sheets/S1/generate")

    assert response.status_code == 200
    assert response.json()["task_id"] == "task-9"
    assert seen == {"project": "demo", "episode": 1, "sheet": "S1", "source": "webui"}


@pytest.mark.parametrize(
    ("code", "status"),
    [("board_missing", 409), ("sheet_missing", 404), ("image_provider_unavailable", 409)],
)
def test_generate_maps_domain_errors(
    keyframes_client: TestClient, monkeypatch: pytest.MonkeyPatch, code: str, status: int
) -> None:
    async def _refuse(*_args: Any, **_kwargs: Any):
        raise KeyframePlanError(code, "x")

    monkeypatch.setattr(keyframes_router, "enqueue_keyframe_sheet", _refuse)

    assert keyframes_client.post(f"{BASE}/sheets/S1/generate").status_code == status
