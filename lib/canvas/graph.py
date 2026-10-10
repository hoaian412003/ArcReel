"""画布图的纯数据操作：节点类型、句柄类型与连线规则，不读盘。

图结构为 ``{"revision": int, "nodes": [...], "edges": [...]}``。节点 ``data`` 里由服务端
维护的字段（``status`` / ``path`` / ``task_id`` / ``error`` / ``version``）不接受客户端补丁。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

NODE_TEXT = "text"
NODE_IMAGE = "image"
NODE_IMAGE_GENERATE = "image_generate"
NODE_VIDEO = "video"
NODE_AUDIO = "audio"
NODE_VIDEO_GENERATE = "video_generate"
NODE_AUDIO_GENERATE = "audio_generate"
#: 用户自建的分组框：把选中的节点收在一起（子节点带 ``parentId``，位置相对分组）。
NODE_GROUP = "group"
#: 资产输出节点：一个 ``asset`` 输出口，携带一张图和它的参考说明。
NODE_ASSET = "asset"
#: 文档节点：显示项目里一份文本 / markdown 文件（来源、分集计划…）的内容，只读。
NODE_MARKDOWN = "markdown"
#: 可持久化在画布里的节点类型（含运行产出的 video / audio 媒体节点）；哪些允许客户端新建由路由与工具的请求模型限定。
NODE_TYPES = (
    NODE_TEXT,
    NODE_IMAGE,
    NODE_IMAGE_GENERATE,
    NODE_VIDEO_GENERATE,
    NODE_AUDIO_GENERATE,
    NODE_VIDEO,
    NODE_AUDIO,
    NODE_GROUP,
    NODE_ASSET,
    NODE_MARKDOWN,
)

#: 供应商节点 → (它产出的媒体句柄 / 句柄数据类型, 产出的媒体节点类型)。
PROVIDER_MEDIA: dict[str, tuple[str, str]] = {
    NODE_IMAGE_GENERATE: ("image", NODE_IMAGE),
    NODE_VIDEO_GENERATE: ("video", NODE_VIDEO),
    NODE_AUDIO_GENERATE: ("audio", NODE_AUDIO),
}

#: 由项目数据推导、不落盘的节点（角色 / 场景 / 道具 / 商品），id 形如 ``p:<kind>:...``。
DERIVED_PREFIX = "p:"
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
#: 与角色同构的「描述 → 图片供应商 → 资产图」分组：场景 / 道具 / 商品。组及其子节点的种类名为 ``<类型>``、
#: ``<类型>_text``、``<类型>_gen``、``<类型>_sheet``。
ASSET_GROUP_TYPES = ("scene", "prop", "product")
#: 每个资产图旁的「资产」输出节点（一个 ``asset`` 输出口：图 + 参考说明）；种类名 ``char_port`` / ``deriv_port`` / ``<类型>_port``。
KIND_CHAR_PORT = "char_port"
KIND_DERIV_PORT = "deriv_port"
_ASSET_KINDS = tuple(
    kind
    for asset in ASSET_GROUP_TYPES
    for kind in (asset, f"{asset}_text", f"{asset}_gen", f"{asset}_sheet", f"{asset}_port")
)
DERIVED_KINDS = (
    KIND_CHARACTER,
    KIND_CHAR_TEXT,
    KIND_CHAR_REF,
    KIND_CHAR_GEN,
    KIND_CHAR_SHEET,
    KIND_DERIV_TEXT,
    KIND_DERIV_GEN,
    KIND_DERIV_SHEET,
    KIND_CHAR_PORT,
    KIND_DERIV_PORT,
    *_ASSET_KINDS,
)
#: 派生节点里复用通用节点外观与连线规则的那几类：派生种类 → 它扮演的节点类型。
DERIVED_NODE_TYPE: dict[str, str] = {
    # 画布上只有一种分组：角色、场景、道具、商品的分组框都是 ``group``，种类靠节点 id 与 data 区分。
    KIND_CHARACTER: NODE_GROUP,
    **dict.fromkeys(ASSET_GROUP_TYPES, NODE_GROUP),
    KIND_CHAR_TEXT: "text",
    KIND_CHAR_REF: "image",
    KIND_CHAR_GEN: "image_generate",
    KIND_CHAR_SHEET: "image",
    KIND_DERIV_TEXT: "text",
    KIND_DERIV_GEN: "image_generate",
    KIND_DERIV_SHEET: "image",
    KIND_CHAR_PORT: NODE_ASSET,
    KIND_DERIV_PORT: NODE_ASSET,
    **{
        kind: node_type
        for asset in ASSET_GROUP_TYPES
        for kind, node_type in (
            (f"{asset}_text", "text"),
            (f"{asset}_gen", "image_generate"),
            (f"{asset}_sheet", "image"),
            (f"{asset}_port", NODE_ASSET),
        )
    },
}

#: 句柄的数据类型。连线只允许同类型句柄相连。
HANDLE_TEXT = "text"
HANDLE_IMAGE = "image"
HANDLE_VIDEO = "video"
HANDLE_AUDIO = "audio"
#: 资产输出口：携带一张图和它的参考说明，可接到任何接受参考图的输入口。
HANDLE_ASSET = "asset"
#: 文档口：只在文档（markdown）节点之间连，表示「从这份文档得来」（来源 → 剧本 → 单元拆解）。
HANDLE_DOC = "doc"

#: ``(节点类型, 句柄 id) → 数据类型``，输入句柄在左、输出句柄在右。
INPUT_HANDLES: dict[str, dict[str, str]] = {
    NODE_IMAGE_GENERATE: {"prompt": HANDLE_TEXT, "image1": HANDLE_IMAGE},
    NODE_VIDEO_GENERATE: {"prompt": HANDLE_TEXT, "frame": HANDLE_IMAGE},
    NODE_AUDIO_GENERATE: {"text": HANDLE_TEXT},
    # 资产节点接一张图：这张图就是它的内容；放在资产分组里时，它也是这个项目资产的资产图（见 canvas_publish）。
    NODE_ASSET: {"image": HANDLE_IMAGE},
    NODE_MARKDOWN: {"in": HANDLE_DOC},
}
OUTPUT_HANDLES: dict[str, dict[str, str]] = {
    NODE_TEXT: {"text": HANDLE_TEXT},
    NODE_IMAGE: {"image": HANDLE_IMAGE},
    NODE_IMAGE_GENERATE: {"image": HANDLE_IMAGE},
    NODE_VIDEO: {"video": HANDLE_VIDEO},
    NODE_AUDIO: {"audio": HANDLE_AUDIO},
    NODE_VIDEO_GENERATE: {"video": HANDLE_VIDEO},
    NODE_AUDIO_GENERATE: {"audio": HANDLE_AUDIO},
    NODE_ASSET: {"asset": HANDLE_ASSET},
    NODE_MARKDOWN: {"out": HANDLE_DOC},
}

#: 只由服务端写入的 data 字段。
SERVER_OWNED_DATA_FIELDS = frozenset({"status", "path", "task_id", "error", "version"})

STATUS_IDLE = "idle"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
BUSY_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def status_timestamps(data: dict[str, Any]) -> dict[str, Any]:
    """状态变化时一并写入的时间戳：``status_at`` 是最近一次状态变化，进入运行 / 结束时再记 ``started_at`` / ``finished_at``。"""
    status = data.get("status")
    if status is None:
        return {}
    now = utc_now_iso()
    stamps: dict[str, Any] = {"status_at": now}
    if status == STATUS_RUNNING:
        stamps["started_at"] = now
    elif status not in BUSY_STATUSES:
        stamps["finished_at"] = now
    return stamps


#: 新建节点的默认 data。
_DEFAULT_DATA: dict[str, dict[str, Any]] = {
    NODE_TEXT: {"text": ""},
    NODE_IMAGE: {"path": None},
    NODE_IMAGE_GENERATE: {"aspect_ratio": "9:16", "status": STATUS_IDLE},
    NODE_VIDEO: {"path": None},
    NODE_AUDIO: {"path": None},
    NODE_VIDEO_GENERATE: {"aspect_ratio": "9:16", "duration_seconds": 5, "status": STATUS_IDLE},
    NODE_AUDIO_GENERATE: {"status": STATUS_IDLE},
    NODE_GROUP: {"label": "Group"},
    NODE_ASSET: {},
    NODE_MARKDOWN: {"file": "", "title": ""},
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
    return {
        "revision": 0,
        "nodes": [],
        "edges": [],
        "overrides": {},
        "notes": {},
        "meta": {},
        "templates": [],
    }


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


def group_nodes(
    graph: dict[str, Any],
    node_ids: list[str],
    label: str,
    position: dict[str, float],
    size: dict[str, float],
) -> dict[str, Any]:
    """把选中的落盘节点收进一个新分组：分组框放在 ``position``、尺寸 ``size``，子节点位置改成相对分组。

    只收落盘的、还没在别的分组里的节点；项目推导的节点与分组本身不能被收。
    """
    chosen = []
    for node_id in node_ids:
        node = find_node(graph, node_id)
        if node["type"] == NODE_GROUP or node.get("parentId"):
            raise CanvasGraphError("canvas_group_invalid", id=node_id)
        chosen.append(node)
    if len(chosen) < 2:
        raise CanvasGraphError("canvas_group_too_small")
    group = make_node(
        NODE_GROUP,
        position,
        {"label": label or "Group", "width": float(size["width"]), "height": float(size["height"])},
    )
    for node in chosen:
        node["parentId"] = group["id"]
        node["position"] = {
            "x": node["position"]["x"] - group["position"]["x"],
            "y": node["position"]["y"] - group["position"]["y"],
        }
    # React Flow 要求父节点排在子节点前面。
    graph["nodes"].insert(0, group)
    return group


_GROUP_KINDS = (KIND_CHARACTER, *ASSET_GROUP_TYPES)


def set_members(
    graph: dict[str, Any],
    node_ids: list[str],
    group_id: str | None,
    positions: dict[str, dict[str, float]],
) -> None:
    """把落盘节点放进某个分组（用户自建的或项目推导的），或从分组里放出来（``group_id`` 为空）。

    ``positions`` 是网页按新归属算好的位置：放进分组时相对分组，放出来时是画布上的绝对位置。
    """
    if group_id is not None:
        kind = derived_kind(group_id)
        if kind is None:
            if find_node(graph, group_id)["type"] != NODE_GROUP:
                raise CanvasGraphError("canvas_group_invalid", id=group_id)
        elif kind not in _GROUP_KINDS:
            raise CanvasGraphError("canvas_group_invalid", id=group_id)
    for node_id in node_ids:
        if derived_kind(node_id) is not None:
            raise CanvasGraphError("canvas_group_invalid", id=node_id)
        node = find_node(graph, node_id)
        if node["type"] == NODE_GROUP or node_id == group_id:
            raise CanvasGraphError("canvas_group_invalid", id=node_id)
        if group_id is None:
            node.pop("parentId", None)
        else:
            node["parentId"] = group_id
        if node_id in positions:
            node["position"] = _position(positions[node_id])
    if group_id is not None and derived_kind(group_id) is None:
        # React Flow 要求父节点排在子节点前面。
        group = find_node(graph, group_id)
        graph["nodes"].remove(group)
        graph["nodes"].insert(0, group)


def ungroup(graph: dict[str, Any], group_id: str) -> None:
    """拆开分组：子节点回到画布上的绝对位置，分组框删掉。"""
    group = find_node(graph, group_id)
    for node in graph["nodes"]:
        if node.get("parentId") == group_id:
            node.pop("parentId")
            node["position"] = {
                "x": node["position"]["x"] + group["position"]["x"],
                "y": node["position"]["y"] + group["position"]["y"],
            }
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != group_id]


def remove_node(graph: dict[str, Any], node_id: str) -> None:
    node = find_node(graph, node_id)
    if node["type"] == NODE_GROUP:
        # 删分组框 = 拆组，里面的节点保留。
        ungroup(graph, node_id)
        return
    if node["data"].get("status") in BUSY_STATUSES:
        raise CanvasGraphError("canvas_node_busy", id=node_id)
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != node_id]
    graph["edges"] = [e for e in graph["edges"] if node_id not in (e["source"], e["target"])]


def _handle_type(table: dict[str, dict[str, str]], node: dict[str, Any], handle: str | None) -> str | None:
    return table.get(node["type"], {}).get(handle or "")


def _compatible(out_type: str, in_type: str) -> bool:
    """同类型句柄可连；``asset`` 输出口还能接到接受图片的输入口（图当参考图，说明随图走）。"""
    return out_type == in_type or (out_type == HANDLE_ASSET and in_type == HANDLE_IMAGE)


def connect(graph: dict[str, Any], source: str, source_handle: str, target: str, target_handle: str) -> dict[str, Any]:
    src = _lookup(graph, source, None)
    dst = _lookup(graph, target, None)
    out_type = _handle_type(OUTPUT_HANDLES, src, source_handle)
    in_type = _handle_type(INPUT_HANDLES, dst, target_handle)
    if out_type is None or in_type is None or not _compatible(out_type, in_type) or source == target:
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


def _sheet_of_generator(node_id: str) -> str:
    """派生的图片供应商节点（``p:<x>_gen:<名>``）的输出就是同名的资产图节点（``p:<x>_sheet:<名>``）。"""
    kind = derived_kind(node_id)
    if kind is None or not kind.endswith("_gen"):
        return node_id
    return node_id.replace(f"p:{kind}:", f"p:{kind[: -len('_gen')]}_sheet:", 1)


def resolve_asset_node(graph: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """资产节点的有效内容：图取自接在它 ``image`` 口上的节点（供应商节点取最近一次产物），名称与类型取自所在的资产分组。"""
    data = dict(node["data"])
    group = next((n for n in graph["nodes"] if n["id"] == node.get("parentId") and n["type"] == NODE_GROUP), None)
    if group is not None and group["data"].get("asset"):
        data["asset"] = group["data"]["asset"]
        data["name"] = str(group["data"].get("label") or data.get("name") or "").strip()
    edge = next((e for e in graph["edges"] if e["target"] == node["id"] and e["targetHandle"] == "image"), None)
    source = next((n for n in graph["nodes"] if edge and n["id"] == edge["source"]), None)
    if source is not None and source["type"] in PROVIDER_MEDIA:
        source = latest_output(graph, source["id"], edge["sourceHandle"] if edge else "image")
    if source is not None and isinstance(source["data"].get("path"), str) and source["data"]["path"]:
        data["path"] = source["data"]["path"]
        data["version"] = source["data"].get("version")
    return {**node, "data": data}


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
            source_id = _sheet_of_generator(e["source"])
            if derived is not None and source_id in derived:
                sources.append(derived[source_id])
        else:
            source = find_node(graph, e["source"])
            if source["type"] in PROVIDER_MEDIA:
                # 直接连供应商节点的输出句柄 = 取它最近一次运行的产物，重跑它后下游自动用上新结果。
                latest = latest_output(graph, source["id"], e["sourceHandle"])
                if latest is not None:
                    sources.append(latest)
            elif source["type"] == NODE_ASSET:
                sources.append(resolve_asset_node(graph, source))
            else:
                sources.append(source)
    return sorted(sources, key=lambda n: (_abs_position(n)["y"], _abs_position(n)["x"]))


#: 供应商节点的输入句柄：(文本句柄, 参考图句柄 | None)。
_INPUT_HANDLE_NAMES: dict[str, tuple[str, str | None]] = {
    NODE_IMAGE_GENERATE: ("prompt", "image1"),
    NODE_VIDEO_GENERATE: ("prompt", "frame"),
    NODE_AUDIO_GENERATE: ("text", None),
}


def _reference_label(node: dict[str, Any]) -> str:
    data = node["data"]
    if node["type"] == NODE_ASSET:
        return str(data.get("note") or default_note(data))
    return str(data.get("note") or data.get("label") or data.get("name") or "")


def _mention_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """提示词里 ``@名称`` 能指到的资产：名称（衍生为 ``名称/衍生名``）→ 它的资产节点（已解析出图与名称）。重名时先出现的优先。"""
    index: dict[str, dict[str, Any]] = {}
    for node in graph["nodes"]:
        if node["type"] != NODE_ASSET:
            continue
        resolved = resolve_asset_node(graph, node)
        data = resolved["data"]
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        key = f"{name}/{data['derivative']}" if data.get("derivative") else name
        index.setdefault(key, resolved)
    return index


def expand_mentions(
    prompt: str,
    refs: list[tuple[str, str]],
    index: Mapping[str, dict[str, Any]],
    *,
    strict: bool,
) -> tuple[str, list[tuple[str, str]]]:
    """把提示词里的 ``@资产名`` 换成 ``图N``，并把该资产的图（连同说明）补进参考图列表。

    已经连着的参考图沿用原编号，没连的追加在后面；同一资产多次出现编号一致。名称按长度从长到短匹配，
    ``@Xe Land Rover 2`` 不会被 ``@Xe Land Rover`` 抢走；``@[名称]`` 与 ``@名称`` 两种写法都认。没有对应资产的 ``@xxx`` 原样保留。资产还没有图时：
    ``strict`` 抛 ``canvas_mention_no_image``，否则原样保留（读画布时不能因此失败）。
    """
    if "@" not in prompt or not index:
        return prompt, refs
    alternatives = "|".join(re.escape(key) for key in sorted(index, key=len, reverse=True))
    # 两种写法：项目剧本里的 ``@[名称]``（括号界定，名称里可含任何字符）和随手写的 ``@名称``。
    pattern = re.compile(rf"@\[({alternatives})\]|@({alternatives})(?!\w)")
    merged = list(refs)

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        data = index[name]["data"]
        path = data.get("path")
        if not isinstance(path, str) or not path:
            if strict:
                raise CanvasGraphError("canvas_mention_no_image", name=name)
            return match.group(0)
        for position, (existing, _label) in enumerate(merged, start=1):
            if existing == path:
                return f"图{position}"
        merged.append((path, str(data.get("note") or name)))
        return f"图{len(merged)}"

    return pattern.sub(_replace, prompt), merged


def _resolved_inputs(
    graph: dict[str, Any], node_id: str, derived: Mapping[str, dict[str, Any]] | None, *, strict: bool
) -> tuple[str, list[tuple[str, str]]]:
    node = find_node(graph, node_id)
    handles = _INPUT_HANDLE_NAMES.get(node["type"])
    if handles is None:
        raise CanvasGraphError("canvas_node_not_runnable", id=node_id)
    text_handle = handles[0]
    texts = [str(n["data"].get("text") or "").strip() for n in _incoming(graph, node_id, text_handle, derived)]
    prompt = "\n\n".join(t for t in texts if t)
    refs = [(n["data"]["path"], _reference_label(n)) for n in _reference_nodes(graph, node_id, derived)]
    if node["type"] == NODE_IMAGE_GENERATE:
        prompt, refs = expand_mentions(prompt, refs, _mention_index(graph), strict=strict)
    return prompt, refs


def resolve_generate_inputs(
    graph: dict[str, Any],
    node_id: str,
    derived: Mapping[str, dict[str, Any]] | None = None,
    *,
    strict: bool = False,
) -> tuple[str, list[str]]:
    """供应商节点的输入：``(拼接后的文本, 参考图项目相对路径列表)``。

    文本来自文本节点与视频单元正文；参考图来自图片节点与起始帧联合图（图片节点的 ``image1``、视频节点的
    ``frame``）。只收已有产物的参考图，占位中的上游图片（还没有 path）被跳过；语音节点没有参考图。
    图片供应商的提示词里的 ``@资产名`` 会换成 ``图N`` 并补进参考图（见 :func:`expand_mentions`）。
    """
    prompt, refs = _resolved_inputs(graph, node_id, derived, strict=strict)
    return prompt, [path for path, _label in refs]


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


_NOTE_ROLES = ("sheet", "derivative", "reference")


def default_note(data: Mapping[str, Any]) -> str:
    """资产图的默认参考说明：随图一起交给供应商，让它知道这张图是谁。可被用户改写。"""
    name = str(data.get("name") or "")
    if data.get("role") == "reference":
        return f"Reference photo of {name}."
    if data.get("role") == "derivative" or data.get("derivative"):
        return f"{name}, {data.get('derivative') or ''} version.".replace(" ,", ",")
    asset = str(data.get("asset") or "character")
    prefix = {"character": "Character sheet of", "scene": "Scene:", "prop": "Prop:", "product": "Product:"}.get(
        asset, "Reference:"
    )
    return f"{prefix} {name}."


def apply_notes(derived_nodes: list[dict[str, Any]], notes: Mapping[str, str]) -> None:
    """给派生的资产图节点写上参考说明：用户改写过的优先，否则用默认说明。"""
    for node in derived_nodes:
        data = node["data"]
        if node["type"] == "image" and data.get("role") in _NOTE_ROLES:
            data["note"] = str(notes.get(node["id"]) or default_note(data))


def set_note(graph: dict[str, Any], node_id: str, note: str) -> None:
    """设置节点的参考说明；派生节点（资产图）存在画布的 ``notes``，落盘节点存在 data 里。空串恢复默认 / 清除。"""
    text = note.strip()
    if derived_kind(node_id) is not None:
        if text:
            graph["notes"][node_id] = text
        else:
            graph["notes"].pop(node_id, None)
        return
    data = find_node(graph, node_id)["data"]
    if text:
        data["note"] = text
    else:
        data.pop("note", None)


def resolve_reference_labels(
    graph: dict[str, Any], node_id: str, derived: Mapping[str, dict[str, Any]] | None = None, *, strict: bool = False
) -> list[str]:
    """与 :func:`resolve_generate_inputs` 返回的参考图等长同序的人读名称（优先参考说明），用于「图N」编号说明。"""
    _prompt, refs = _resolved_inputs(graph, node_id, derived, strict=strict)
    return [label for _path, label in refs]


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


#: 产物节点放进分组时给它留的宽 / 高（图片节点约 300 宽、视频 / 语音更矮）。
OUTPUT_GROUP_WIDTH = 340.0
OUTPUT_GROUP_HEIGHT = 480.0


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
    parent_id = source.get("parentId")
    group = next((n for n in graph["nodes"] if n["id"] == parent_id and n["type"] == NODE_GROUP), None)
    if group is not None:
        # 供应商在分组里：产物与它同组（位置本来就是相对分组的），分组放不下就撑大一点。
        media_node["parentId"] = group["id"]
        group["data"]["width"] = max(float(group["data"].get("width") or 0), x + OUTPUT_GROUP_WIDTH)
        group["data"]["height"] = max(float(group["data"].get("height") or 0), y + OUTPUT_GROUP_HEIGHT)
    now = utc_now_iso()
    media_node["data"].update(
        {"status": STATUS_QUEUED, "prompt": prompt, "generated": True, "submitted_at": now, "status_at": now}
    )
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


#: 已移除的集相关推导节点种类；旧画布里残留的这类引用在读盘时清掉。
_REMOVED_EPISODE_KINDS = ("episode", "unit", "keyframe", "h3_prompt")


def wire_result_to_asset(graph: dict[str, Any], generator_id: str, media_id: str) -> None:
    """一次运行出了图：生成器在某个资产分组里、且这个组只有一个（非衍生）资产节点时，最新的图接到资产节点上取代旧的。"""
    generator = find_node(graph, generator_id)
    group = next((n for n in graph["nodes"] if n["id"] == generator.get("parentId") and n["type"] == NODE_GROUP), None)
    if group is None or not group["data"].get("asset"):
        return
    assets = [
        n
        for n in graph["nodes"]
        if n.get("parentId") == group["id"] and n["type"] == NODE_ASSET and not n["data"].get("derivative")
    ]
    if len(assets) != 1:
        return
    target = assets[0]["id"]
    graph["edges"] = [e for e in graph["edges"] if not (e["target"] == target and e["targetHandle"] == "image")]
    graph["edges"].append(
        {
            "id": new_id("edge"),
            "source": media_id,
            "sourceHandle": "image",
            "target": target,
            "targetHandle": "image",
        }
    )


def _adopt_stray_outputs(graph: dict[str, Any]) -> None:
    """早先运行的产物没跟供应商同组（位置却是按组内算的，于是跑到画布远处）：补上同组，位置不用改。"""
    by_id = {n["id"]: n for n in graph["nodes"]}
    for edge in graph["edges"]:
        media = by_id.get(edge["target"])
        source = by_id.get(edge["source"])
        if (
            media is None
            or source is None
            or media.get("parentId")
            or not media["data"].get("generated")
            or edge["targetHandle"] != "in"
        ):
            continue
        parent = by_id.get(source.get("parentId") or "")
        if parent is not None and parent["type"] == NODE_GROUP:
            media["parentId"] = parent["id"]


def normalize_legacy(graph: dict[str, Any]) -> None:
    """把旧版落盘的画布迁移到当前形状（读盘时调用，下次写盘即固化）。

    - 图片供应商的参考图输入句柄 ``refs`` 改名 ``image1``；
    - 供应商不再输出「使用的提示词」文本节点：删掉这些自动生成的文本节点及其连线；
    - 集视图已移除：丢弃 ``view``、H3 写作节点，以及连到已不存在的集 / 单元 / 起始帧联合图推导节点的连线。
    """
    graph.pop("view", None)
    _adopt_stray_outputs(graph)
    removed = {n["id"] for n in graph["nodes"] if n["type"] == "h3_writer"}
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in removed]
    gone = tuple(f"{DERIVED_PREFIX}{kind}:" for kind in _REMOVED_EPISODE_KINDS)
    graph["edges"] = [
        e
        for e in graph["edges"]
        if e["source"] not in removed
        and e["target"] not in removed
        and not e["source"].startswith(gone)
        and not e["target"].startswith(gone)
    ]
    graph["overrides"] = {k: v for k, v in graph.get("overrides", {}).items() if not k.startswith(gone)}
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

    只收「还没有可用产物」或「产物已过期」的供应商节点，已是最新的上游不重跑。
    """
    node = find_node(graph, target_id)
    if node["type"] != NODE_IMAGE_GENERATE:
        raise CanvasGraphError("canvas_node_not_runnable", id=target_id)
    stale = stale_generated_images(graph, derived or {})
    order: list[dict[str, str]] = []
    seen: set[str] = set()

    def needs_run(upstream: dict[str, Any]) -> str | None:
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
TEMPLATE_NODE_TYPES = (NODE_TEXT, NODE_IMAGE_GENERATE, NODE_VIDEO_GENERATE, NODE_AUDIO_GENERATE)


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
