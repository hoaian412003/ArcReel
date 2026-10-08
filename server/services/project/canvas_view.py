"""画布的项目视图：把参考生视频项目里选中的集推导成画布上的只读节点（集 / 视频单元 / 起始帧联合图 / H3 提示词）。

派生节点不落盘，每次读画布时按项目数据现算，所以它们永远与项目一致；用户只在画布上摆放它们的位置
（集分组的位置存进画布的 ``overrides``）并与自己的节点连线。运行类操作沿用既有端点，不在这里。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.canvas.graph import (
    ASSET_GROUP_TYPES,
    KIND_CHAR_GEN,
    KIND_CHAR_REF,
    KIND_CHAR_SHEET,
    KIND_CHAR_TEXT,
    KIND_CHARACTER,
    KIND_DERIV_GEN,
    KIND_DERIV_SHEET,
    KIND_DERIV_TEXT,
    KIND_EPISODE,
    KIND_H3_PROMPT,
    KIND_KEYFRAME,
    KIND_UNIT,
    derived_id,
)
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import ProjectManager, find_episode
from lib.prompts.video_prompt_profile import is_h3_ref2va
from lib.script.reference_video.h3_prompt import (
    UNAVAILABLE_STALE,
    H3PromptUnavailable,
    load_h3_prompt,
)
from server.services.project.direction_review import read_formal_script
from server.services.project.keyframe_board import KeyframeBoardService
from server.services.project.keyframe_plan import KeyframePlanError

#: 集分组内的列与行排布（画布坐标，子节点位置相对分组）。
GROUP_PADDING_TOP = 64
COLUMN_KEYFRAME_X = 24
COLUMN_UNIT_X = 360
COLUMN_H3_X = 700
UNIT_ROW_HEIGHT = 200
KEYFRAME_ROW_HEIGHT = 260
GROUP_WIDTH = 1060
GROUP_GAP = 80

#: 角色分组在画布左侧竖排：每个角色一组，组内从左到右是 描述 / 参考图 → 图片供应商 → 资产图。
CHARACTER_GROUP_WIDTH = 1140
CHARACTER_GROUP_HEIGHT = 700
#: 每个衍生在分组里占一行（描述 → 图片供应商 → 衍生资产图），分组随衍生数增高。
DERIVATIVE_ROW_HEIGHT = 400
CHARACTER_GROUP_GAP = 60
CHARACTER_ORIGIN_X = -CHARACTER_GROUP_WIDTH - 160
#: 组内各子节点的位置（相对分组）。
_CHAR_TEXT_POS: dict[str, float] = {"x": 24, "y": 64}
_CHAR_REF_POS: dict[str, float] = {"x": 24, "y": 330}
_CHAR_GEN_POS: dict[str, float] = {"x": 400, "y": 64}
_CHAR_SHEET_POS: dict[str, float] = {"x": 790, "y": 64}

#: 未登记摆放位置的节点之间保留的最小分组高度。
GROUP_MIN_HEIGHT = 320


def applicable(project: Mapping[str, Any]) -> bool:
    return project.get("generation_mode") == "reference_video"


def list_episodes(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    """画布可展示的集：参考生视频项目的全部集。"""
    if not applicable(project):
        return []
    return [
        {"episode": ep["episode"], "title": str(ep.get("title") or "")}
        for ep in project.get("episodes") or []
        if isinstance(ep, dict) and isinstance(ep.get("episode"), int)
    ]


def _h3_status(unit: dict[str, Any]) -> str:
    try:
        load_h3_prompt(unit)
    except H3PromptUnavailable as exc:
        return "stale" if exc.code == UNAVAILABLE_STALE else "missing"
    return "current"


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
    episodes: list[int],
    overrides: Mapping[str, Mapping[str, float]],
    *,
    h3_status: Callable[[dict[str, Any]], str] = _h3_status,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 ``(派生节点, 虚拟连线)``。角色节点对所有项目都有；集相关节点只在参考生视频项目里有，不存在的集被跳过。

    ``h3_status`` 判定单元的 H3 提示词是 current / stale / missing，默认按脚本里的载荷机械校验。
    """
    characters, character_edges, next_y = _character_nodes(pm, project_name, project, overrides)
    for asset_type in ASSET_GROUP_TYPES:
        more_nodes, more_edges, next_y = _asset_group_nodes(pm, project_name, project, asset_type, next_y, overrides)
        characters.extend(more_nodes)
        character_edges.extend(more_edges)
    if not applicable(project):
        return characters, character_edges
    project_path = pm.get_project_path(project_name)
    h3 = is_h3_ref2va(project)
    nodes: list[dict[str, Any]] = [*characters]
    edges: list[dict[str, Any]] = [*character_edges]
    cursor_x = 0.0
    keyframes = KeyframeBoardService(pm)

    for number in episodes:
        meta = find_episode(project, number)
        if meta is None:
            continue
        script = read_formal_script(pm, project_name, project, number) or {}
        units = [u for u in script.get("video_units") or [] if isinstance(u, dict) and u.get("unit_id")]
        state: dict[str, Any] = {}
        if h3:
            try:
                state = keyframes.state(project_name, number)
            except KeyframePlanError:
                state = {}
        sheets = state.get("sheets") or []
        cell_of = {row["unit_id"]: row["cell_id"] for row in state.get("units") or []}

        rows = max(len(units), len(sheets) * KEYFRAME_ROW_HEIGHT // UNIT_ROW_HEIGHT + 1, 1)
        height = max(GROUP_MIN_HEIGHT, GROUP_PADDING_TOP + rows * UNIT_ROW_HEIGHT)
        group_id = derived_id(KIND_EPISODE, number)
        group_pos = dict(overrides.get(group_id) or {"x": cursor_x, "y": 0.0})
        cursor_x += GROUP_WIDTH + GROUP_GAP
        nodes.append(
            {
                "id": group_id,
                "type": KIND_EPISODE,
                "derived": True,
                "position": {"x": float(group_pos["x"]), "y": float(group_pos["y"])},
                "width": GROUP_WIDTH,
                "height": height,
                "data": {
                    "episode": number,
                    "title": str(meta.get("title") or ""),
                    "unit_count": len(units),
                    "h3": h3,
                },
            }
        )

        sheet_ids: dict[str, str] = {}
        for index, sheet in enumerate(sheets):
            node_id = derived_id(KIND_KEYFRAME, number, sheet["id"])
            for cell in sheet.get("cells") or []:
                sheet_ids[cell["id"]] = node_id
            rel = {"x": COLUMN_KEYFRAME_X, "y": GROUP_PADDING_TOP + index * KEYFRAME_ROW_HEIGHT}
            nodes.append(
                {
                    "id": node_id,
                    "type": KIND_KEYFRAME,
                    "derived": True,
                    "parentId": group_id,
                    "position": rel,
                    "abs": _abs(group_pos, rel),
                    "data": {
                        "episode": number,
                        "sheet_id": sheet["id"],
                        "scene": sheet.get("scene"),
                        "path": sheet.get("image"),
                        "version": _version(project_path, sheet.get("image")),
                        "problem": sheet.get("problem"),
                        "stale": any(c.get("stale") for c in sheet.get("cells") or []),
                        "cell_count": len(sheet.get("cells") or []),
                        "approved": all(c.get("status") == "approved" for c in sheet.get("cells") or []),
                    },
                }
            )

        for index, unit in enumerate(units):
            unit_id = str(unit["unit_id"])
            row_y = GROUP_PADDING_TOP + index * UNIT_ROW_HEIGHT
            unit_node_id = derived_id(KIND_UNIT, number, unit_id)
            rel = {"x": COLUMN_UNIT_X, "y": row_y}
            nodes.append(
                {
                    "id": unit_node_id,
                    "type": KIND_UNIT,
                    "derived": True,
                    "parentId": group_id,
                    "position": rel,
                    "abs": _abs(group_pos, rel),
                    "data": {
                        "episode": number,
                        "unit_id": unit_id,
                        "text": str(unit.get("text") or ""),
                        "duration_seconds": unit.get("duration_seconds"),
                        "h3_status": h3_status(unit) if h3 else None,
                    },
                }
            )
            sheet_node = sheet_ids.get(cell_of.get(unit_id) or "")
            if sheet_node is not None:
                edges.append(_virtual_edge(sheet_node, "image", unit_node_id, "frame"))
            if h3:
                h3_node_id = derived_id(KIND_H3_PROMPT, number, unit_id)
                h3_rel = {"x": COLUMN_H3_X, "y": row_y}
                payload = unit.get("h3_prompt") if isinstance(unit.get("h3_prompt"), dict) else None
                nodes.append(
                    {
                        "id": h3_node_id,
                        "type": KIND_H3_PROMPT,
                        "derived": True,
                        "parentId": group_id,
                        "position": h3_rel,
                        "abs": _abs(group_pos, h3_rel),
                        "data": {
                            "episode": number,
                            "unit_id": unit_id,
                            "status": h3_status(unit),
                            "sections": {k: v for k, v in (payload or {}).items() if k != "source_text_hash"} or None,
                        },
                    }
                )
                edges.append(_virtual_edge(unit_node_id, "unit", h3_node_id, "in"))
    return nodes, edges


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
                "type": KIND_CHARACTER,
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
ASSET_GROUP_HEIGHT = 440


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
                "type": "character",
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


def writer_output_edges(stored_edges: list[dict[str, Any]], shown: set[str]) -> list[dict[str, Any]]:
    """H3 写作节点 → 它所连单元对应的 H3 提示词节点（输出自动挂出，不落盘）。"""
    out: list[dict[str, Any]] = []
    for edge in stored_edges:
        if edge["targetHandle"] != "units" or not edge["source"].startswith("p:unit:"):
            continue
        h3_id = edge["source"].replace("p:unit:", "p:h3_prompt:", 1)
        if h3_id in shown:
            out.append(_virtual_edge(edge["target"], "h3", h3_id, "in"))
    return out
