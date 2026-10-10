"""画布工具：Agent 经工具读 / 编辑画布并运行节点，规则与网页共用 CanvasService。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.canvas.graph import make_node
from lib.project.project_manager import ProjectManager
from server.agent_toolset import canvas as canvas_tools
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


async def test_episode_nodes_are_no_longer_part_of_the_canvas(ctx: Services) -> None:
    view = await _canvas(ctx)
    assert not [n for n in view["nodes"] if n["type"] in {"unit", "episode", "h3_prompt", "keyframe"}]
    assert "available_episodes" not in view

    gone = await _call(canvas_tools.canvas_delete_node, canvas_tools.CanvasNodeRequest(node_id="p:unit:1:E1U01"), ctx)
    assert gone.problem is not None


async def test_long_texts_are_truncated_in_the_agent_digest(ctx: Services) -> None:
    await _call(
        canvas_tools.canvas_add_node,
        canvas_tools.CanvasAddNodeRequest(type="text", position={"x": 0, "y": 0}, text="长" * 5000),
        ctx,
    )
    node = (await _canvas(ctx))["nodes"][0]
    assert len(node["data"]["text"]) < 700


def _group(view: dict[str, Any], name: str) -> dict[str, Any]:
    return next(n for n in view["nodes"] if n["type"] == "group" and n["data"].get("label") == name)


def _members(view: dict[str, Any], group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """分组里的节点，按类型取（同类型取第一个）。"""
    found: dict[str, dict[str, Any]] = {}
    for n in view["nodes"]:
        if n.get("parent_id") == group["id"]:
            found.setdefault(n["type"], n)
    return found


async def test_project_assets_appear_on_the_canvas_as_plain_groups_and_nodes(ctx: Services) -> None:
    ctx.projects.add_character("demo", "阿离", "少女，黑色短发")
    ctx.projects.add_scenes_batch("demo", {"茶楼": {"description": "旧楼"}})

    view = await _canvas(ctx)

    hero = _group(view, "阿离")
    assert hero["data"]["asset"] == "character"
    assert _group(view, "茶楼")["data"]["asset"] == "scene"
    members = _members(view, hero)
    assert set(members) == {"text", "image", "image_generate", "asset"}
    assert members["text"]["data"]["text"] == "少女，黑色短发"
    # 没有任何只读 / 绑定标记：全是普通节点，可以自由编辑。
    assert not any(n.get("read_only") or "bound" in n["data"] or "link" in n["data"] for n in view["nodes"])
    edges = {(e["source"], e["target"]) for e in view["edges"]}
    assert (members["text"]["id"], members["image_generate"]["id"]) in edges


async def test_a_group_generator_runs_like_any_image_generate_node(ctx: Services) -> None:
    ctx.projects.add_character("demo", "阿离", "少女，黑色短发")
    ctx.projects.add_character("demo", "阿宁", "")
    view = await _canvas(ctx)

    generator = _members(view, _group(view, "阿离"))["image_generate"]
    run = await _call(canvas_tools.canvas_run_node, canvas_tools.CanvasNodeRequest(node_id=generator["id"]), ctx)

    assert run.problem is None
    call = _queue(ctx).calls[0]
    assert call["task_type"] == "canvas_image"
    assert call["payload"]["prompt"] == "少女，黑色短发"

    blank = _members(view, _group(view, "阿宁"))["image_generate"]
    empty = await _call(canvas_tools.canvas_run_node, canvas_tools.CanvasNodeRequest(node_id=blank["id"]), ctx)
    assert empty.problem is not None
    assert empty.problem.code == "canvas_prompt_required"


async def test_a_new_asset_group_creates_the_project_asset_and_the_picture_on_its_asset_node_becomes_its_sheet(
    ctx: Services, tmp_path: Path
) -> None:
    from PIL import Image

    service = canvas_tools._service(ctx)
    _, _ = await service.create_asset_group("demo", "character", "阿离", "少女", {"x": 0, "y": 0})

    # 描述写在分组里的文本节点上，写出去就是项目里这个资产的描述。
    assert ctx.projects.load_project("demo")["characters"]["阿离"]["description"] == "少女"
    view = await _canvas(ctx)
    text = _members(view, _group(view, "阿离"))["text"]
    await _call(
        canvas_tools.canvas_patch_node,
        canvas_tools.CanvasPatchNodeRequest(node_id=text["id"], text="少女，长发"),
        ctx,
    )
    assert ctx.projects.load_project("demo")["characters"]["阿离"]["description"] == "少女，长发"

    # 接一张图到资产节点：这张图成为资产图（走与「上传资产图」同一条入口）。
    project_dir = ctx.projects.get_project_path("demo")
    (project_dir / "canvas" / "images").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "red").save(project_dir / "canvas" / "images" / "pic.png")

    def _add_image(graph: dict[str, Any]) -> str:
        image = make_node("image", {"x": 0, "y": 0}, {}, server_data={"path": "canvas/images/pic.png", "version": 1})
        graph["nodes"].append(image)
        return image["id"]

    image_id, _ = await service.mutate("demo", _add_image)
    asset = _members(view, _group(view, "阿离"))["asset"]
    wired = await _call(
        canvas_tools.canvas_connect,
        canvas_tools.CanvasConnectRequest(
            source=image_id, source_handle="image", target=asset["id"], target_handle="image"
        ),
        ctx,
    )
    assert wired.problem is None

    sheet = ctx.projects.load_project("demo")["characters"]["阿离"]["character_sheet"]
    assert sheet == "characters/阿离.png"
    assert (project_dir / sheet).is_file()


async def test_a_derivative_asset_node_publishes_its_picture_as_that_derivatives_sheet(
    ctx: Services,
) -> None:
    from PIL import Image

    service = canvas_tools._service(ctx)
    group, _ = await service.create_asset_group("demo", "character", "阿离", "少女", {"x": 0, "y": 0})
    ctx.projects.update_asset_entry(
        "character",
        "demo",
        "阿离",
        lambda entry: entry.update(derivatives={"战损": {"description": "衣衫破损", "character_sheet": ""}}),
    )
    project_dir = ctx.projects.get_project_path("demo")
    (project_dir / "canvas" / "images").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "blue").save(project_dir / "canvas" / "images" / "d.png")

    def _add(graph: dict[str, Any]) -> None:
        image = make_node("image", {"x": 0, "y": 0}, {}, server_data={"path": "canvas/images/d.png", "version": 1})
        asset = make_node("asset", {"x": 10, "y": 10}, {"derivative": "战损"})
        asset["parentId"] = group["id"]
        graph["nodes"].extend([image, asset])
        graph["edges"].append(
            {
                "id": "e-d",
                "source": image["id"],
                "sourceHandle": "image",
                "target": asset["id"],
                "targetHandle": "image",
            }
        )

    await service.mutate("demo", _add)

    sheet = ctx.projects.load_project("demo")["characters"]["阿离"]["derivatives"]["战损"]["character_sheet"]
    assert sheet.startswith("characters/derivatives/阿离/")
    assert (project_dir / sheet).is_file()


async def test_editing_the_project_elsewhere_is_not_overwritten_by_the_canvas(ctx: Services) -> None:
    service = canvas_tools._service(ctx)
    await service.create_asset_group("demo", "scene", "茶楼", "旧楼", {"x": 0, "y": 0})
    ctx.projects.update_asset_entry("scene", "demo", "茶楼", lambda entry: entry.update(description="新楼"))

    # 画布上随后做任何别的编辑，都不会把项目里改过的描述冲回旧值。
    await _call(
        canvas_tools.canvas_add_node,
        canvas_tools.CanvasAddNodeRequest(type="text", position={"x": 5, "y": 5}, text="x"),
        ctx,
    )

    assert ctx.projects.load_project("demo")["scenes"]["茶楼"]["description"] == "新楼"


async def test_get_canvas_filters_nodes(ctx: Services) -> None:
    for kind in ("text", "image_generate"):
        await _call(
            canvas_tools.canvas_add_node,
            canvas_tools.CanvasAddNodeRequest(type=kind, position={"x": 0, "y": 0}),
            ctx,
        )

    outcome = await _call(
        canvas_tools.get_canvas, canvas_tools.GetCanvasRequest(node_type="image_generate", include_edges=False), ctx
    )

    assert outcome.value is not None
    assert [n["type"] for n in outcome.value["nodes"]] == ["image_generate"]
    assert outcome.value["node_count"] == 1
    assert outcome.value["edges"] == []


async def test_an_episode_is_imported_as_one_group_with_a_lane_of_plain_nodes_per_unit(ctx: Services) -> None:
    service = canvas_tools._service(ctx)
    project_dir = ctx.projects.get_project_path("demo")
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "episode_1.txt").write_text("第一章 原文", encoding="utf-8")

    group, _ = await service.import_episode("demo", 1)
    again, _ = await service.import_episode("demo", 1)

    assert again["id"] == group["id"]
    assert group["data"]["episode"] == 1
    view = await _canvas(ctx)
    members = [n for n in view["nodes"] if n.get("parent_id") == group["id"]]
    steps = {(n["data"].get("unit"), n["data"]["step"]) for n in members if n["data"].get("step")}
    assert {
        ("E1U01", step)
        for step in ("script", "direction", "keyframe", "h3", "video_gen", "video", "dub_text", "voice_gen", "voice")
    } <= steps
    assert {n["data"]["step"] for n in members if n["type"] == "markdown"} == {"source", "screenplay", "plan"}
    assert not any(n.get("read_only") for n in view["nodes"])
    script = next(n for n in members if n["data"].get("unit") == "E1U01" and n["data"]["step"] == "script")
    assert script["data"]["text"] == "阿离看向阿宁。"
    assert script["data"]["script_value"] == "阿离看向阿宁。"
    # 单元里的节点都是普通类型，连成 H3 提示词 → 视频供应商 → 视频。
    ids = {(n["data"].get("unit"), n["data"]["step"]): n["id"] for n in members if n["data"].get("step")}
    edges = {(e["source"], e["target"]) for e in view["edges"]}
    assert (ids[("E1U01", "h3")], ids[("E1U01", "video_gen")]) in edges
    assert (ids[("E1U01", "video_gen")], ids[("E1U01", "video")]) in edges


async def test_unit_text_is_saved_back_to_the_script_only_on_request(ctx: Services) -> None:
    service = canvas_tools._service(ctx)
    group, _ = await service.import_episode("demo", 1)
    view = await _canvas(ctx)
    script = next(n for n in view["nodes"] if n["data"].get("unit") == "E1U01" and n["data"].get("step") == "script")

    await _call(
        canvas_tools.canvas_patch_node,
        canvas_tools.CanvasPatchNodeRequest(node_id=script["id"], text="新的文本"),
        ctx,
    )
    unchanged = ctx.projects.load_script("demo", "episode_1.json")["video_units"][0]["text"]
    assert unchanged == "阿离看向阿宁。"
    shown = next(n for n in (await _canvas(ctx))["nodes"] if n["id"] == script["id"])
    assert (shown["data"]["text"], shown["data"]["script_value"]) == ("新的文本", "阿离看向阿宁。")

    await service.save_unit_field("demo", 1, "E1U01", "text", "新的文本")

    assert ctx.projects.load_script("demo", "episode_1.json")["video_units"][0]["text"] == "新的文本"
    assert group["data"]["episode"] == 1


async def test_flow_overview_lists_every_unit_step_with_the_node_to_jump_to(ctx: Services) -> None:
    service = canvas_tools._service(ctx)
    await service.import_episode("demo", 1)

    episodes = await service.flow("demo")

    episode = next(e for e in episodes if e["episode"] == 1)
    assert episode["group_id"] is not None
    first = episode["units"][0]
    assert first["unit_id"] == "E1U01"
    assert first["steps"]["script"]["state"] == "done"
    assert first["steps"]["video"]["state"] == "empty"
    assert first["steps"]["script"]["node_id"]


async def test_document_nodes_read_only_source_and_draft_files(ctx: Services) -> None:
    service = canvas_tools._service(ctx)
    project_dir = ctx.projects.get_project_path("demo")
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "episode_1.txt").write_text("原文", encoding="utf-8")

    assert await service.markdown("demo", "source/episode_1.txt") == "原文"
    from lib.canvas.graph import CanvasGraphError

    for bad in ("project.json", "../outside.txt", "source/../project.json"):
        with pytest.raises(CanvasGraphError):
            await service.markdown("demo", bad)


async def test_the_screenplay_document_follows_the_project_draft_and_its_review(ctx: Services) -> None:
    from lib.script import screenplay

    service = canvas_tools._service(ctx)
    project_dir = ctx.projects.get_project_path("demo")
    group, _ = await service.import_episode("demo", 1)

    def _node(view: dict[str, Any]) -> dict[str, Any]:
        return next(n for n in view["nodes"] if n["data"].get("step") == "screenplay")

    assert _node(await _canvas(ctx))["data"]["screenplay_status"] == "none"
    assert await service.markdown("demo", "drafts/episode_1/screenplay.json") == ""

    draft = screenplay.ScreenplayDraft.model_validate(
        {
            "title": "第一集",
            "logline": "阿离当众翻脸",
            "scenes": [
                {
                    "heading": "内景 · 茶楼 · 夜",
                    "summary": "对质",
                    "estimated_seconds": 30,
                    "text": "阿离推开门。",
                }
            ],
        }
    )
    written = screenplay.write_screenplay(project_dir, 1, draft)
    data = _node(await _canvas(ctx))["data"]
    assert data["screenplay_status"] == "pending_review"
    assert data["screenplay_fingerprint"] == screenplay.screenplay_fingerprint(written)
    assert "阿离推开门" in await service.markdown("demo", "drafts/episode_1/screenplay.json")

    again, _ = await service.import_episode("demo", 1)  # 再导入只补缺的文档，不重复
    assert again["id"] == group["id"]
    view = await _canvas(ctx)
    assert sum(1 for n in view["nodes"] if n["data"].get("step") == "screenplay") == 1
    # 来源 → 剧本 → 单元拆解 连成一条线（每对只连一次）。
    ids = {n["data"]["step"]: n["id"] for n in view["nodes"] if n["type"] == "markdown"}
    doc_edges = {(e["source"], e["target"]) for e in view["edges"] if e["source"] in ids.values()}
    assert doc_edges == {(ids["source"], ids["screenplay"]), (ids["screenplay"], ids["plan"])}


async def test_importing_again_adds_only_the_units_that_are_not_on_the_canvas_yet(ctx: Services) -> None:
    service = canvas_tools._service(ctx)
    group, _ = await service.import_episode("demo", 1)
    before = {n["id"]: n["position"] for n in (await _canvas(ctx))["nodes"]}

    ctx.projects.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "drama",
            "video_units": [*_UNITS, {"unit_id": "E1U03", "text": "新增的一段。", "duration_seconds": 8}],
        },
        "episode_1.json",
    )
    again, _ = await service.import_episode("demo", 1)

    view = await _canvas(ctx)
    assert again["id"] == group["id"]
    units = {n["data"]["unit"] for n in view["nodes"] if n["data"].get("unit")}
    assert units == {"E1U01", "E1U02", "E1U03"}
    assert all(
        before[node_id] == next(n for n in view["nodes"] if n["id"] == node_id)["position"] for node_id in before
    )
    third = [n for n in view["nodes"] if n["data"].get("unit") == "E1U03" and n["data"]["step"] == "script"]
    assert len(third) == 1


async def test_viewing_the_canvas_adds_units_that_appeared_later_but_not_ones_the_user_deleted(ctx: Services) -> None:
    service = canvas_tools._service(ctx)
    await service.import_episode("demo", 1)
    view = await _canvas(ctx)
    doomed = [n["id"] for n in view["nodes"] if n["data"].get("unit") == "E1U02"]
    for node_id in doomed:
        await service.delete_node("demo", node_id)

    ctx.projects.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "drama",
            "video_units": [*_UNITS, {"unit_id": "E1U03", "text": "新增的一段。", "duration_seconds": 8}],
        },
        "episode_1.json",
    )
    view = await _canvas(ctx)

    units = {n["data"]["unit"] for n in view["nodes"] if n["data"].get("unit")}
    assert units == {"E1U01", "E1U03"}  # 新单元自动补上；用户删掉的 E1U02 不再冒出来

    await service.import_episode("demo", 1)  # 明确要求导入时才补回
    units = {n["data"]["unit"] for n in (await _canvas(ctx))["nodes"] if n["data"].get("unit")}
    assert units == {"E1U01", "E1U02", "E1U03"}


async def test_keyframe_boards_planned_later_appear_on_the_canvas_by_themselves(
    ctx: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.services.project import canvas_episodes

    service = canvas_tools._service(ctx)
    await service.import_episode("demo", 1)
    sheets: list[dict[str, Any]] = []
    monkeypatch.setattr(
        canvas_episodes.KeyframeBoardService,
        "state",
        lambda _self, _project, _episode: {"sheets": sheets, "units": []},
    )
    assert not [n for n in (await _canvas(ctx))["nodes"] if n["data"].get("step") == "kf_gen"]

    sheets.append({"id": "S1", "scene": "茶楼", "image": None, "cells": [{"id": "C1", "unit_ids": ["E1U01"]}]})
    view = await _canvas(ctx)

    boards = [n for n in view["nodes"] if n["data"].get("step") in ("kf_gen", "kf_sheet")]
    assert {n["data"]["step"] for n in boards} == {"kf_gen", "kf_sheet"}
    again = await _canvas(ctx)  # 只补一次
    assert len([n for n in again["nodes"] if n["data"].get("step") == "kf_gen"]) == 1
