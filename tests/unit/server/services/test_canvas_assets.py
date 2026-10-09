"""把项目里已有的资产放上画布，成为普通节点和分组；以及早先绑定节点的转换。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from lib.canvas.graph import add_node, empty_graph, make_node
from server.services.project import canvas_view
from server.services.project.canvas_assets import import_project_assets, to_primitives


class _PM:
    def __init__(self, root: Path):
        self.root = root

    def get_project_path(self, _name):
        return self.root


PROJECT: dict[str, Any] = {
    "characters": {
        "阿离": {"description": "少女", "character_sheet": "characters/阿离.png"},
        "阿宁": {"description": "少年", "character_sheet": ""},
    },
    "scenes": {"茶楼": {"description": "旧楼", "scene_sheet": ""}},
}


@pytest.fixture
def asset_pm(tmp_path):
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "阿离.png").write_bytes(b"png")
    return _PM(tmp_path)


def _import(asset_pm, graph, project) -> list[str]:
    nodes, edges = canvas_view.build_project_view(asset_pm, "demo", project, graph["overrides"], notes=graph["notes"])
    return import_project_assets(graph, nodes, edges)


def _group(graph, name):
    return next(n for n in graph["nodes"] if n["type"] == "group" and n["data"].get("label") == name)


def _children(graph, group):
    return [n for n in graph["nodes"] if n.get("parentId") == group["id"]]


def test_each_asset_becomes_a_plain_group_of_plain_nodes(asset_pm):
    graph = empty_graph()

    created = _import(asset_pm, graph, PROJECT)

    assert len(created) == 3
    group = _group(graph, "阿离")
    assert (group["data"]["asset"], group["data"]["autolayout"]) == ("character", True)
    assert graph["nodes"][0]["type"] == "group"  # 父节点排在子节点前面
    kids = _children(graph, group)
    assert sorted(n["type"] for n in kids) == ["asset", "image", "image", "image_generate", "text"]
    # 没有任何「与项目绑定」的特殊标记：就是普通节点。
    assert not any({"bound", "link", "role", "derived"} & set(n["data"]) | ({"derived"} & set(n)) for n in kids)
    text = next(n for n in kids if n["type"] == "text")
    assert text["data"]["text"] == "少女"
    sheet = next(n for n in kids if n["type"] == "image" and n["data"]["path"] == "characters/阿离.png")
    asset = next(n for n in kids if n["type"] == "asset")
    wired = {(e["source"], e["sourceHandle"], e["target"], e["targetHandle"]) for e in graph["edges"]}
    assert (sheet["id"], "image", asset["id"], "image") in wired
    generator = next(n for n in kids if n["type"] == "image_generate")
    assert (text["id"], "text", generator["id"], "prompt") in wired


def test_assets_without_a_sheet_get_an_empty_image_slot(asset_pm):
    graph = empty_graph()
    _import(asset_pm, graph, PROJECT)

    kids = _children(graph, _group(graph, "阿宁"))

    sheet = next(n for n in kids if n["type"] == "image" and n["data"].get("label") == "阿宁")
    assert sheet["data"]["path"] is None


def test_importing_again_only_adds_assets_that_are_not_on_the_canvas_yet(asset_pm):
    graph = empty_graph()
    _import(asset_pm, graph, PROJECT)
    before = copy.deepcopy(graph)

    assert _import(asset_pm, graph, PROJECT) == []
    assert graph == before

    project = copy.deepcopy(PROJECT)
    project["props"] = {"令牌": {"description": "铜制", "prop_sheet": ""}}
    assert len(_import(asset_pm, graph, project)) == 1
    assert _group(graph, "令牌")["data"]["asset"] == "prop"


def test_a_derivative_row_keeps_its_name_on_its_own_asset_node(asset_pm):
    graph = empty_graph()
    project = copy.deepcopy(PROJECT)
    project["characters"]["阿离"]["derivatives"] = {"幼年": {"description": "小一点", "character_sheet": ""}}

    _import(asset_pm, graph, project)

    kids = _children(graph, _group(graph, "阿离"))
    assert sorted(
        n["data"].get("derivative") for n in kids if n["type"] == "asset" and n["data"].get("derivative")
    ) == ["幼年"]


def test_a_custom_reference_note_moves_to_the_asset_node(asset_pm):
    graph = empty_graph()
    graph["notes"] = {"p:char_sheet:阿离": "This is her face."}

    _import(asset_pm, graph, PROJECT)

    asset = next(n for n in _children(graph, _group(graph, "阿离")) if n["type"] == "asset")
    assert asset["data"]["note"] == "This is her face."


def test_bound_nodes_from_the_earlier_model_become_plain_nodes(asset_pm):
    graph = empty_graph()
    frame = add_node(graph, make_node("group", {"x": 0, "y": 0}, {"label": "阿离"}))
    frame["data"].update(bound=True, link="p:character:阿离", name="阿离", asset="character")
    sheet = add_node(graph, make_node("image", {"x": 1, "y": 1}))
    sheet["parentId"] = frame["id"]
    sheet["data"].update(bound=True, link="p:char_sheet:阿离", role="sheet", name="阿离")
    port = add_node(graph, make_node("asset", {"x": 2, "y": 2}))
    port["parentId"] = frame["id"]
    port["data"].update(bound=True, link="p:char_port:阿离", role="port", name="阿离")
    gen = add_node(graph, make_node("image_generate", {"x": 3, "y": 3}))
    gen["parentId"] = frame["id"]
    gen["data"].update(bound=True, link="p:char_gen:阿离")
    graph["edges"] += [
        {"id": "e1", "source": gen["id"], "sourceHandle": "image", "target": sheet["id"], "targetHandle": "in"},
        {"id": "e2", "source": sheet["id"], "sourceHandle": "image", "target": port["id"], "targetHandle": "in"},
    ]

    assert to_primitives(graph) is True

    for node in (frame, sheet, port, gen):
        assert not ({"bound", "link", "role"} & set(node["data"]))
    assert frame["data"]["label"] == "阿离"
    assert frame["data"]["asset"] == "character"
    assert [(e["source"], e["target"], e["targetHandle"]) for e in graph["edges"]] == [
        (sheet["id"], port["id"], "image")
    ]
    assert to_primitives(graph) is False
