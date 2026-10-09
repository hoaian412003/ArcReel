"""画布的项目视图：把项目里的角色、场景、道具、商品推导成画布上的只读分组与节点。

派生节点不落盘，每次读画布时按项目数据现算，所以它们永远与项目一致；用户只在画布上摆放它们的位置
（分组的位置存进画布的 ``overrides``）并与自己的节点连线。运行类操作沿用既有端点，不在这里。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.canvas.graph import (
    ASSET_GROUP_TYPES,
    KIND_CHAR_GEN,
    KIND_CHAR_PORT,
    KIND_CHAR_REF,
    KIND_CHAR_SHEET,
    KIND_CHAR_TEXT,
    KIND_CHARACTER,
    KIND_DERIV_GEN,
    KIND_DERIV_PORT,
    KIND_DERIV_SHEET,
    KIND_DERIV_TEXT,
    NODE_ASSET,
    NODE_GROUP,
    apply_notes,
    derived_id,
    derived_kind,
)
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import ProjectManager

#: 角色分组在画布左侧竖排：每个角色一组，组内从左到右是 描述 / 参考图 → 图片供应商 → 资产图。
CHARACTER_GROUP_WIDTH = 1400
CHARACTER_GROUP_HEIGHT = 820
#: 每个衍生在分组里占一行（描述 → 图片供应商 → 衍生资产图），分组随衍生数增高。
DERIVATIVE_ROW_HEIGHT = 480
CHARACTER_GROUP_GAP = 60
CHARACTER_ORIGIN_X = -CHARACTER_GROUP_WIDTH - 160
#: 组内各子节点的位置（相对分组）。
_CHAR_TEXT_POS: dict[str, float] = {"x": 24, "y": 64}
_CHAR_REF_POS: dict[str, float] = {"x": 24, "y": 330}
_CHAR_GEN_POS: dict[str, float] = {"x": 400, "y": 64}
_CHAR_SHEET_POS: dict[str, float] = {"x": 790, "y": 64}


def _version(project_path: Path, rel: str | None) -> int | None:
    if not rel:
        return None
    try:
        return os.stat(project_path / rel).st_mtime_ns
    except OSError:
        return None


def _abs(group: Mapping[str, float], rel: Mapping[str, float]) -> dict[str, float]:
    return {"x": group["x"] + rel["x"], "y": group["y"] + rel["y"]}


def build_project_view(
    pm: ProjectManager,
    project_name: str,
    project: dict[str, Any],
    overrides: Mapping[str, Mapping[str, float]],
    *,
    notes: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """项目推导的节点与虚拟连线；资产图节点带上参考说明 ``note``（用户改写的优先，否则默认说明）。"""
    nodes, edges = _build_project_view(pm, project_name, project, overrides)
    apply_notes(nodes, notes or {})
    ports, port_edges = _asset_ports(nodes)
    merged = [*nodes, *ports]
    _apply_manual_positions(merged, overrides)
    return merged, [*edges, *port_edges]


def _apply_manual_positions(nodes: list[dict[str, Any]], overrides: Mapping[str, Mapping[str, float]]) -> None:
    """用户在分组里拖过的节点：位置取手动值（相对分组），并标记 ``pinned``，自动排版不再动它。"""
    group_pos = {n["id"]: n["position"] for n in nodes if "parentId" not in n}
    for node in nodes:
        manual = overrides.get(node["id"]) if node.get("parentId") else None
        if manual is None:
            continue
        node["position"] = {"x": float(manual["x"]), "y": float(manual["y"])}
        parent = group_pos.get(node["parentId"])
        if parent is not None:
            node["abs"] = _abs(parent, node["position"])
        node["data"] = {**node["data"], "pinned": True}


#: 资产输出节点在资产图节点右边的间距（资产图节点宽 240）。
_PORT_OFFSET_X = 280
_PORT_KIND = {KIND_CHAR_SHEET: KIND_CHAR_PORT, KIND_DERIV_SHEET: KIND_DERIV_PORT} | {
    f"{asset}_sheet": f"{asset}_port" for asset in ASSET_GROUP_TYPES
}


def _asset_ports(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """每个资产图节点旁挂一个「资产」输出节点：同一张图 + 参考说明，一个 ``asset`` 输出口，接到任何参考图输入口即用。

    说明的编辑落在资产图节点上（``note_target``），输出节点只是它的出口。
    """
    ports: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for node in nodes:
        kind = derived_kind(node["id"])
        port_kind = _PORT_KIND.get(kind or "")
        if port_kind is None:
            continue
        key = node["id"].split(":", 2)[2]
        port_id = derived_id(port_kind, key)
        position = {"x": node["position"]["x"] + _PORT_OFFSET_X, "y": node["position"]["y"]}
        ports.append(
            {
                "id": port_id,
                "type": NODE_ASSET,
                "derived": True,
                "parentId": node["parentId"],
                "position": position,
                "abs": {"x": node["abs"]["x"] + _PORT_OFFSET_X, "y": node["abs"]["y"]},
                "data": {**node["data"], "role": "port", "note_target": node["id"]},
            }
        )
        edges.append(_virtual_edge(node["id"], "image", port_id, "in"))
    return ports, edges


def _build_project_view(
    pm: ProjectManager,
    project_name: str,
    project: dict[str, Any],
    overrides: Mapping[str, Mapping[str, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 ``(派生节点, 虚拟连线)``：角色、场景、道具、商品的分组及其子节点。"""
    characters, character_edges, next_y = _character_nodes(pm, project_name, project, overrides)
    for asset_type in ASSET_GROUP_TYPES:
        more_nodes, more_edges, next_y = _asset_group_nodes(pm, project_name, project, asset_type, next_y, overrides)
        characters.extend(more_nodes)
        character_edges.extend(more_edges)
    return characters, character_edges


def _derivatives_of(entry: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    table = entry.get("derivatives")
    if not isinstance(table, Mapping):
        return []
    return [(str(key), item) for key, item in table.items() if isinstance(item, Mapping)]


def _char_child(
    nodes: list[dict[str, Any]],
    group_id: str,
    group_pos: Mapping[str, float],
    name: str,
    kind: str,
    node_type: str,
    position: dict[str, float],
    data: dict[str, Any],
) -> str:
    """角色分组里的一个子节点：位置相对分组，只读（不可单独拖离）。"""
    node_id = derived_id(kind, name)
    nodes.append(
        {
            "id": node_id,
            "type": node_type,
            "derived": True,
            "parentId": group_id,
            "position": position,
            "abs": _abs(group_pos, position),
            "data": {"name": name, **data},
        }
    )
    return node_id


def _character_nodes(
    pm: ProjectManager,
    project_name: str,
    project: Mapping[str, Any],
    overrides: Mapping[str, Mapping[str, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    """项目里的每个角色一个分组（按角色名），组内：描述文本 + 参考图 → 图片供应商 → 角色资产图。

    这些节点的内容都取自项目里存的角色条目（描述、参考图、资产图），生成走既有的角色资产图任务，
    所以组内的连线是推导出的（只读）；资产图节点的 image 句柄可以再连到别处的图片供应商。
    """
    characters = project.get("characters")
    if not isinstance(characters, Mapping) or not characters:
        return [], [], 0.0
    project_path = pm.get_project_path(project_name)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    next_y = 0.0
    for name, entry in characters.items():
        entry = entry if isinstance(entry, Mapping) else {}
        sheet = entry.get("character_sheet")
        sheet = sheet if isinstance(sheet, str) and sheet else None
        reference = entry.get("reference_image")
        reference = reference if isinstance(reference, str) and reference else None
        group_id = derived_id(KIND_CHARACTER, name)
        derivatives = _derivatives_of(entry)
        height = CHARACTER_GROUP_HEIGHT + len(derivatives) * DERIVATIVE_ROW_HEIGHT
        default = {"x": float(CHARACTER_ORIGIN_X), "y": next_y}
        next_y += height + CHARACTER_GROUP_GAP
        group_pos = dict(overrides.get(group_id) or default)
        group_pos = {"x": float(group_pos["x"]), "y": float(group_pos["y"])}
        nodes.append(
            {
                "id": group_id,
                "type": NODE_GROUP,
                "derived": True,
                "position": group_pos,
                "width": CHARACTER_GROUP_WIDTH,
                "height": height,
                "data": {"name": name, "asset": "character"},
            }
        )

        text_id = _char_child(
            nodes,
            group_id,
            group_pos,
            name,
            KIND_CHAR_TEXT,
            "text",
            _CHAR_TEXT_POS,
            {"text": str(entry.get("description") or "")},
        )
        ref_id = _char_child(
            nodes,
            group_id,
            group_pos,
            name,
            KIND_CHAR_REF,
            "image",
            _CHAR_REF_POS,
            {"path": reference, "version": _version(project_path, reference), "role": "reference"},
        )
        gen_id = _char_child(
            nodes, group_id, group_pos, name, KIND_CHAR_GEN, "image_generate", _CHAR_GEN_POS, {"aspect_ratio": None}
        )
        sheet_id = _char_child(
            nodes,
            group_id,
            group_pos,
            name,
            KIND_CHAR_SHEET,
            "image",
            _CHAR_SHEET_POS,
            {"path": sheet, "version": _version(project_path, sheet), "label": name, "role": "sheet"},
        )
        edges.extend(
            [
                _virtual_edge(text_id, "text", gen_id, "prompt"),
                _virtual_edge(ref_id, "image", gen_id, "image1"),
                _virtual_edge(gen_id, "image", sheet_id, "in"),
            ]
        )
        for row, (derivative, item) in enumerate(derivatives):
            y = CHARACTER_GROUP_HEIGHT + row * DERIVATIVE_ROW_HEIGHT
            key = f"{name}/{derivative}"
            path = item.get("character_sheet")
            path = path if isinstance(path, str) and path else None
            extra = {"derivative": derivative}
            d_text = _char_child(
                nodes,
                group_id,
                group_pos,
                key,
                KIND_DERIV_TEXT,
                "text",
                {"x": _CHAR_TEXT_POS["x"], "y": y},
                {**extra, "name": name, "text": str(item.get("description") or "")},
            )
            d_gen = _char_child(
                nodes,
                group_id,
                group_pos,
                key,
                KIND_DERIV_GEN,
                "image_generate",
                {"x": _CHAR_GEN_POS["x"], "y": y},
                {**extra, "name": name},
            )
            d_sheet = _char_child(
                nodes,
                group_id,
                group_pos,
                key,
                KIND_DERIV_SHEET,
                "image",
                {"x": _CHAR_SHEET_POS["x"], "y": y},
                {
                    **extra,
                    "name": name,
                    "path": path,
                    "version": _version(project_path, path),
                    "label": key,
                    "role": "derivative",
                },
            )
            edges.extend(
                [
                    _virtual_edge(d_text, "text", d_gen, "prompt"),
                    _virtual_edge(sheet_id, "image", d_gen, "image1"),
                    _virtual_edge(d_gen, "image", d_sheet, "in"),
                ]
            )
    return nodes, edges, next_y


#: 场景 / 道具 / 商品分组的高度（没有参考图节点，比角色组矮）与组宽（同角色组）。
ASSET_GROUP_HEIGHT = 560


def _asset_group_nodes(
    pm: ProjectManager,
    project_name: str,
    project: Mapping[str, Any],
    asset_type: str,
    start_y: float,
    overrides: Mapping[str, Mapping[str, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    """场景 / 道具 / 商品：每个资产一个分组，组内 描述 → 图片供应商 → 资产图，接在上一类分组下面。

    与角色同构（描述与资产图都取自项目里的条目、生成走既有的资产图任务），只是没有参考图与衍生。
    """
    spec = ASSET_SPECS[asset_type]
    entries = project.get(spec.bucket_key)
    if not isinstance(entries, Mapping) or not entries:
        return [], [], start_y
    project_path = pm.get_project_path(project_name)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    next_y = start_y
    for name, entry in entries.items():
        entry = entry if isinstance(entry, Mapping) else {}
        sheet = entry.get(spec.sheet_field)
        sheet = sheet if isinstance(sheet, str) and sheet else None
        group_id = derived_id(asset_type, name)
        default = {"x": float(CHARACTER_ORIGIN_X), "y": next_y}
        next_y += ASSET_GROUP_HEIGHT + CHARACTER_GROUP_GAP
        group_pos = dict(overrides.get(group_id) or default)
        group_pos = {"x": float(group_pos["x"]), "y": float(group_pos["y"])}
        nodes.append(
            {
                "id": group_id,
                "type": NODE_GROUP,
                "derived": True,
                "position": group_pos,
                "width": CHARACTER_GROUP_WIDTH,
                "height": ASSET_GROUP_HEIGHT,
                "data": {"name": name, "asset": asset_type},
            }
        )
        common = {"asset": asset_type}
        text_id = _char_child(
            nodes,
            group_id,
            group_pos,
            name,
            f"{asset_type}_text",
            "text",
            _CHAR_TEXT_POS,
            {**common, "text": str(entry.get("description") or "")},
        )
        gen_id = _char_child(
            nodes, group_id, group_pos, name, f"{asset_type}_gen", "image_generate", _CHAR_GEN_POS, common
        )
        sheet_id = _char_child(
            nodes,
            group_id,
            group_pos,
            name,
            f"{asset_type}_sheet",
            "image",
            _CHAR_SHEET_POS,
            {**common, "path": sheet, "version": _version(project_path, sheet), "label": name, "role": "sheet"},
        )
        edges.extend([_virtual_edge(text_id, "text", gen_id, "prompt"), _virtual_edge(gen_id, "image", sheet_id, "in")])
    return nodes, edges, next_y


def _virtual_edge(source: str, source_handle: str, target: str, target_handle: str) -> dict[str, Any]:
    return {
        "id": f"v:{source}>{target}",
        "source": source,
        "sourceHandle": source_handle,
        "target": target,
        "targetHandle": target_handle,
        "virtual": True,
    }
