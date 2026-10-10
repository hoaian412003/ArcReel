"""一集在画布上的流程：来源与分集计划、每个视频单元从剧本到配音的一条横向流水线、起始帧联合图。

画布上没有「集」这种特殊节点：一集就是一个分组（``data.episode`` 记它是第几集），里面是普通的文档、文本、图片、
视频供应商、语音供应商和媒体节点。每个节点的 ``data`` 里带着它在流水线里的位置（``ep`` / ``unit`` / ``step``），
用来在总览里跳到节点，以及让「运行」走已有的生成链路（起始帧联合图、单元视频、配音）。
生成物（起始帧图、视频、配音）与 H3 提示词由流水线写在项目里，查看画布时按项目当前内容刷新；
单元文本与导演说明在画布上可改，点「存回剧本」才写入剧本（会让依赖它的 H3 提示词与视频过期）。
"""

from __future__ import annotations

import json
import logging
import os
from itertools import pairwise
from pathlib import Path
from typing import Any

from lib.canvas.graph import (
    NODE_AUDIO,
    NODE_AUDIO_GENERATE,
    NODE_GROUP,
    NODE_IMAGE,
    NODE_IMAGE_GENERATE,
    NODE_MARKDOWN,
    NODE_TEXT,
    NODE_VIDEO,
    NODE_VIDEO_GENERATE,
    make_node,
    new_id,
)
from lib.project.project_manager import ProjectManager, find_episode
from lib.script import screenplay, script_review
from lib.script.reference_video.h3_prompt import H3PromptUnavailable, load_h3_prompt
from lib.script.script_editor import patch_field
from server.services.project.direction_review import read_formal_script
from server.services.project.keyframe_board import KeyframeBoardService
from server.services.project.keyframe_plan import KeyframePlanError

logger = logging.getLogger(__name__)

#: 一条单元流水线上的步骤，从左到右。
STEPS = ("script", "direction", "keyframe", "h3", "video_gen", "video", "dub_text", "voice_gen", "voice")
COLUMN_WIDTH = 330
BOARD_COLUMN_X = 28
LANE_X = BOARD_COLUMN_X + COLUMN_WIDTH + 30
ROW_HEIGHT = 380  # 文本节点会随内容往下长（紧凑态最高约 280）
LANE_TOP = 520
DOC_TOP = 64
_H3_ORDER = ("subject_definitions", "summary", "detailed_description", "overall_soundscape", "non_diegetic_music")
_EDITABLE_FIELDS = ("text", "direction")
_MARKDOWN_PREFIXES = ("source/", "drafts/")
_MARKDOWN_MAX_BYTES = 400_000


class EpisodeFlowError(Exception):
    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def _version(project_path: Path, rel: str | None) -> int | None:
    if not rel:
        return None
    try:
        return os.stat(project_path / rel).st_mtime_ns
    except OSError:
        return None


def _exists(project_path: Path, rel: str | None) -> str | None:
    return rel if rel and (project_path / rel).is_file() else None


def h3_text(unit: dict[str, Any]) -> tuple[str, str]:
    """单元当前有效的 H3 提示词（六段按顺序拼成文本）与状态 ``current`` / ``stale`` / ``missing``。"""
    payload = unit.get("h3_prompt")
    status = "current"
    try:
        load_h3_prompt(unit)
    except H3PromptUnavailable as exc:
        status = "stale" if exc.code == "stale" else "missing"
    if not isinstance(payload, dict):
        return "", "missing"
    parts = [f"## {key}\n{str(payload[key]).strip()}" for key in _H3_ORDER if str(payload.get(key) or "").strip()]
    return "\n\n".join(parts), status


def _assets(unit: dict[str, Any]) -> dict[str, Any]:
    raw = unit.get("generated_assets")
    return raw if isinstance(raw, dict) else {}


def _dub_lines(unit: dict[str, Any]) -> list[dict[str, Any]]:
    lines = unit.get("dub_lines")
    return [line for line in lines if isinstance(line, dict)] if isinstance(lines, list) else []


def _tag(node: dict[str, Any], episode: int, step: str, unit_id: str | None = None, **extra: Any) -> dict[str, Any]:
    node["data"].update(ep=episode, step=step, compact=True, **extra)
    if unit_id is not None:
        node["data"]["unit"] = unit_id
    return node


def _place(graph: dict[str, Any], node: dict[str, Any], group: dict[str, Any], x: float, y: float) -> dict[str, Any]:
    node["parentId"] = group["id"]
    node["position"] = {"x": float(x), "y": float(y)}
    graph["nodes"].append(node)
    return node


def _edge(
    graph: dict[str, Any], source: dict[str, Any], handle: str, target: dict[str, Any], target_handle: str
) -> None:
    graph["edges"].append(
        {
            "id": new_id("edge"),
            "source": source["id"],
            "sourceHandle": handle,
            "target": target["id"],
            "targetHandle": target_handle,
        }
    )


def episode_group(graph: dict[str, Any], episode: int) -> dict[str, Any] | None:
    return next(
        (n for n in graph["nodes"] if n["type"] == NODE_GROUP and n["data"].get("episode") == episode),
        None,
    )


_DOCS = (
    ("source", "EP source", "source/episode_{ep}.txt"),
    ("screenplay", "Screenplay", "drafts/episode_{ep}/screenplay.json"),
    ("plan", "Script plan", "drafts/episode_{ep}/script_plan_reference_units.json"),
)


def ensure_docs(graph: dict[str, Any], group: dict[str, Any], episode: int) -> None:
    """集分组顶部的文档节点（来源 → 剧本 → 单元拆解）：缺哪个补哪个，已有的不动。"""
    have = {n["data"].get("step") for n in graph["nodes"] if n.get("parentId") == group["id"]}
    for index, (step, label, template) in enumerate(_DOCS):
        if step in have:
            continue
        doc = make_node(NODE_MARKDOWN, {"x": 0, "y": 0}, {"file": template.format(ep=episode), "title": label})
        doc["data"].update(ep=episode, step=step)
        _place(graph, doc, group, BOARD_COLUMN_X + index * (COLUMN_WIDTH + 30), DOC_TOP)
    docs = {
        n["data"]["step"]: n
        for n in graph["nodes"]
        if n.get("parentId") == group["id"] and n["data"].get("step") in {step for step, _, _ in _DOCS}
    }
    for (upstream, _, _), (downstream, _, _) in pairwise(_DOCS):
        if (
            upstream in docs
            and downstream in docs
            and not any(
                e["source"] == docs[upstream]["id"] and e["target"] == docs[downstream]["id"] for e in graph["edges"]
            )
        ):
            _edge(graph, docs[upstream], "out", docs[downstream], "in")
    needed = BOARD_COLUMN_X + len(_DOCS) * (COLUMN_WIDTH + 30) + 28
    if float(group["data"].get("width") or 0) < needed:
        group["data"]["width"] = float(needed)
    if float(group["data"].get("height") or 0) < DOC_TOP + 420:
        group["data"]["height"] = float(DOC_TOP + 420)


def import_episode(
    pm: ProjectManager, project_name: str, graph: dict[str, Any], episode: int, *, only_new: bool = False
) -> dict[str, Any]:
    """把第 ``episode`` 集的流程摆上画布。已经在画布上时只补缺的：文档节点、还没上画布的单元行与关键帧板，已有节点（含用户调过的位置）不动。"""
    existing = episode_group(graph, episode)
    nodes_before = len(graph["nodes"])
    project = pm.load_project(project_name)
    meta = find_episode(project, episode)
    if meta is None:
        raise EpisodeFlowError("canvas_episode_not_found", episode=episode)
    project_path = pm.get_project_path(project_name)
    script = read_formal_script(pm, project_name, project, episode) or {}
    units = [u for u in script.get("video_units") or [] if isinstance(u, dict) and u.get("unit_id")]
    try:
        state = KeyframeBoardService(pm).state(project_name, episode)
    except KeyframePlanError:
        state = {}
    sheets = state.get("sheets") or []
    cell_of = {row["unit_id"]: row["cell_id"] for row in state.get("units") or []}
    cells = {c["id"]: c for sheet in sheets for c in sheet.get("cells") or []}
    sheet_of_cell = {c["id"]: sheet["id"] for sheet in sheets for c in sheet.get("cells") or []}
    row_of = {str(u["unit_id"]): index for index, u in enumerate(units)}

    width = LANE_X + len(STEPS) * COLUMN_WIDTH
    height = LANE_TOP + max(len(units), 1) * ROW_HEIGHT + 40
    if existing is not None:
        group = existing
        group["data"]["width"] = max(float(group["data"].get("width") or 0), float(width))
        group["data"]["height"] = max(float(group["data"].get("height") or 0), float(height))
    else:
        existing_x = [n["position"]["x"] + n["data"].get("width", 0) for n in graph["nodes"] if n["type"] == NODE_GROUP]
        title = str(meta.get("title") or "")
        group = make_node(
            NODE_GROUP,
            {"x": max(existing_x, default=0.0) + 200.0, "y": 0.0},
            {"label": f"EP{episode:02d} · {title}".strip(" ·"), "width": float(width), "height": float(height)},
        )
        group["data"]["episode"] = episode
        graph["nodes"].insert(0, group)

    ensure_docs(graph, group, episode)
    members = [n for n in graph["nodes"] if n.get("parentId") == group["id"]]
    have_units = {n["data"].get("unit") for n in members if n["data"].get("unit")}
    if only_new:
        # 自动跟进：只补上次同步之后新出现的单元，用户从画布上删掉的单元不会又冒出来。
        have_units |= set(group["data"].get("synced_units") or [])
    have_sheets = {n["data"].get("sheet") for n in members if n["data"].get("step") == "kf_gen"}
    if only_new:
        have_sheets |= set(group["data"].get("synced_sheets") or [])

    aspect = str(project.get("aspect_ratio") or "9:16")
    keyframe_nodes: dict[str, dict[str, Any]] = {}
    for sheet in sheets:
        if sheet["id"] in have_sheets:
            continue
        first_row = min(
            (row_of[u] for c in sheet.get("cells") or [] for u in c.get("unit_ids") or [] if u in row_of), default=0
        )
        y = LANE_TOP + first_row * ROW_HEIGHT
        generator = make_node(NODE_IMAGE_GENERATE, {"x": 0, "y": 0}, {"aspect_ratio": "16:9"})
        _tag(generator, episode, "kf_gen", sheet=sheet["id"], title=f"Keyframe {sheet['id']}")
        _place(graph, generator, group, BOARD_COLUMN_X, y)
        board = make_node(
            NODE_IMAGE,
            {"x": 0, "y": 0},
            {"label": f"{sheet['id']} · {sheet.get('scene') or ''}".strip(" ·")},
            server_data={
                "path": _exists(project_path, sheet.get("image")),
                "version": _version(project_path, sheet.get("image")),
            },
        )
        board["data"]["generated"] = True
        board["data"]["status"] = "done" if board["data"]["path"] else "idle"
        _tag(board, episode, "kf_sheet", sheet=sheet["id"])
        _place(graph, board, group, BOARD_COLUMN_X, y + 190)
        graph["edges"].append(
            {
                "id": new_id("edge"),
                "source": generator["id"],
                "sourceHandle": "image",
                "target": board["id"],
                "targetHandle": "in",
            }
        )
        keyframe_nodes[sheet["id"]] = board

    for index, unit in enumerate(units):
        unit_id = str(unit["unit_id"])
        if unit_id in have_units:
            continue
        y = LANE_TOP + index * ROW_HEIGHT
        x = {step: LANE_X + i * COLUMN_WIDTH for i, step in enumerate(STEPS)}
        script_node = _place(
            graph,
            _tag(
                make_node(
                    NODE_TEXT, {"x": 0, "y": 0}, {"text": str(unit.get("text") or ""), "label": f"{unit_id} · script"}
                ),
                episode,
                "script",
                unit_id,
            ),
            group,
            x["script"],
            y,
        )
        _place(
            graph,
            _tag(
                make_node(
                    NODE_TEXT,
                    {"x": 0, "y": 0},
                    {"text": str(unit.get("direction") or ""), "label": f"{unit_id} · direction"},
                ),
                episode,
                "direction",
                unit_id,
            ),
            group,
            x["direction"],
            y,
        )
        cell_id = cell_of.get(unit_id) or ""
        cell = cells.get(cell_id)
        key_path = _exists(project_path, (cell or {}).get("image"))
        keyframe = make_node(
            NODE_IMAGE,
            {"x": 0, "y": 0},
            {"label": f"{unit_id} · {(cell or {}).get('id') or 'keyframe'}"},
            server_data={"path": key_path, "version": _version(project_path, key_path)},
        )
        _place(
            graph,
            _tag(keyframe, episode, "keyframe", unit_id, cell=(cell or {}).get("id"), sheet=sheet_of_cell.get(cell_id)),
            group,
            x["keyframe"],
            y,
        )
        h3, _ = h3_text(unit)
        h3_node = _place(
            graph,
            _tag(
                make_node(NODE_TEXT, {"x": 0, "y": 0}, {"text": h3, "label": f"{unit_id} · H3 prompt"}),
                episode,
                "h3",
                unit_id,
            ),
            group,
            x["h3"],
            y,
        )
        video_gen = _place(
            graph,
            _tag(
                make_node(
                    NODE_VIDEO_GENERATE,
                    {"x": 0, "y": 0},
                    {"aspect_ratio": aspect, "duration_seconds": int(unit.get("duration_seconds") or 5)},
                ),
                episode,
                "video_gen",
                unit_id,
            ),
            group,
            x["video_gen"],
            y,
        )
        assets = _assets(unit)
        clip = _exists(project_path, assets.get("video_clip"))
        video = make_node(
            NODE_VIDEO,
            {"x": 0, "y": 0},
            {"label": unit_id},
            server_data={"path": clip, "version": _version(project_path, clip)},
        )
        video["data"].update(generated=True, status="done" if clip else "idle")
        _place(graph, _tag(video, episode, "video", unit_id), group, x["video"], y)
        lines = _dub_lines(unit)
        dub_text = _place(
            graph,
            _tag(
                make_node(
                    NODE_TEXT,
                    {"x": 0, "y": 0},
                    {
                        "text": "\n".join(str(line.get("text_vi") or "") for line in lines).strip(),
                        "label": f"{unit_id} · dub lines",
                    },
                ),
                episode,
                "dub_text",
                unit_id,
            ),
            group,
            x["dub_text"],
            y,
        )
        voice_gen = _place(
            graph,
            _tag(make_node(NODE_AUDIO_GENERATE, {"x": 0, "y": 0}, {}), episode, "voice_gen", unit_id),
            group,
            x["voice_gen"],
            y,
        )
        audio = _exists(project_path, next((line.get("audio_path") for line in lines if line.get("audio_path")), None))
        voice = make_node(
            NODE_AUDIO,
            {"x": 0, "y": 0},
            {"label": unit_id},
            server_data={"path": audio, "version": _version(project_path, audio)},
        )
        voice["data"].update(generated=True, status="done" if audio else "idle")
        _place(graph, _tag(voice, episode, "voice", unit_id), group, x["voice"], y)

        _edge(graph, h3_node, "text", video_gen, "prompt")
        _edge(graph, keyframe, "image", video_gen, "frame")
        graph["edges"].append(
            {
                "id": new_id("edge"),
                "source": video_gen["id"],
                "sourceHandle": "video",
                "target": video["id"],
                "targetHandle": "in",
            }
        )
        _edge(graph, dub_text, "text", voice_gen, "text")
        graph["edges"].append(
            {
                "id": new_id("edge"),
                "source": voice_gen["id"],
                "sourceHandle": "audio",
                "target": voice["id"],
                "targetHandle": "in",
            }
        )
        del script_node
    if len(graph["nodes"]) > nodes_before:
        # 新节点是按固定间距放的；网页量到真实大小后按矩阵重排一次（排完清掉这个标记）。
        group["data"]["autolayout"] = True
    group["data"]["synced_units"] = sorted(str(u["unit_id"]) for u in units)
    group["data"]["synced_sheets"] = sorted(str(sheet["id"]) for sheet in sheets)
    return group


def episodes_with_new_units(pm: ProjectManager, project_name: str, graph: dict[str, Any]) -> list[int]:
    """画布上已同步过的集分组里，项目又出现了新单元的集（还没同步过的分组不自动动，由导入触发）。"""
    result: list[int] = []
    groups = [
        n
        for n in graph["nodes"]
        if n["type"] == NODE_GROUP and isinstance(n["data"].get("episode"), int) and "synced_units" in n["data"]
    ]
    if not groups:
        return result
    project = pm.load_project(project_name)
    for group in groups:
        episode = group["data"]["episode"]
        script = read_formal_script(pm, project_name, project, episode) or {}
        known = set(group["data"].get("synced_units") or [])
        if any(
            isinstance(u, dict) and u.get("unit_id") and str(u["unit_id"]) not in known
            for u in script.get("video_units") or []
        ):
            result.append(episode)
            continue
        try:
            sheets = KeyframeBoardService(pm).state(project_name, episode).get("sheets") or []
        except KeyframePlanError:
            continue
        synced = set(group["data"].get("synced_sheets") or [])
        if any(str(sheet["id"]) not in synced for sheet in sheets):
            result.append(episode)
    return result


def refresh_outputs(pm: ProjectManager, project_name: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """查看画布时：流水线产出（起始帧图、视频、配音、H3 提示词）按项目当前内容刷新，剧本里的当前文本带上供「存回剧本」比对。不写盘。"""
    episodes = {n["data"]["ep"] for n in nodes if isinstance(n["data"].get("ep"), int) and n["data"].get("step")}
    if not episodes:
        return nodes
    project = pm.load_project(project_name)
    project_path = pm.get_project_path(project_name)
    context: dict[int, tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]] = {}
    for episode in episodes:
        script = read_formal_script(pm, project_name, project, episode) or {}
        units = {
            str(u["unit_id"]): u for u in script.get("video_units") or [] if isinstance(u, dict) and u.get("unit_id")
        }
        try:
            state = KeyframeBoardService(pm).state(project_name, episode)
        except KeyframePlanError:
            state = {}
        sheets = {s["id"]: s for s in state.get("sheets") or []}
        cells = {c["id"]: c for s in state.get("sheets") or [] for c in s.get("cells") or []}
        context[episode] = (units, sheets, cells)

    def _media(node: dict[str, Any], rel: str | None) -> dict[str, Any]:
        path = _exists(project_path, rel)
        data = {**node["data"], "path": path, "version": _version(project_path, path)}
        if node["data"].get("generated"):
            data["status"] = "done" if path else "idle"
        return {**node, "data": data}

    result: list[dict[str, Any]] = []
    for node in nodes:
        data = node["data"]
        episode, step = data.get("ep"), data.get("step")
        if not isinstance(episode, int) or episode not in context:
            result.append(node)
            continue
        units, sheets, cells = context[episode]
        unit = units.get(str(data.get("unit")))
        if step == "kf_sheet":
            node = _media(node, (sheets.get(data.get("sheet")) or {}).get("image"))
        elif step == "keyframe":
            node = _media(node, (cells.get(data.get("cell")) or {}).get("image"))
            sheet_id = next(
                (
                    sid
                    for sid, sheet in sheets.items()
                    if any(c.get("id") == data.get("cell") for c in sheet.get("cells") or [])
                ),
                None,
            )
            if sheet_id is not None:
                node = {**node, "data": {**node["data"], "sheet": sheet_id}}
        elif unit is not None and step == "video":
            node = _media(node, _assets(unit).get("video_clip"))
        elif unit is not None and step == "voice":
            node = _media(
                node, next((line.get("audio_path") for line in _dub_lines(unit) if line.get("audio_path")), None)
            )
        elif unit is not None and step == "h3":
            text, status = h3_text(unit)
            node = {**node, "data": {**data, "text": text, "h3_status": status}}
        elif unit is not None and step in ("script", "direction"):
            field = "text" if step == "script" else "direction"
            node = {**node, "data": {**data, "script_value": str(unit.get(field) or "")}}
        elif step in ("source", "screenplay", "plan") and data.get("file"):
            extra: dict[str, Any] = {"file_rev": _version(project_path, _exists(project_path, data["file"]))}
            if step == "screenplay":
                extra.update(_screenplay_state(project_path, episode))
            node = {**node, "data": {**data, **extra}}
        result.append(node)
    return result


def _screenplay_state(project_path: Path, episode: int) -> dict[str, Any]:
    try:
        data = screenplay.load_screenplay(project_path, episode)
    except screenplay.ScreenplayError:
        return {"screenplay_status": "corrupt", "screenplay_fingerprint": None}
    return {
        "screenplay_status": screenplay.review_status(data),
        "screenplay_fingerprint": screenplay.screenplay_fingerprint(data) if data else None,
    }


def save_unit_field(pm: ProjectManager, project_name: str, episode: int, unit_id: str, field: str, value: str) -> None:
    """把单元文本 / 导演说明写回剧本（走剧本编辑核心：依赖它的 H3 提示词与产出随之按规则过期）。"""
    if field not in _EDITABLE_FIELDS:
        raise EpisodeFlowError("canvas_field_not_editable", field=field)
    project = pm.load_project(project_name)
    if find_episode(project, episode) is None:
        raise EpisodeFlowError("canvas_episode_not_found", episode=episode)
    project_path = pm.get_project_path(project_name)
    filename = script_review.formal_script_filename(project_path, project, episode)
    script = pm.load_script(project_name, filename)
    try:
        patch_field(script, unit_id, field, value)
    except ValueError as exc:
        raise EpisodeFlowError("canvas_unit_not_found", unit=unit_id) from exc
    pm.save_script(project_name, script, filename)


def read_markdown(project_path: Path, file: str) -> str:
    """文档节点的内容：来源文本直接读；分集计划等 JSON 渲染成可读的 markdown。"""
    from lib.infra.path_safety import PathTraversalError, safe_join

    if not file.startswith(_MARKDOWN_PREFIXES):
        raise EpisodeFlowError("canvas_file_not_allowed", file=file)
    try:
        path = safe_join(project_path, file)
    except PathTraversalError as exc:
        raise EpisodeFlowError("canvas_file_not_allowed", file=file) from exc
    # ``source/../project.json`` 这类写法绕过前缀检查：以解析后的真实路径为准。
    resolved = path.resolve()
    if not any(resolved.is_relative_to((project_path / prefix).resolve()) for prefix in _MARKDOWN_PREFIXES):
        raise EpisodeFlowError("canvas_file_not_allowed", file=file)
    if not path.is_file():
        return ""
    raw = path.read_bytes()[:_MARKDOWN_MAX_BYTES].decode("utf-8", errors="replace")
    if path.suffix != ".json":
        return raw
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    if path.name == screenplay.SCREENPLAY_FILENAME and isinstance(data, dict):
        try:
            return screenplay.render_markdown(
                screenplay.load_screenplay(project_path, int(path.parent.name.split("_")[-1])) or data
            )
        except (screenplay.ScreenplayError, ValueError):
            return raw
    return _plan_markdown(data) if isinstance(data, dict) else f"```json\n{raw}\n```"


def _plan_markdown(data: dict[str, Any]) -> str:
    units = data.get("units") or data.get("video_units")
    if not isinstance(units, list):
        return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"
    lines = [f"# {data.get('title') or 'Script plan'}", ""]
    for unit in units:
        if not isinstance(unit, dict):
            continue
        lines.append(f"## {unit.get('unit_id') or ''}".rstrip())
        lines.extend(f"**{key}**: {str(unit[key]).strip()}" for key in ("text", "source_text", "note") if unit.get(key))
        lines.append("")
    return "\n".join(lines)


def flow_status(pm: ProjectManager, project_name: str, graph: dict[str, Any]) -> list[dict[str, Any]]:
    """总览用：每一集的来源 / 分集计划 / 每个单元各步骤的状态，以及画布上对应的节点 id（点一下就能跳过去）。"""
    project = pm.load_project(project_name)
    project_path = pm.get_project_path(project_name)
    by_step: dict[tuple[int, str | None, str], str] = {}
    for node in graph["nodes"]:
        data = node["data"]
        if isinstance(data.get("ep"), int) and data.get("step"):
            by_step[(data["ep"], data.get("unit"), data["step"])] = node["id"]
    result: list[dict[str, Any]] = []
    for meta in project.get("episodes") or []:
        if not isinstance(meta, dict) or not isinstance(meta.get("episode"), int):
            continue
        episode = meta["episode"]
        script = read_formal_script(pm, project_name, project, episode) or {}
        units = [u for u in script.get("video_units") or [] if isinstance(u, dict) and u.get("unit_id")]
        state: dict[str, Any] = {}
        if units:  # 还没有单元脚本的集不会有关键帧板，省掉一次读盘
            try:
                state = KeyframeBoardService(pm).state(project_name, episode)
            except KeyframePlanError:
                state = {}
        cells = {c["id"]: c for s in state.get("sheets") or [] for c in s.get("cells") or []}
        cell_of = {row["unit_id"]: row["cell_id"] for row in state.get("units") or []}
        group = episode_group(graph, episode)
        rows = []
        for unit in units:
            unit_id = str(unit["unit_id"])
            lines = _dub_lines(unit)
            h3, h3_state = h3_text(unit)
            states = {
                "script": "done" if str(unit.get("text") or "").strip() else "empty",
                "direction": "done" if str(unit.get("direction") or "").strip() else "empty",
                "keyframe": "done"
                if _exists(project_path, (cells.get(cell_of.get(unit_id) or "") or {}).get("image"))
                else "empty",
                "h3": {"current": "done", "stale": "stale"}.get(h3_state, "empty") if h3 else "empty",
                "video": "done" if _exists(project_path, _assets(unit).get("video_clip")) else "empty",
                "voice": "done" if any(_exists(project_path, line.get("audio_path")) for line in lines) else "empty",
            }
            rows.append(
                {
                    "unit_id": unit_id,
                    "steps": {
                        step: {"state": state_value, "node_id": by_step.get((episode, unit_id, step))}
                        for step, state_value in states.items()
                    },
                }
            )
        result.append(
            {
                "episode": episode,
                "title": str(meta.get("title") or ""),
                "source": bool(_exists(project_path, f"source/episode_{episode}.txt")),
                "screenplay": _screenplay_state(project_path, episode)["screenplay_status"],
                "plan": bool(_exists(project_path, f"drafts/episode_{episode}/script_plan_reference_units.json")),
                "group_id": group["id"] if group else None,
                "units": rows,
            }
        )
    return result
