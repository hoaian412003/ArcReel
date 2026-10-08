"""画布的项目视图：把参考生视频项目里选中的集推导成画布上的只读节点（集 / 视频单元 / 起始帧联合图 / H3 提示词）。

派生节点不落盘，每次读画布时按项目数据现算，所以它们永远与项目一致；用户只在画布上摆放它们的位置
（集分组的位置存进画布的 ``overrides``）并与自己的节点连线。运行类操作沿用既有端点，不在这里。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.canvas.graph import KIND_EPISODE, KIND_H3_PROMPT, KIND_KEYFRAME, KIND_UNIT, derived_id
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
    """返回 ``(派生节点, 虚拟连线)``。不适用的项目或不存在的集被跳过。

    ``h3_status`` 判定单元的 H3 提示词是 current / stale / missing，默认按脚本里的载荷机械校验。
    """
    if not applicable(project):
        return [], []
    project_path = pm.get_project_path(project_name)
    h3 = is_h3_ref2va(project)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
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
