"""画布图的纯数据操作：节点类型、句柄类型与连线规则，不读盘。

图结构为 ``{"revision": int, "nodes": [...], "edges": [...]}``。节点 ``data`` 里由服务端
维护的字段（``status`` / ``path`` / ``task_id`` / ``error`` / ``version``）不接受客户端补丁。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

NODE_TEXT = "text"
NODE_IMAGE = "image"
NODE_IMAGE_GENERATE = "image_generate"
NODE_H3_WRITER = "h3_writer"
#: 用户可创建、持久化在画布里的节点类型。
NODE_TYPES = (NODE_TEXT, NODE_IMAGE, NODE_IMAGE_GENERATE, NODE_H3_WRITER)

#: 由项目数据推导、不落盘的节点（集 / 视频单元 / 起始帧联合图 / H3 提示词），id 形如 ``p:<kind>:...``。
DERIVED_PREFIX = "p:"
KIND_EPISODE = "episode"
KIND_UNIT = "unit"
KIND_KEYFRAME = "keyframe"
KIND_H3_PROMPT = "h3_prompt"
DERIVED_KINDS = (KIND_EPISODE, KIND_UNIT, KIND_KEYFRAME, KIND_H3_PROMPT)

#: 句柄的数据类型。连线只允许同类型句柄相连。
HANDLE_TEXT = "text"
HANDLE_IMAGE = "image"
HANDLE_UNIT = "unit"
HANDLE_H3 = "h3"

#: ``(节点类型, 句柄 id) → 数据类型``，输入句柄在左、输出句柄在右。
INPUT_HANDLES: dict[str, dict[str, str]] = {
    NODE_IMAGE_GENERATE: {"prompt": HANDLE_TEXT, "refs": HANDLE_IMAGE},
    NODE_H3_WRITER: {"units": HANDLE_UNIT},
}
OUTPUT_HANDLES: dict[str, dict[str, str]] = {
    NODE_TEXT: {"text": HANDLE_TEXT},
    NODE_IMAGE: {"image": HANDLE_IMAGE},
    NODE_IMAGE_GENERATE: {"text": HANDLE_TEXT, "image": HANDLE_IMAGE},
    NODE_H3_WRITER: {"h3": HANDLE_H3},
    KIND_UNIT: {"unit": HANDLE_UNIT, "text": HANDLE_TEXT},
    KIND_KEYFRAME: {"image": HANDLE_IMAGE},
}

#: 只由服务端写入的 data 字段。
SERVER_OWNED_DATA_FIELDS = frozenset({"status", "path", "task_id", "error", "version"})

STATUS_IDLE = "idle"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
BUSY_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})

#: 新建节点的默认 data。
_DEFAULT_DATA: dict[str, dict[str, Any]] = {
    NODE_TEXT: {"text": ""},
    NODE_IMAGE: {"path": None},
    NODE_IMAGE_GENERATE: {"aspect_ratio": "9:16", "status": STATUS_IDLE},
    NODE_H3_WRITER: {},
}

#: 输出节点相对供应商节点的摆放间距。
OUTPUT_OFFSET_X = 420
OUTPUT_STACK_GAP = 300


class CanvasGraphError(Exception):
    """画布操作违反图规则。``code`` 对应 ``errors`` 命名空间的 i18n 键。"""

    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def empty_graph() -> dict[str, Any]:
    return {"revision": 0, "nodes": [], "edges": [], "view": {"episodes": []}, "overrides": {}}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def find_node(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    for node in graph["nodes"]:
        if node["id"] == node_id:
            return node
    raise CanvasGraphError("canvas_node_not_found", id=node_id)


def derived_kind(node_id: str) -> str | None:
    """``p:<kind>:...`` 形式的派生节点 id → kind；非派生 id 返回 None。"""
    if not node_id.startswith(DERIVED_PREFIX):
        return None
    kind = node_id.split(":", 2)[1]
    return kind if kind in DERIVED_KINDS else None


def derived_id(kind: str, *parts: object) -> str:
    return ":".join([DERIVED_PREFIX.rstrip(":"), kind, *(str(part) for part in parts)])


def _lookup(graph: dict[str, Any], node_id: str, derived: Mapping[str, dict[str, Any]] | None) -> dict[str, Any]:
    """按 id 取节点：先看落盘节点，再看派生节点（有数据用数据，没有就只按 id 推出类型）。"""
    kind = derived_kind(node_id)
    if kind is not None:
        return (derived or {}).get(node_id) or {"id": node_id, "type": kind, "position": {"x": 0, "y": 0}, "data": {}}
    return find_node(graph, node_id)


def _abs_position(node: dict[str, Any]) -> dict[str, float]:
    return node.get("abs") or node["position"]


def make_node(
    node_type: str,
    position: dict[str, float],
    data: dict[str, Any] | None = None,
    *,
    server_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``data`` 是客户端可写字段；``server_data`` 供服务端写入受保护字段（如上传图片的 ``path``）。"""
    if node_type not in NODE_TYPES:
        raise CanvasGraphError("canvas_node_type_invalid", type=node_type)
    merged = {**_DEFAULT_DATA[node_type], **_user_data(data or {}), **(server_data or {})}
    return {"id": new_id(node_type), "type": node_type, "position": _position(position), "data": merged}


def _position(position: dict[str, float]) -> dict[str, float]:
    return {"x": float(position.get("x", 0)), "y": float(position.get("y", 0))}


def _user_data(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in SERVER_OWNED_DATA_FIELDS}


def add_node(graph: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    graph["nodes"].append(node)
    return node


def patch_node(
    graph: dict[str, Any],
    node_id: str,
    *,
    position: dict[str, float] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node = find_node(graph, node_id)
    if position is not None:
        node["position"] = _position(position)
    if data:
        node["data"].update(_user_data(data))
    return node


def remove_node(graph: dict[str, Any], node_id: str) -> None:
    node = find_node(graph, node_id)
    if node["data"].get("status") in BUSY_STATUSES:
        raise CanvasGraphError("canvas_node_busy", id=node_id)
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != node_id]
    graph["edges"] = [e for e in graph["edges"] if node_id not in (e["source"], e["target"])]


def _handle_type(table: dict[str, dict[str, str]], node: dict[str, Any], handle: str | None) -> str | None:
    return table.get(node["type"], {}).get(handle or "")


def connect(graph: dict[str, Any], source: str, source_handle: str, target: str, target_handle: str) -> dict[str, Any]:
    src = _lookup(graph, source, None)
    dst = _lookup(graph, target, None)
    out_type = _handle_type(OUTPUT_HANDLES, src, source_handle)
    in_type = _handle_type(INPUT_HANDLES, dst, target_handle)
    if out_type is None or in_type is None or out_type != in_type or source == target:
        raise CanvasGraphError("canvas_invalid_connection")
    for edge in graph["edges"]:
        if (edge["source"], edge["sourceHandle"], edge["target"], edge["targetHandle"]) == (
            source,
            source_handle,
            target,
            target_handle,
        ):
            return edge
    edge = {
        "id": new_id("edge"),
        "source": source,
        "sourceHandle": source_handle,
        "target": target,
        "targetHandle": target_handle,
    }
    graph["edges"].append(edge)
    return edge


def disconnect(graph: dict[str, Any], edge_id: str) -> None:
    before = len(graph["edges"])
    graph["edges"] = [e for e in graph["edges"] if e["id"] != edge_id]
    if len(graph["edges"]) == before:
        raise CanvasGraphError("canvas_edge_not_found", id=edge_id)


def _incoming(
    graph: dict[str, Any], node_id: str, handle: str, derived: Mapping[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """连到 ``node_id`` 某输入句柄的上游节点，按画布纵向位置排序（读图顺序即拼接顺序）。

    指向当前没有加载的派生节点（该集未展示）的连线被跳过。
    """
    sources = []
    for e in graph["edges"]:
        if e["target"] != node_id or e["targetHandle"] != handle:
            continue
        if derived_kind(e["source"]) is not None:
            if derived is not None and e["source"] in derived:
                sources.append(derived[e["source"]])
        else:
            sources.append(find_node(graph, e["source"]))
    return sorted(sources, key=lambda n: (_abs_position(n)["y"], _abs_position(n)["x"]))


def resolve_generate_inputs(
    graph: dict[str, Any], node_id: str, derived: Mapping[str, dict[str, Any]] | None = None
) -> tuple[str, list[str]]:
    """图片供应商节点的输入：``(拼接后的提示词, 参考图项目相对路径列表)``。

    文本来自文本节点与视频单元正文；参考图来自图片节点与起始帧联合图。只收已有产物的参考图，
    占位中的上游图片（还没有 path）被跳过。
    """
    node = find_node(graph, node_id)
    if node["type"] != NODE_IMAGE_GENERATE:
        raise CanvasGraphError("canvas_node_not_runnable", id=node_id)
    texts = [str(n["data"].get("text") or "").strip() for n in _incoming(graph, node_id, "prompt", derived)]
    prompt = "\n\n".join(t for t in texts if t)
    refs = [p for n in _incoming(graph, node_id, "refs", derived) if isinstance(p := n["data"].get("path"), str) and p]
    return prompt, refs


def stale_generated_images(graph: dict[str, Any], derived: Mapping[str, dict[str, Any]]) -> set[str]:
    """由某次运行产出、但供应商节点现在的输入已不同于当时输入的图片节点 id。"""
    stale: set[str] = set()
    for edge in graph["edges"]:
        if edge["sourceHandle"] != "image" or derived_kind(edge["source"]) is not None:
            continue
        image = next((n for n in graph["nodes"] if n["id"] == edge["target"]), None)
        generator = next((n for n in graph["nodes"] if n["id"] == edge["source"]), None)
        if image is None or generator is None or not image["data"].get("generated"):
            continue
        if generator["type"] != NODE_IMAGE_GENERATE or image["data"].get("status") != STATUS_DONE:
            continue
        prompt, _ = resolve_generate_inputs(graph, generator["id"], derived)
        if prompt and prompt != image["data"].get("prompt"):
            stale.add(image["id"])
    return stale


def add_run_outputs(graph: dict[str, Any], node_id: str, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """为一次运行挂出输出节点：使用的提示词文本节点 + 待填充的图片占位节点，并连上供应商节点。

    重复运行会往下堆叠，已有输出保留。输出节点上的 ``in`` 句柄只承接来源连线，不接受手动连线。
    """
    source = find_node(graph, node_id)
    siblings = sum(1 for e in graph["edges"] if e["source"] == node_id and e["sourceHandle"] == "image")
    x = source["position"]["x"] + OUTPUT_OFFSET_X
    y = source["position"]["y"] + siblings * OUTPUT_STACK_GAP
    text_node = make_node(NODE_TEXT, {"x": x, "y": y}, {"text": prompt, "generated": True})
    image_node = make_node(NODE_IMAGE, {"x": x + OUTPUT_OFFSET_X * 0.75, "y": y})
    image_node["data"].update({"status": STATUS_QUEUED, "prompt": prompt, "generated": True})
    add_node(graph, text_node)
    add_node(graph, image_node)
    for target, handle in ((text_node, "text"), (image_node, "image")):
        graph["edges"].append(
            {
                "id": new_id("edge"),
                "source": node_id,
                "sourceHandle": handle,
                "target": target["id"],
                "targetHandle": "in",
            }
        )
    return text_node, image_node
