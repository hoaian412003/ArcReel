"""画布工具：Agent 经工具读 / 编辑画布并运行节点，规则与网页共用 CanvasService。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.project.project_manager import ProjectManager
from server.agent_toolset import canvas as canvas_tools
from server.services.project import canvas_service
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolRequest
from tests.fakes import FakeConfigResolver

_CALLER = CallerContext(user_id="default", source="mcp")

_UNITS = [
    {"unit_id": "E1U01", "text": "阿离看向阿宁。", "duration_seconds": 8},
    {"unit_id": "E1U02", "text": "阿宁回头。", "duration_seconds": 8},
]


class _Queue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}"}


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Services:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")

    def _settings(project: dict[str, Any]) -> None:
        project["generation_mode"] = "reference_video"
        project["video_prompt_profile"] = "h3_ref2va"

    pm.update_project("demo", _settings)
    pm.save_script(
        "demo",
        {"episode": 1, "title": "第一集", "content_mode": "drama", "video_units": _UNITS},
        "episode_1.json",
    )
    queue = _Queue()
    monkeypatch.setattr(canvas_tools, "get_generation_queue", lambda: queue)
    services = Services(projects=pm, workflow_planner=WorkflowPlanner(pm), capabilities=FakeConfigResolver())
    _QUEUES[id(services)] = queue
    return services


_QUEUES: dict[int, _Queue] = {}


def _queue(ctx: Services) -> _Queue:
    return _QUEUES[id(ctx)]


def _scope(ctx: Services) -> ProjectScope:
    return ProjectScope(project_name="demo", data_root=ctx.projects.data_root)


async def _call(handler, request, ctx: Services):
    return await handler(ToolRequest(request), _scope(ctx), _CALLER, ctx)


async def _canvas(ctx: Services) -> dict[str, Any]:
    outcome = await _call(canvas_tools.get_canvas, canvas_tools.GetCanvasRequest(), ctx)
    assert outcome.problem is None
    assert outcome.value is not None
    return outcome.value


async def test_agent_builds_connects_and_runs_an_image_chain(ctx: Services) -> None:
    text = await _call(
        canvas_tools.canvas_add_node,
        canvas_tools.CanvasAddNodeRequest(type="text", position={"x": 0, "y": 0}, text="一只狐狸"),
        ctx,
    )
    generator = await _call(
        canvas_tools.canvas_add_node,
        canvas_tools.CanvasAddNodeRequest(type="image_generate", position={"x": 400, "y": 0}, aspect_ratio="16:9"),
        ctx,
    )
    assert text.value is not None
    assert generator.value is not None
    text_id, gen_id = text.value["node"]["id"], generator.value["node"]["id"]

    bad = await _call(
        canvas_tools.canvas_connect,
        canvas_tools.CanvasConnectRequest(source=text_id, source_handle="text", target=gen_id, target_handle="image1"),
        ctx,
    )
    assert bad.problem is not None
    assert bad.problem.code == "canvas_invalid_connection"

    ok = await _call(
        canvas_tools.canvas_connect,
        canvas_tools.CanvasConnectRequest(source=text_id, source_handle="text", target=gen_id, target_handle="prompt"),
        ctx,
    )
    assert ok.problem is None

    run = await _call(canvas_tools.canvas_run_node, canvas_tools.CanvasNodeRequest(node_id=gen_id), ctx)
    assert run.problem is None
    assert run.value is not None
    call = _queue(ctx).calls[0]
    assert call["task_type"] == "canvas_image"
    assert call["source"] == "mcp"
    assert call["payload"]["prompt"] == "一只狐狸"
    assert call["payload"]["aspect_ratio"] == "16:9"

    view = await _canvas(ctx)
    types = sorted(n["type"] for n in view["nodes"])
    assert types == ["image", "image_generate", "text"]
    image = next(n for n in view["nodes"] if n["id"] == run.value["image_node_id"])
    assert image["data"]["status"] == "queued"


async def test_running_without_a_prompt_is_a_problem_not_a_task(ctx: Services) -> None:
    generator = await _call(
        canvas_tools.canvas_add_node,
        canvas_tools.CanvasAddNodeRequest(type="image_generate", position={"x": 0, "y": 0}),
        ctx,
    )
    assert generator.value is not None
    run = await _call(
        canvas_tools.canvas_run_node, canvas_tools.CanvasNodeRequest(node_id=generator.value["node"]["id"]), ctx
    )
    assert run.problem is not None
    assert run.problem.code == "canvas_prompt_required"
    assert _queue(ctx).calls == []


async def test_episode_view_exposes_read_only_project_nodes_and_h3_writer_runs_per_episode(
    ctx: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = await _call(canvas_tools.canvas_set_episodes, canvas_tools.CanvasSetEpisodesRequest(episodes=[1]), ctx)
    assert shown.value == {"shown_episodes": [1], "revision": 1}
    view = await _canvas(ctx)
    units = [n for n in view["nodes"] if n["type"] == "unit"]
    assert [n["id"] for n in units] == ["p:unit:1:E1U01", "p:unit:1:E1U02"]
    assert all(n["read_only"] for n in view["nodes"] if n["type"] in {"unit", "episode", "h3_prompt"})

    undeletable = await _call(
        canvas_tools.canvas_delete_node, canvas_tools.CanvasNodeRequest(node_id="p:unit:1:E1U01"), ctx
    )
    assert undeletable.problem is not None

    writer = await _call(
        canvas_tools.canvas_add_node,
        canvas_tools.CanvasAddNodeRequest(type="h3_writer", position={"x": 1500, "y": 0}),
        ctx,
    )
    assert writer.value is not None
    writer_id = writer.value["node"]["id"]
    empty = await _call(canvas_tools.canvas_run_node, canvas_tools.CanvasNodeRequest(node_id=writer_id), ctx)
    assert empty.problem is not None
    assert empty.problem.code == "canvas_h3_writer_no_units"

    for unit in units:
        await _call(
            canvas_tools.canvas_connect,
            canvas_tools.CanvasConnectRequest(
                source=unit["id"], source_handle="unit", target=writer_id, target_handle="units"
            ),
            ctx,
        )
    enqueued: list[dict[str, Any]] = []

    async def _enqueue(**kwargs: Any) -> dict[str, Any]:
        enqueued.append(kwargs)
        return {"task_id": "text-task"}

    monkeypatch.setattr(canvas_service, "prompt_authoring_preflight", lambda *_a: None)
    monkeypatch.setattr(canvas_service, "enqueue_task_only", _enqueue)

    run = await _call(canvas_tools.canvas_run_node, canvas_tools.CanvasNodeRequest(node_id=writer_id), ctx)

    assert run.problem is None
    assert run.value is not None
    assert run.value["task_ids"] == ["text-task"]
    assert enqueued[0]["payload"]["entry_ids"] == ["E1U01", "E1U02"]
    assert enqueued[0]["payload"]["keep_text"] is True
    after = await _canvas(ctx)
    outputs = [e for e in after["edges"] if e["source"] == writer_id and e["sourceHandle"] == "h3"]
    assert {e["target"] for e in outputs} == {"p:h3_prompt:1:E1U01", "p:h3_prompt:1:E1U02"}


async def test_long_texts_are_truncated_in_the_agent_digest(ctx: Services) -> None:
    await _call(
        canvas_tools.canvas_add_node,
        canvas_tools.CanvasAddNodeRequest(type="text", position={"x": 0, "y": 0}, text="长" * 5000),
        ctx,
    )
    node = (await _canvas(ctx))["nodes"][0]
    assert len(node["data"]["text"]) < 700


async def test_each_character_is_a_group_whose_sheet_feeds_references_and_whose_generator_runs(ctx: Services) -> None:
    ctx.projects.add_character("demo", "阿离", "少女，黑色短发")
    ctx.projects.add_character("demo", "阿宁", "")
    sheet = "p:char_sheet:阿离"

    view = await _canvas(ctx)
    by_type: dict[str, list[str]] = {}
    for node in view["nodes"]:
        by_type.setdefault(node["id"].split(":")[1], []).append(node["id"])
    assert sorted(by_type["character"]) == ["p:character:阿宁", "p:character:阿离"]
    assert sorted(by_type) == ["char_gen", "char_ref", "char_sheet", "char_text", "character"]
    assert all(n["read_only"] for n in view["nodes"] if n["id"].startswith("p:char"))
    inner = {(e["source"], e["target"]) for e in view["edges"] if e.get("virtual")}
    assert ("p:char_text:阿离", "p:char_gen:阿离") in inner
    assert ("p:char_gen:阿离", sheet) in inner

    generator = await _call(
        canvas_tools.canvas_add_node,
        canvas_tools.CanvasAddNodeRequest(type="image_generate", position={"x": 600, "y": 0}),
        ctx,
    )
    assert generator.value is not None
    wired = await _call(
        canvas_tools.canvas_connect,
        canvas_tools.CanvasConnectRequest(
            source=sheet, source_handle="image", target=generator.value["node"]["id"], target_handle="image1"
        ),
        ctx,
    )
    assert wired.problem is None

    run = await _call(canvas_tools.canvas_run_node, canvas_tools.CanvasNodeRequest(node_id="p:char_gen:阿离"), ctx)
    assert run.problem is None
    call = _queue(ctx).calls[0]
    assert (call["task_type"], call["resource_id"], call["media_type"]) == ("character", "阿离", "image")

    blank = await _call(canvas_tools.canvas_run_node, canvas_tools.CanvasNodeRequest(node_id="p:char_gen:阿宁"), ctx)
    assert blank.problem is not None
    assert blank.problem.code == "canvas_character_description_required"
