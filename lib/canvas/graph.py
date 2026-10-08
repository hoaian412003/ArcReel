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
NODE_VIDEO = "video"
NODE_AUDIO = "audio"
NODE_VIDEO_GENERATE = "video_generate"
NODE_AUDIO_GENERATE = "audio_generate"
#: 可持久化在画布里的节点类型（含运行产出的 video / audio 媒体节点）；哪些允许客户端新建由路由与工具的请求模型限定。
NODE_TYPES = (
    NODE_TEXT,
    NODE_IMAGE,
    NODE_IMAGE_GENERATE,
    NODE_H3_WRITER,
    NODE_VIDEO_GENERATE,
    NODE_AUDIO_GENERATE,
    NODE_VIDEO,
    NODE_AUDIO,
)

#: 供应商节点 → (它产出的媒体句柄 / 句柄数据类型, 产出的媒体节点类型)。
PROVIDER_MEDIA: dict[str, tuple[str, str]] = {
    NODE_IMAGE_GENERATE: ("image", NODE_IMAGE),
    NODE_VIDEO_GENERATE: ("video", NODE_VIDEO),
    NODE_AUDIO_GENERATE: ("audio", NODE_AUDIO),
}

#: 由项目数据推导、不落盘的节点（集 / 视频单元 / 起始帧联合图 / H3 提示词 / 角色），id 形如 ``p:<kind>:...``。
DERIVED_PREFIX = "p:"
KIND_EPISODE = "episode"
KIND_UNIT = "unit"
KIND_KEYFRAME = "keyframe"
KIND_H3_PROMPT = "h3_prompt"
KIND_CHARACTER = "character"
#: 角色分组里的四个子节点：描述文本 → 图片供应商（参考图 image1）→ 角色资产图。
KIND_CHAR_TEXT = "char_text"
KIND_CHAR_REF = "char_ref"
KIND_CHAR_GEN = "char_gen"
KIND_CHAR_SHEET = "char_sheet"
#: 角色衍生（如「幼年」）的三个子节点：变化描述 → 图片供应商（以本体资产图为参考做编辑）→ 衍生资产图。
KIND_DERIV_TEXT = "deriv_text"
KIND_DERIV_GEN = "deriv_gen"
KIND_DERIV_SHEET = "deriv_sheet"
DERIVED_KINDS = (
    KIND_EPISODE,
    KIND_UNIT,
    KIND_KEYFRAME,
    KIND_H3_PROMPT,
    KIND_CHARACTER,
    KIND_CHAR_TEXT,
    KIND_CHAR_REF,
    KIND_CHAR_GEN,
    KIND_CHAR_SHEET,
    KIND_DERIV_TEXT,
    KIND_DERIV_GEN,
    KIND_DERIV_SHEET,
)
#: 派生节点里复用通用节点外观与连线规则的那几类：派生种类 → 它扮演的节点类型。
DERIVED_NODE_TYPE: dict[str, str] = {
    KIND_CHAR_TEXT: "text",
    KIND_CHAR_REF: "image",
    KIND_CHAR_GEN: "image_generate",
    KIND_CHAR_SHEET: "image",
    KIND_DERIV_TEXT: "text",
    KIND_DERIV_GEN: "image_generate",
    KIND_DERIV_SHEET: "image",
}

#: 句柄的数据类型。连线只允许同类型句柄相连。
HANDLE_TEXT = "text"
HANDLE_IMAGE = "image"
HANDLE_UNIT = "unit"
HANDLE_H3 = "h3"
HANDLE_VIDEO = "video"
HANDLE_AUDIO = "audio"

#: ``(节点类型, 句柄 id) → 数据类型``，输入句柄在左、输出句柄在右。
INPUT_HANDLES: dict[str, dict[str, str]] = {
    NODE_IMAGE_GENERATE: {"prompt": HANDLE_TEXT, "image1": HANDLE_IMAGE},
    NODE_H3_WRITER: {"units": HANDLE_UNIT},
    NODE_VIDEO_GENERATE: {"prompt": HANDLE_TEXT, "frame": HANDLE_IMAGE},
    NODE_AUDIO_GENERATE: {"text": HANDLE_TEXT},
}
OUTPUT_HANDLES: dict[str, dict[str, str]] = {
    NODE_TEXT: {"text": HANDLE_TEXT},
    NODE_IMAGE: {"image": HANDLE_IMAGE},
    NODE_IMAGE_GENERATE: {"image": HANDLE_IMAGE},
    NODE_H3_WRITER: {"h3": HANDLE_H3},
    NODE_VIDEO: {"video": HANDLE_VIDEO},
    NODE_AUDIO: {"audio": HANDLE_AUDIO},
    NODE_VIDEO_GENERATE: {"video": HANDLE_VIDEO},
    NODE_AUDIO_GENERATE: {"audio": HANDLE_AUDIO},
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
    NODE_VIDEO: {"path": None},
    NODE_AUDIO: {"path": None},
    NODE_VIDEO_GENERATE: {"aspect_ratio": "9:16", "duration_seconds": 5, "status": STATUS_IDLE},
    NODE_AUDIO_GENERATE: {"status": STATUS_IDLE},
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
    return {"revision": 0, "nodes": [], "edges": [], "view": {"episodes": []}, "overrides": {}, "templates": []}


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
        fallback = {"id": node_id, "type": DERIVED_NODE_TYPE.get(kind, kind), "position": {"x": 0, "y": 0}, "data": {}}
        return (derived or {}).get(node_id) or fallback
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


def latest_output(graph: dict[str, Any], generator_id: str, handle: str) -> dict[str, Any] | None:
    """供应商节点某输出句柄上最近一次运行的产物节点：图片取已完成的，文本取最新的提示词副本。"""
    targets = {e["target"] for e in graph["edges"] if e["source"] == generator_id and e["sourceHandle"] == handle}
    outputs = [n for n in graph["nodes"] if n["id"] in targets and n["data"].get("generated")]
    if handle in {"image", "video", "audio"}:
        outputs = [n for n in outputs if n["data"].get("status") == STATUS_DONE and n["data"].get("path")]
    return outputs[-1] if outputs else None


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
            # 角色分组里图片供应商的输出就是该角色的资产图节点。
            source_id = (
                e["source"]
                .replace(f"p:{KIND_CHAR_GEN}:", f"p:{KIND_CHAR_SHEET}:", 1)
                .replace(f"p:{KIND_DERIV_GEN}:", f"p:{KIND_DERIV_SHEET}:", 1)
            )
            if derived is not None and source_id in derived:
                sources.append(derived[source_id])
        else:
            source = find_node(graph, e["source"])
            if source["type"] in PROVIDER_MEDIA:
                # 直接连供应商节点的输出句柄 = 取它最近一次运行的产物，重跑它后下游自动用上新结果。
                latest = latest_output(graph, source["id"], e["sourceHandle"])
                if latest is not None:
                    sources.append(latest)
            else:
                sources.append(source)
    return sorted(sources, key=lambda n: (_abs_position(n)["y"], _abs_position(n)["x"]))


#: 供应商节点的输入句柄：(文本句柄, 参考图句柄 | None)。
_INPUT_HANDLE_NAMES: dict[str, tuple[str, str | None]] = {
    NODE_IMAGE_GENERATE: ("prompt", "image1"),
    NODE_VIDEO_GENERATE: ("prompt", "frame"),
    NODE_AUDIO_GENERATE: ("text", None),
}


def resolve_generate_inputs(
    graph: dict[str, Any], node_id: str, derived: Mapping[str, dict[str, Any]] | None = None
) -> tuple[str, list[str]]:
    """供应商节点的输入：``(拼接后的文本, 参考图项目相对路径列表)``。

    文本来自文本节点与视频单元正文；参考图来自图片节点与起始帧联合图（图片节点的 ``refs``、视频节点的
    ``frame``）。只收已有产物的参考图，占位中的上游图片（还没有 path）被跳过；语音节点没有参考图。
    """
    node = find_node(graph, node_id)
    handles = _INPUT_HANDLE_NAMES.get(node["type"])
    if handles is None:
        raise CanvasGraphError("canvas_node_not_runnable", id=node_id)
    text_handle, ref_handle = handles
    texts = [str(n["data"].get("text") or "").strip() for n in _incoming(graph, node_id, text_handle, derived)]
    prompt = "\n\n".join(t for t in texts if t)
    refs = [n["data"]["path"] for n in _reference_nodes(graph, node_id, derived)] if ref_handle else []
    return prompt, refs


def _reference_nodes(
    graph: dict[str, Any], node_id: str, derived: Mapping[str, dict[str, Any]] | None
) -> list[dict[str, Any]]:
    handle = _INPUT_HANDLE_NAMES[find_node(graph, node_id)["type"]][1]
    if handle is None:
        return []
    return [
        n
        for n in _incoming(graph, node_id, handle, derived)
        if isinstance(n["data"].get("path"), str) and n["data"]["path"]
    ]


def resolve_reference_labels(
    graph: dict[str, Any], node_id: str, derived: Mapping[str, dict[str, Any]] | None = None
) -> list[str]:
    """与 :func:`resolve_generate_inputs` 返回的参考图等长同序的人读名称，用于「图N」编号说明。"""
    labels = []
    for n in _reference_nodes(graph, node_id, derived):
        data = n["data"]
        if n["type"] == KIND_KEYFRAME:
            labels.append(f"keyframe {data.get('sheet_id') or ''}".strip())
        else:
            labels.append(str(data.get("label") or data.get("name") or ""))
    return labels


def reference_legend(labels: list[str]) -> str:
    """「图N」编号说明：编号即参考图在请求里的位置（从 1 起），提示词里按 图N 指认。空列表返回空串。"""
    if not labels:
        return ""
    lines = [f"图{index}: {label}" if label else f"图{index}" for index, label in enumerate(labels, start=1)]
    return "Reference images (cite them in the prompt as 图N):\n" + "\n".join(lines)


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


def add_run_outputs(graph: dict[str, Any], node_id: str, prompt: str) -> dict[str, Any]:
    """为一次运行挂出一个待填充的媒体占位节点（图片 / 视频 / 语音）并连上供应商节点。

    重复运行会往下堆叠，已有输出保留；``prompt`` 记在节点上，供判定产物是否过期。输出节点上的 ``in``
    句柄只承接来源连线，不接受手动连线。
    """
    source = find_node(graph, node_id)
    handle, media_type = PROVIDER_MEDIA[source["type"]]
    siblings = sum(1 for e in graph["edges"] if e["source"] == node_id and e["sourceHandle"] == handle)
    x = source["position"]["x"] + OUTPUT_OFFSET_X
    y = source["position"]["y"] + siblings * OUTPUT_STACK_GAP
    media_node = make_node(media_type, {"x": x, "y": y})
    media_node["data"].update({"status": STATUS_QUEUED, "prompt": prompt, "generated": True})
    add_node(graph, media_node)
    graph["edges"].append(
        {
            "id": new_id("edge"),
            "source": node_id,
            "sourceHandle": handle,
            "target": media_node["id"],
            "targetHandle": "in",
        }
    )
    return media_node


def normalize_legacy(graph: dict[str, Any]) -> None:
    """把旧版落盘的画布迁移到当前形状（读盘时调用，下次写盘即固化）。

    - 图片供应商的参考图输入句柄 ``refs`` 改名 ``image1``；
    - 供应商不再输出「使用的提示词」文本节点：删掉这些自动生成的文本节点及其连线。
    """
    providers = {n["id"] for n in graph["nodes"] if n["type"] in PROVIDER_MEDIA}
    for edge in graph["edges"]:
        if edge.get("targetHandle") == "refs":
            edge["targetHandle"] = "image1"
    stale_text = {
        e["target"]
        for e in graph["edges"]
        if e["source"] in providers and e.get("sourceHandle") == "text" and e.get("targetHandle") == "in"
    }
    if stale_text:
        graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in stale_text]
        graph["edges"] = [e for e in graph["edges"] if e["target"] not in stale_text and e["source"] not in stale_text]
    # 其余从供应商 text 句柄连出去的边（用户手连到下游文本的）已无对应句柄，一并丢弃。
    graph["edges"] = [e for e in graph["edges"] if not (e["source"] in providers and e.get("sourceHandle") == "text")]


def branch_plan(
    graph: dict[str, Any], target_id: str, derived: Mapping[str, dict[str, Any]] | None = None
) -> list[dict[str, str]]:
    """运行 ``target_id`` 之前须先跑的上游节点，先上游后下游，最后一项是 ``target_id`` 自己。

    只收「还没有可用产物」或「产物已过期」的供应商节点与起始帧联合图节点，已是最新的上游不重跑。
    """
    node = find_node(graph, target_id)
    if node["type"] != NODE_IMAGE_GENERATE:
        raise CanvasGraphError("canvas_node_not_runnable", id=target_id)
    stale = stale_generated_images(graph, derived or {})
    order: list[dict[str, str]] = []
    seen: set[str] = set()

    def needs_run(upstream: dict[str, Any]) -> str | None:
        if upstream["type"] == KIND_KEYFRAME:
            data = upstream["data"]
            return "missing" if not data.get("path") else ("stale" if data.get("stale") else None)
        if upstream["type"] != NODE_IMAGE_GENERATE:
            return None
        if latest_output(graph, upstream["id"], "image") is None:
            return "missing"
        outputs = {e["target"] for e in graph["edges"] if e["source"] == upstream["id"]}
        return "stale" if outputs & stale else None

    def visit(current_id: str) -> None:
        if current_id in seen:
            return
        seen.add(current_id)
        for edge in graph["edges"]:
            if edge["target"] != current_id or edge["targetHandle"] not in {"prompt", "image1"}:
                continue
            source_id = edge["source"]
            source = (derived or {}).get(source_id) if derived_kind(source_id) else _node_or_none(graph, source_id)
            if source is None:
                continue
            visit(source_id)
            reason = needs_run(source)
            if reason is not None and all(item["id"] != source_id for item in order):
                order.append({"id": source_id, "type": source["type"], "reason": reason})

    visit(target_id)
    order.append({"id": target_id, "type": NODE_IMAGE_GENERATE, "reason": "target"})
    return order


def _node_or_none(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    return next((n for n in graph["nodes"] if n["id"] == node_id), None)


#: 模板里保留的节点类型：只带可复用的结构（文本、供应商），不带随项目走的产物图片与项目推导节点。
TEMPLATE_NODE_TYPES = (NODE_TEXT, NODE_IMAGE_GENERATE, NODE_H3_WRITER, NODE_VIDEO_GENERATE, NODE_AUDIO_GENERATE)


def make_template(graph: dict[str, Any], name: str, node_ids: list[str]) -> dict[str, Any]:
    """把选中的节点（及它们之间的连线）存成模板；产物、状态与项目推导节点不入模板。"""
    chosen = [
        n
        for n in graph["nodes"]
        if n["id"] in set(node_ids) and n["type"] in TEMPLATE_NODE_TYPES and not n["data"].get("generated")
    ]
    if not chosen:
        raise CanvasGraphError("canvas_template_empty")
    ids = {n["id"] for n in chosen}
    left = min(n["position"]["x"] for n in chosen)
    top = min(n["position"]["y"] for n in chosen)
    return {
        "id": new_id("tpl"),
        "name": name,
        "nodes": [
            {
                "ref": n["id"],
                "type": n["type"],
                "offset": {"x": n["position"]["x"] - left, "y": n["position"]["y"] - top},
                "data": _user_data(n["data"]),
            }
            for n in chosen
        ],
        "edges": [
            {k: e[k] for k in ("source", "sourceHandle", "target", "targetHandle")}
            for e in graph["edges"]
            if e["source"] in ids and e["target"] in ids
        ],
    }


def instantiate_template(
    graph: dict[str, Any], template: dict[str, Any], origin: dict[str, float]
) -> list[dict[str, Any]]:
    """在 ``origin`` 处展开模板：新建节点与连线（id 全部重新生成），返回新节点。"""
    created: dict[str, dict[str, Any]] = {}
    for item in template["nodes"]:
        position = {"x": origin["x"] + item["offset"]["x"], "y": origin["y"] + item["offset"]["y"]}
        created[item["ref"]] = add_node(graph, make_node(item["type"], position, dict(item["data"])))
    for edge in template["edges"]:
        connect(
            graph,
            created[edge["source"]]["id"],
            edge["sourceHandle"],
            created[edge["target"]]["id"],
            edge["targetHandle"],
        )
    return list(created.values())
