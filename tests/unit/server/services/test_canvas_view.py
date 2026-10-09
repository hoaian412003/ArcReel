"""画布项目视图：项目里的角色、场景、道具、商品推导成分组、子节点与它们的虚拟连线。"""

from itertools import pairwise
from pathlib import Path

import pytest

from lib.canvas.graph import derived_id
from server.services.project import canvas_view


class _PM:
    def __init__(self, root: Path):
        self.root = root

    def get_project_path(self, _name):
        return self.root


@pytest.fixture
def patched(tmp_path):
    return _PM(tmp_path)


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

    nodes, edges = canvas_view.build_project_view(patched, "demo", CHARACTER_PROJECT, {})

    by_id = {n["id"]: n for n in nodes}
    group = by_id[derived_id("character", "阿离")]
    assert (group["type"], group["derived"], group["data"]["name"]) == ("group", True, "阿离")
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
    nodes, _ = canvas_view.build_project_view(patched, "demo", CHARACTER_PROJECT, placed)
    groups = {n["data"]["name"]: n for n in nodes if n["type"] == "group" and n["id"].startswith("p:character:")}
    assert groups["阿宁"]["position"] == {"x": 12.0, "y": 34.0}
    assert groups["阿离"]["position"]["x"] < 0
    child = next(n for n in nodes if n["id"] == derived_id("char_sheet", "阿宁"))
    assert child["abs"]["x"] == 12.0 + child["position"]["x"]


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
    nodes, edges = canvas_view.build_project_view(patched, "demo", DERIVATIVE_PROJECT, {})
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


ASSET_PROJECT = {
    "generation_mode": "storyboard",
    "characters": {"阿离": {"description": "少女"}},
    "scenes": {"茶楼": {"description": "旧楼", "scene_sheet": "scenes/茶楼.png"}},
    "props": {"玉佩": {"description": "青色玉佩"}},
    "products": {"马克杯": {"description": "白色马克杯", "product_sheet": ""}},
}


def test_scenes_props_and_products_get_description_generator_sheet_groups_below_the_characters(patched):
    nodes, edges = canvas_view.build_project_view(patched, "demo", ASSET_PROJECT, {})
    by_id = {n["id"]: n for n in nodes}

    for asset, name in (("scene", "茶楼"), ("prop", "玉佩"), ("product", "马克杯")):
        group = by_id[derived_id(asset, name)]
        assert (group["type"], group["data"]["asset"]) == ("group", asset)
        parts = [by_id[derived_id(f"{asset}_{part}", name)] for part in ("text", "gen", "sheet")]
        assert [p["type"] for p in parts] == ["text", "image_generate", "image"]
        assert all(p["parentId"] == group["id"] and p["data"]["asset"] == asset for p in parts)
        inner = {(e["source"], e["target"]) for e in edges}
        assert (parts[0]["id"], parts[1]["id"]) in inner
        assert (parts[1]["id"], parts[2]["id"]) in inner

    assert by_id[derived_id("scene_sheet", "茶楼")]["data"]["path"] == "scenes/茶楼.png"
    assert by_id[derived_id("product_sheet", "马克杯")]["data"]["path"] is None
    # 分组自上而下依次排开，互不重叠。
    ordered = sorted((n for n in nodes if n["type"] == "group"), key=lambda n: n["position"]["y"])
    assert [n["data"]["asset"] for n in ordered] == ["character", "scene", "prop", "product"]
    for upper, lower in pairwise(ordered):
        assert lower["position"]["y"] >= upper["position"]["y"] + upper["height"]


def test_every_asset_sheet_gets_an_asset_port_carrying_the_image_and_note(patched):
    (patched.root / "characters").mkdir()
    (patched.root / "characters" / "阿离.png").write_bytes(b"png")

    nodes, edges = canvas_view.build_project_view(
        patched, "demo", CHARACTER_PROJECT, {}, notes={derived_id("char_sheet", "阿离"): "This is her face."}
    )

    by_id = {n["id"]: n for n in nodes}
    port = by_id[derived_id("char_port", "阿离")]
    sheet = by_id[derived_id("char_sheet", "阿离")]
    assert (port["type"], port["parentId"], port["data"]["role"]) == ("asset", sheet["parentId"], "port")
    assert port["data"]["path"] == "characters/阿离.png"
    assert port["data"]["note"] == "This is her face."
    assert port["data"]["note_target"] == sheet["id"]
    assert port["position"]["x"] > sheet["position"]["x"]
    assert (sheet["id"], "image", port["id"], "in") in {
        (e["source"], e["sourceHandle"], e["target"], e["targetHandle"]) for e in edges
    }
    assert derived_id("char_port", "阿宁") in by_id


def test_a_node_the_user_dragged_inside_its_group_keeps_that_spot_and_is_pinned(patched):
    (patched.root / "characters").mkdir()
    (patched.root / "characters" / "阿离.png").write_bytes(b"png")
    sheet_id = derived_id("char_sheet", "阿离")

    nodes, _ = canvas_view.build_project_view(patched, "demo", CHARACTER_PROJECT, {sheet_id: {"x": 123.0, "y": 456.0}})

    by_id = {n["id"]: n for n in nodes}
    sheet = by_id[sheet_id]
    group = by_id[derived_id("character", "阿离")]
    assert sheet["position"] == {"x": 123.0, "y": 456.0}
    assert sheet["abs"] == {"x": group["position"]["x"] + 123.0, "y": group["position"]["y"] + 456.0}
    assert sheet["data"]["pinned"] is True
    assert "pinned" not in by_id[derived_id("char_gen", "阿离")]["data"]
