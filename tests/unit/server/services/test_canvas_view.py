"""画布项目视图：参考生视频项目里选中的集推导成集 / 单元 / 起始帧 / H3 节点与它们的虚拟连线。"""

from pathlib import Path

import pytest

from lib.canvas.graph import derived_id
from server.services.project import canvas_view


class _PM:
    def __init__(self, root: Path):
        self.root = root

    def get_project_path(self, _name):
        return self.root


UNITS = [
    {"unit_id": "E1U1", "text": "The duel begins", "duration_seconds": 8},
    {
        "unit_id": "E1U2",
        "text": "The duel ends",
        "duration_seconds": 6,
        "h3_prompt": {"summary": "x", "source_text_hash": "h"},
    },
]
PROJECT = {
    "generation_mode": "reference_video",
    "episodes": [{"episode": 1, "title": "Pilot", "script_file": "episode_1.json"}],
}


@pytest.fixture
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr(canvas_view, "is_h3_ref2va", lambda _p: True)
    monkeypatch.setattr(canvas_view, "read_formal_script", lambda *_a: {"video_units": UNITS})
    monkeypatch.setattr(
        canvas_view.KeyframeBoardService,
        "state",
        lambda _self, _n, _e: {
            "units": [{"unit_id": "E1U1", "cell_id": "K01"}],
            "sheets": [
                {
                    "id": "S1",
                    "scene": "arena",
                    "image": None,
                    "problem": None,
                    "cells": [{"id": "K01", "status": "draft"}],
                }
            ],
        },
    )
    return _PM(tmp_path)


def _status(unit):
    return "current" if unit["unit_id"] == "E1U2" else "missing"


def test_view_lays_out_episode_group_with_unit_keyframe_and_h3_columns(patched):
    nodes, edges = canvas_view.build_project_view(patched, "demo", PROJECT, [1], {}, h3_status=_status)
    by_id = {n["id"]: n for n in nodes}
    group = by_id[derived_id("episode", 1)]
    unit = by_id[derived_id("unit", 1, "E1U2")]

    assert group["data"]["unit_count"] == 2
    assert unit["parentId"] == group["id"]
    assert by_id[derived_id("h3_prompt", 1, "E1U2")]["data"]["status"] == "current"
    assert by_id[derived_id("h3_prompt", 1, "E1U1")]["data"]["status"] == "missing"
    assert by_id[derived_id("keyframe", 1, "S1")]["data"]["cell_count"] == 1
    pairs = {(e["source"], e["target"]) for e in edges}
    assert (derived_id("keyframe", 1, "S1"), derived_id("unit", 1, "E1U1")) in pairs
    assert (derived_id("unit", 1, "E1U1"), derived_id("h3_prompt", 1, "E1U1")) in pairs
    assert all(e["virtual"] for e in edges)


def test_episode_group_position_comes_from_overrides(patched):
    nodes, _ = canvas_view.build_project_view(
        patched, "demo", PROJECT, [1], {derived_id("episode", 1): {"x": 500, "y": 40}}
    )
    group = next(n for n in nodes if n["type"] == "episode")
    child = next(n for n in nodes if n["id"] == derived_id("unit", 1, "E1U1"))
    assert group["position"] == {"x": 500.0, "y": 40.0}
    assert child["abs"]["x"] == 500.0 + child["position"]["x"]


def test_non_reference_projects_and_unknown_episodes_yield_nothing(patched):
    assert canvas_view.build_project_view(patched, "demo", {"generation_mode": "storyboard"}, [1], {}) == ([], [])
    assert canvas_view.build_project_view(patched, "demo", PROJECT, [9], {}) == ([], [])
    assert canvas_view.list_episodes({"generation_mode": "storyboard"}) == []
    assert canvas_view.list_episodes(PROJECT) == [{"episode": 1, "title": "Pilot"}]


def test_writer_edges_attach_to_the_h3_nodes_of_connected_units():
    unit = derived_id("unit", 1, "E1U1")
    h3 = derived_id("h3_prompt", 1, "E1U1")
    stored = [{"id": "e", "source": unit, "sourceHandle": "unit", "target": "h3_writer_a", "targetHandle": "units"}]
    out = canvas_view.writer_output_edges(stored, {h3})
    assert [(e["source"], e["sourceHandle"], e["target"]) for e in out] == [("h3_writer_a", "h3", h3)]
    assert canvas_view.writer_output_edges(stored, set()) == []


CHARACTER_PROJECT = {
    "generation_mode": "storyboard",
    "characters": {
        "阿离": {"description": "少女", "character_sheet": "characters/阿离.png"},
        "阿宁": {"description": "少年", "character_sheet": ""},
    },
}


def test_every_project_gets_a_group_per_character_with_text_reference_generator_and_sheet(patched):
    (patched.root / "characters").mkdir()
    (patched.root / "characters" / "阿离.png").write_bytes(b"png")

    nodes, edges = canvas_view.build_project_view(patched, "demo", CHARACTER_PROJECT, [], {})

    by_id = {n["id"]: n for n in nodes}
    group = by_id[derived_id("character", "阿离")]
    assert (group["type"], group["derived"], group["data"]["name"]) == ("character", True, "阿离")
    parts = {kind: by_id[derived_id(kind, "阿离")] for kind in ("char_text", "char_ref", "char_gen", "char_sheet")}
    assert {k: v["type"] for k, v in parts.items()} == {
        "char_text": "text",
        "char_ref": "image",
        "char_gen": "image_generate",
        "char_sheet": "image",
    }
    assert all(p["parentId"] == group["id"] for p in parts.values())
    assert parts["char_text"]["data"]["text"] == "少女"
    assert parts["char_sheet"]["data"]["path"] == "characters/阿离.png"
    assert parts["char_sheet"]["data"]["version"] is not None
    assert parts["char_sheet"]["data"]["label"] == "阿离"
    # 还没有资产图 / 参考图的角色也有完整分组（用来生成与上传）。
    assert by_id[derived_id("char_sheet", "阿宁")]["data"]["path"] is None
    assert by_id[derived_id("char_ref", "阿宁")]["data"]["path"] is None
    inner = {(e["source"], e["sourceHandle"], e["target"], e["targetHandle"]) for e in edges}
    assert (parts["char_text"]["id"], "text", parts["char_gen"]["id"], "prompt") in inner
    assert (parts["char_ref"]["id"], "image", parts["char_gen"]["id"], "image1") in inner
    assert (parts["char_gen"]["id"], "image", parts["char_sheet"]["id"], "in") in inner
    assert all(e["virtual"] for e in edges)


def test_character_groups_stack_on_the_left_and_use_overrides(patched):
    placed = {derived_id("character", "阿宁"): {"x": 12, "y": 34}}
    nodes, _ = canvas_view.build_project_view(patched, "demo", CHARACTER_PROJECT, [], placed)
    groups = {n["data"]["name"]: n for n in nodes if n["type"] == "character"}
    assert groups["阿宁"]["position"] == {"x": 12.0, "y": 34.0}
    assert groups["阿离"]["position"]["x"] < 0
    child = next(n for n in nodes if n["id"] == derived_id("char_sheet", "阿宁"))
    assert child["abs"]["x"] == 12.0 + child["position"]["x"]


def test_characters_sit_alongside_episode_groups_in_reference_projects(patched):
    project = {**PROJECT, "characters": {"阿离": {"character_sheet": ""}}}
    nodes, _ = canvas_view.build_project_view(patched, "demo", project, [1], {}, h3_status=_status)
    assert {n["type"] for n in nodes} >= {"character", "episode", "unit", "image_generate"}


DERIVATIVE_PROJECT = {
    "generation_mode": "storyboard",
    "characters": {
        "阿离": {
            "description": "少女",
            "character_sheet": "characters/阿离.png",
            "derivatives": {
                "幼年": {"description": "年纪更小", "character_sheet": "characters/derivatives/阿离/幼年.png"},
                "战斗装": {"description": "穿上重甲"},
            },
        },
        "阿宁": {"description": "少年"},
    },
}


def test_each_derivative_is_a_row_in_its_character_group_that_edits_the_base_sheet(patched):
    nodes, edges = canvas_view.build_project_view(patched, "demo", DERIVATIVE_PROJECT, [], {})
    by_id = {n["id"]: n for n in nodes}
    group = by_id[derived_id("character", "阿离")]
    other = by_id[derived_id("character", "阿宁")]

    assert group["height"] == canvas_view.CHARACTER_GROUP_HEIGHT + 2 * canvas_view.DERIVATIVE_ROW_HEIGHT
    # 下一组接在这一组（含衍生行）之后，不会叠上去。
    assert other["position"]["y"] >= group["position"]["y"] + group["height"]

    d_text = by_id[derived_id("deriv_text", "阿离/幼年")]
    d_gen = by_id[derived_id("deriv_gen", "阿离/幼年")]
    d_sheet = by_id[derived_id("deriv_sheet", "阿离/幼年")]
    assert (d_text["type"], d_gen["type"], d_sheet["type"]) == ("text", "image_generate", "image")
    assert (d_text["data"]["text"], d_text["data"]["derivative"], d_text["data"]["name"]) == (
        "年纪更小",
        "幼年",
        "阿离",
    )
    assert d_sheet["data"]["path"] == "characters/derivatives/阿离/幼年.png"
    assert d_sheet["data"]["label"] == "阿离/幼年"
    assert by_id[derived_id("deriv_sheet", "阿离/战斗装")]["data"]["path"] is None
    # 第二行在第一行下面。
    assert by_id[derived_id("deriv_gen", "阿离/战斗装")]["position"]["y"] > d_gen["position"]["y"]

    inner = {(e["source"], e["sourceHandle"], e["target"], e["targetHandle"]) for e in edges}
    assert (d_text["id"], "text", d_gen["id"], "prompt") in inner
    assert (derived_id("char_sheet", "阿离"), "image", d_gen["id"], "image1") in inner
    assert (d_gen["id"], "image", d_sheet["id"], "in") in inner
