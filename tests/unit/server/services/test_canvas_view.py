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
