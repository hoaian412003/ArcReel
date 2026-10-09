"""把项目里已有的资产（角色 / 场景 / 道具 / 商品）放上画布，成为普通的节点和分组。

画布上没有「资产」这种特殊节点：一个资产就是一个分组（``data.asset`` 记它是哪类项目条目，标题就是资产名），
里面是普通的文本、图片、图片供应商节点，外加一个资产节点——它接一张图，这张图就是资产图（见 ``canvas_publish``）。
这里只负责一次性的搬运：把项目里已经有的内容摆成这样的分组，以及把早先「与项目绑定、只读同步」的节点转成普通节点。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lib.canvas.graph import (
    NODE_ASSET,
    NODE_GROUP,
    derived_kind,
    make_node,
    new_id,
)

#: 项目推导节点的种类后缀 → 画布上的普通节点类型。
_CHILD_TYPES = {"text": "text", "ref": "image", "gen": "image_generate", "sheet": "image", "port": NODE_ASSET}
_SERVER_FIELDS = ("path", "version")


def _suffix(kind: str) -> str:
    return kind.rsplit("_", 1)[-1]


def _primitive(derived: Mapping[str, Any], notes: Mapping[str, str], sheet_notes: Mapping[str, str]) -> dict[str, Any]:
    kind = derived_kind(derived["id"]) or ""
    node_type = _CHILD_TYPES[_suffix(kind)]
    data = derived["data"]
    user: dict[str, Any] = {}
    server: dict[str, Any] = {}
    if node_type == "text":
        user["text"] = str(data.get("text") or "")
    elif node_type == "image":
        user["label"] = str(data.get("label") or "")
        server = {k: data[k] for k in _SERVER_FIELDS if data.get(k) is not None}
        server.setdefault("path", None)
    elif node_type == NODE_ASSET:
        if data.get("derivative"):
            user["derivative"] = data["derivative"]
        if custom := sheet_notes.get(derived["id"]) or notes.get(derived["id"]):
            user["note"] = custom
    return make_node(node_type, derived["position"], user, server_data=server)


def import_project_assets(
    graph: dict[str, Any],
    derived_nodes: list[dict[str, Any]],
    derived_edges: list[dict[str, Any]],
) -> list[str]:
    """项目里有、画布上还没有分组的资产：各建一个分组与组内的普通节点。返回新建的分组 id。"""
    have = {
        (g["data"].get("asset"), str(g["data"].get("label") or "").strip())
        for g in graph["nodes"]
        if g["type"] == NODE_GROUP and g["data"].get("asset")
    }
    created: list[str] = []
    notes = graph["notes"]
    for group in [n for n in derived_nodes if n["type"] == NODE_GROUP]:
        kind, name = group["data"]["asset"], str(group["data"]["name"])
        if (kind, name) in have:
            continue
        frame = make_node(
            NODE_GROUP,
            group["position"],
            {"label": name, "asset": kind, "width": float(group["width"]), "height": float(group["height"])},
        )
        # 首次放进画布时由网页按节点实际尺寸排一次版，之后位置归用户。
        frame["data"]["autolayout"] = True
        graph["nodes"].insert(0, frame)
        created.append(frame["id"])

        mapping: dict[str, str] = {}
        sheet_of_port: dict[str, str] = {}
        for edge in derived_edges:
            if edge["targetHandle"] == "in" and (derived_kind(edge["target"]) or "").endswith("_port"):
                sheet_of_port[edge["target"]] = edge["source"]
        for child in [n for n in derived_nodes if n.get("parentId") == group["id"]]:
            kind_name = derived_kind(child["id"]) or ""
            if _suffix(kind_name) not in _CHILD_TYPES:
                continue
            node = _primitive(child, notes, {child["id"]: notes.get(sheet_of_port.get(child["id"], ""), "")})
            node["parentId"] = frame["id"]
            graph["nodes"].append(node)
            mapping[child["id"]] = node["id"]
        for edge in derived_edges:
            source, target = mapping.get(edge["source"]), mapping.get(edge["target"])
            if source is None or target is None:
                continue
            handle = edge["targetHandle"]
            if handle == "in":
                # 「生成器 → 资产图」不再连线（每次运行产出新的图节点）；「资产图 → 资产节点」接到资产节点的 image 口。
                if not (derived_kind(edge["target"]) or "").endswith("_port"):
                    continue
                handle = "image"
            graph["edges"].append(
                {
                    "id": new_id("edge"),
                    "source": source,
                    "sourceHandle": edge["sourceHandle"],
                    "target": target,
                    "targetHandle": handle,
                }
            )
    return created


def to_primitives(graph: dict[str, Any]) -> bool:
    """把早先「与项目绑定」的节点与分组转成普通节点 / 分组（去掉 ``bound`` / ``link`` / ``role``）。"""
    changed = False
    bound_targets = {n["id"] for n in graph["nodes"] if n["data"].get("bound") and n["type"] != NODE_GROUP}
    kept: list[dict[str, Any]] = []
    for edge in graph["edges"]:
        if edge["targetHandle"] == "in" and edge["target"] in bound_targets:
            target = next(n for n in graph["nodes"] if n["id"] == edge["target"])
            if target["type"] != NODE_ASSET:
                changed = True
                continue
            edge = {**edge, "targetHandle": "image"}
            changed = True
        kept.append(edge)
    graph["edges"] = kept
    for node in graph["nodes"]:
        data = node["data"]
        if node["type"] == NODE_GROUP and data.get("bound"):
            data["label"] = str(data.get("name") or data.get("label") or "")
        if any(key in data for key in ("bound", "link", "role")):
            for key in (
                ("bound", "link", "role", "name", "asset") if node["type"] != NODE_GROUP else ("bound", "link", "name")
            ):
                data.pop(key, None)
            changed = True
    return changed
