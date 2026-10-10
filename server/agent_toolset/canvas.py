"""节点画布工具的声明：读画布、增删改节点与连线、选择展示的集、运行节点。

画布是项目的可视化创作图（见 CONTEXT.md「节点画布」）。Agent 与网页共用 ``CanvasService``，
改动经文件锁落盘，网页轮询读到后即时出现；运行类操作复用既有生成链路，立即返回任务 id。
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.canvas.graph import CanvasGraphError
from lib.generation.generation_queue import get_generation_queue
from lib.i18n import _ as translate
from lib.infra.path_safety import safe_join
from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.services.project.canvas_service import UPLOAD_EXTS, UPLOAD_MAX_BYTES, CanvasService
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolProblem, ToolRequest

_TEXT_PREVIEW_CHARS = 600


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(description="画布横坐标；向右为正")
    y: float = Field(description="画布纵坐标；向下为正")


class GetCanvasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_type: str | None = Field(
        default=None, description="只返回这个类型的节点（如 text / image_generate / character / scene）；缺省返回全部"
    )
    id_contains: str | None = Field(default=None, description="只返回 id 含该子串的节点（不分大小写）")
    include_edges: bool = Field(default=True, description="false 时不返回连线，读大画布时先看节点用")


class CanvasAddNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["text", "image_generate", "video_generate", "audio_generate"] = Field(
        description=(
            "节点类型：text 文本（提示词 / 备注）；image_generate 图片供应商；"
            "video_generate 视频供应商（提示词 + 可选起始帧图 → 视频）；audio_generate 语音供应商（文本 → 语音）。后三类可运行"
        )
    )
    position: Position = Field(
        description="放置位置。先 get_canvas 看现有节点的坐标，避免重叠；节点宽约 280，纵向间隔取 ≥ 300"
    )
    text: str | None = Field(default=None, description="type=text 时的正文")
    aspect_ratio: Literal["9:16", "16:9", "1:1", "3:4", "4:3"] | None = Field(
        default=None, description="type=image_generate 时的画幅，缺省 9:16"
    )
    model: str | None = Field(
        default=None,
        description="type=image_generate 时指定的图像模型，形如 供应商/模型（如 gemini-aistudio/imagen-4）；缺省用项目默认",
    )
    duration_seconds: int | None = Field(
        default=None,
        ge=1,
        le=60,
        description="type=video_generate 时的视频时长（秒），缺省 5；须是所用视频模型支持的时长",
    )
    voice: str | None = Field(default=None, description="type=audio_generate 时的音色名；缺省用项目的旁白音色")


class CanvasPatchNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, description="节点 id（get_canvas 返回）")
    position: Position | None = Field(default=None, description="新位置；缺省不动")
    text: str | None = Field(default=None, description="新正文，仅文本节点；传空串即清空")
    aspect_ratio: Literal["9:16", "16:9", "1:1", "3:4", "4:3"] | None = Field(
        default=None, description="新画幅，仅图片供应商节点"
    )
    model: str | None = Field(
        default=None, description="新图像模型（供应商/模型），仅图片供应商节点；传空串恢复项目默认"
    )
    note: str | None = Field(
        default=None,
        description=(
            "参考说明，图片节点（含项目资产图 p:*_sheet）专用：这张图被别的供应商节点当参考图时，说明随图一起交给供应商，"
            "如「This is his face, ...」。传空串恢复默认（资产图）或清除"
        ),
    )
    duration_seconds: int | None = Field(default=None, ge=1, le=60, description="新视频时长（秒），仅视频供应商节点")
    voice: str | None = Field(default=None, description="新音色名，仅语音供应商节点；传空串恢复项目默认")


class CanvasNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, description="节点 id（get_canvas 返回）")


class CanvasConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, description="上游节点 id")
    source_handle: str = Field(min_length=1, description="上游节点的输出句柄 id")
    target: str = Field(min_length=1, description="下游节点 id")
    target_handle: str = Field(min_length=1, description="下游节点的输入句柄 id")


class CanvasDisconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str = Field(min_length=1, description="连线 id（get_canvas 返回）；由项目数据推导出的虚拟连线不能删")


class CanvasSaveTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80, description="模板名")
    node_ids: list[str] = Field(min_length=1, description="要存进模板的节点 id；节点之间的连线一并保存")


class CanvasUseTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: str = Field(min_length=1, description="模板 id（get_canvas 的 templates 返回）")
    position: Position = Field(description="展开位置（模板左上角）")


class CanvasImportEpisodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode: int = Field(ge=1, description="集号；已经在画布上就返回现有分组")


class CanvasImportAssetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanvasGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_ids: list[str] = Field(min_length=1, description="要放进新分组的节点 id")
    label: str = Field(min_length=1, max_length=80, description="分组名")
    position: Position = Field(description="分组左上角")
    width: float = Field(default=900, ge=200, description="分组宽")
    height: float = Field(default=600, ge=120, description="分组高")


class CanvasUngroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str = Field(min_length=1, description="分组节点 id；只拆分组，组内节点与项目资产保留")


class CanvasCreateAssetGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["character", "scene", "prop", "product"] = Field(description="资产类型")
    name: str = Field(min_length=1, max_length=80, description="资产名（分组标题，也是项目里的资产名）")
    description: str = Field(default="", max_length=20000, description="资产描述；有描述才会在项目里建出对应资产")
    position: Position = Field(description="分组左上角；先 get_canvas 看现有节点的坐标，避免重叠")


class CanvasSetNodeImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str = Field(
        min_length=1,
        description="项目内的图片文件相对路径（png / jpg / jpeg / webp，≤ 20MB），如 source/ref.png；不能指向项目外",
    )
    position: Position = Field(description="新建图片节点时的位置；填进已有图片节点时忽略")
    node_id: str | None = Field(
        default=None, description="填进（或换掉）这个已有的上传图片节点；缺省则新建一个图片节点"
    )
    asset_node_id: str | None = Field(
        default=None, description="给了就顺手把这张图连到该资产节点的 image 输入口：这张图即成为该项目资产的资产图"
    )


def _problem(exc: CanvasGraphError) -> ToolProblem:
    return ToolProblem(exc.code, translate(exc.code, **exc.params))


def _service(services: Services) -> CanvasService:
    return CanvasService(services.projects, get_generation_queue())


def _digest(node: dict[str, Any]) -> dict[str, Any]:
    """给 Agent 的节点摘要：长文本截断，H3 六段只给各段前缀，避免一次读画布吃掉上下文。"""
    data = dict(node["data"])
    for key in ("text", "prompt"):
        if isinstance(data.get(key), str) and len(data[key]) > _TEXT_PREVIEW_CHARS:
            data[key] = data[key][:_TEXT_PREVIEW_CHARS] + "…"
    if isinstance(data.get("sections"), dict):
        data["sections"] = {k: str(v)[:120] for k, v in data["sections"].items()}
    out = {"id": node["id"], "type": node["type"], "position": node.get("abs") or node["position"], "data": data}
    if node.get("derived"):
        out["read_only"] = True
    if node.get("parentId"):
        out["parent_id"] = node["parentId"]
    return out


async def get_canvas(
    request: ToolRequest[GetCanvasRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    try:
        view = await _service(services).view(scope.project_name)
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_canvas 失败: {exc}"))
    needle = value.id_contains.lower() if value.id_contains else None
    nodes = [
        n
        for n in view["nodes"]
        if (value.node_type is None or n["type"] == value.node_type) and (needle is None or needle in n["id"].lower())
    ]
    kept = {n["id"] for n in nodes}
    filtered = value.node_type is not None or needle is not None
    edges = [
        {k: e[k] for k in ("id", "source", "sourceHandle", "target", "targetHandle")}
        | ({"virtual": True} if e.get("virtual") else {})
        for e in view["edges"]
        if not filtered or e["source"] in kept or e["target"] in kept
    ]
    return ToolOutcome(
        value={
            "revision": view["revision"],
            "templates": view["templates"],
            "node_count": len(nodes),
            "nodes": [_digest(n) for n in nodes],
            "edges": edges if value.include_edges else [],
        }
    )


async def canvas_add_node(
    request: ToolRequest[CanvasAddNodeRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    data: dict[str, Any] = {}
    if value.text is not None:
        data["text"] = value.text
    if value.aspect_ratio is not None:
        data["aspect_ratio"] = value.aspect_ratio
    if value.model is not None:
        data["model"] = value.model
    if value.duration_seconds is not None:
        data["duration_seconds"] = value.duration_seconds
    if value.voice is not None:
        data["voice"] = value.voice
    try:
        node, revision = await _service(services).add_node(
            scope.project_name, value.type, value.position.model_dump(), data
        )
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"node": _digest(node), "revision": revision})


async def canvas_patch_node(
    request: ToolRequest[CanvasPatchNodeRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    data: dict[str, Any] = {}
    if value.text is not None:
        data["text"] = value.text
    if value.aspect_ratio is not None:
        data["aspect_ratio"] = value.aspect_ratio
    if value.model is not None:
        data["model"] = value.model or None
    if value.duration_seconds is not None:
        data["duration_seconds"] = value.duration_seconds
    if value.voice is not None:
        data["voice"] = value.voice or None
    service = _service(services)
    try:
        if value.note is not None:
            revision = await service.set_note(scope.project_name, value.node_id, value.note)
            if not data and value.position is None:
                return ToolOutcome(value={"node_id": value.node_id, "note": value.note.strip(), "revision": revision})
        node, revision = await service.patch_node(
            scope.project_name, value.node_id, value.position.model_dump() if value.position else None, data
        )
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"node": _digest(node), "revision": revision})


async def canvas_delete_node(
    request: ToolRequest[CanvasNodeRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        revision = await _service(services).delete_node(scope.project_name, request.value.node_id)
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"deleted": request.value.node_id, "revision": revision})


async def canvas_connect(
    request: ToolRequest[CanvasConnectRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    try:
        edge, revision = await _service(services).connect(
            scope.project_name, value.source, value.source_handle, value.target, value.target_handle
        )
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"edge": edge, "revision": revision})


async def canvas_disconnect(
    request: ToolRequest[CanvasDisconnectRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        revision = await _service(services).disconnect(scope.project_name, request.value.edge_id)
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"deleted": request.value.edge_id, "revision": revision})


async def canvas_run_node(
    request: ToolRequest[CanvasNodeRequest], scope: ProjectScope, caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        result = await _service(services).run(
            scope.project_name, request.value.node_id, user_id=caller.user_id, source=caller.source
        )
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"canvas_run_node 失败: {exc}"))
    return ToolOutcome(value=result)


async def canvas_save_template(
    request: ToolRequest[CanvasSaveTemplateRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        template, revision = await _service(services).save_template(
            scope.project_name, request.value.name, list(request.value.node_ids)
        )
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"template": template, "revision": revision})


async def canvas_use_template(
    request: ToolRequest[CanvasUseTemplateRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    try:
        nodes, revision = await _service(services).use_template(
            scope.project_name, value.template_id, value.position.model_dump()
        )
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"nodes": [_digest(n) for n in nodes], "revision": revision})


async def canvas_import_episode(
    request: ToolRequest[CanvasImportEpisodeRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        group, revision = await _service(services).import_episode(scope.project_name, request.value.episode)
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"group": _digest(group), "revision": revision})


async def canvas_import_assets(
    request: ToolRequest[CanvasImportAssetsRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        created, revision = await _service(services).import_assets(scope.project_name)
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"created_groups": created, "revision": revision})


async def canvas_create_asset_group(
    request: ToolRequest[CanvasCreateAssetGroupRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    service = _service(services)
    try:
        group, revision = await service.create_asset_group(
            scope.project_name, value.kind, value.name.strip(), value.description, value.position.model_dump()
        )
        view = await service.view(scope.project_name)
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    children = [_digest(n) for n in view["nodes"] if n.get("parentId") == group["id"]]
    return ToolOutcome(value={"group": _digest(group), "children": children, "revision": revision})


async def canvas_set_node_image(
    request: ToolRequest[CanvasSetNodeImageRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    service = _service(services)
    suffix = "." + value.file.rsplit(".", 1)[-1].lower() if "." in value.file else ""
    if suffix not in UPLOAD_EXTS:
        return ToolOutcome(
            problem=ToolProblem(
                "canvas_upload_ext_unsupported",
                translate("canvas_upload_ext_unsupported", allowed=", ".join(UPLOAD_EXTS)),
            )
        )
    project_path = await asyncio.to_thread(service.pm.get_project_path, scope.project_name)
    try:
        source = safe_join(project_path, value.file, must_exist=True, require_file=True)
    except (ValueError, FileNotFoundError, OSError):
        return ToolOutcome(
            problem=ToolProblem(
                "canvas_image_file_not_found", translate("canvas_image_file_not_found", file=value.file)
            )
        )
    content = await asyncio.to_thread(source.read_bytes)
    if len(content) > UPLOAD_MAX_BYTES:
        return ToolOutcome(
            problem=ToolProblem(
                "upload_too_large", translate("upload_too_large", max_mb=UPLOAD_MAX_BYTES // (1024 * 1024))
            )
        )
    try:
        node, revision = await service.place_image(
            scope.project_name, content, suffix, source.name, value.position.model_dump(), value.node_id
        )
        edge = None
        if value.asset_node_id is not None:
            edge, revision = await service.connect(
                scope.project_name, node["id"], "image", value.asset_node_id, "image"
            )
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"node": _digest(node), "edge": edge, "revision": revision})


async def canvas_group(
    request: ToolRequest[CanvasGroupRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    try:
        group, revision = await _service(services).group_nodes(
            scope.project_name,
            list(value.node_ids),
            value.label,
            value.position.model_dump(),
            {"width": value.width, "height": value.height},
        )
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"group": _digest(group), "revision": revision})


async def canvas_ungroup(
    request: ToolRequest[CanvasUngroupRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        revision = await _service(services).delete_node(scope.project_name, request.value.group_id)
    except CanvasGraphError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value={"ungrouped": request.value.group_id, "revision": revision})


_HANDLES_NOTE = (
    "句柄（只有同数据类型的句柄能连）：text 节点输出 text；image 节点输出 image；"
    "image_generate 输入 prompt(text)、image1(image)，输出 image（一张图）；"
    "image_generate 的输出句柄 image 也可直接连到另一个 image_generate 的 image1，或连到 video_generate 的 frame（起始帧）：运行时取它最近一次产出的图。"
    "video_generate 输入 prompt(text)、frame(image)，输出 video；audio_generate 输入 text(text)，输出 audio。"
    "角色 / 场景 / 道具 / 商品都是普通分组（group）：组内是文本 → 图片供应商 → 图片 → 资产节点（asset，输出 image）。"
    "资产节点 = 发布键：它的输入图成为项目资产图；删除分组只从画布移除，项目资产保留。"
    "提示词里写 @[名称] 即自动带上同名资产的图，无需连线。"
    "文档（markdown）节点只在彼此之间连：输出口 out → 输入口 in，表示「从这份文档得来」（EP source → Screenplay → Script plan）。"
)

GET_CANVAS = ToolDeclaration(
    name="get_canvas",
    description=(
        "读取项目的节点画布：节点（落盘节点与项目推导的只读节点）与连线。"
        "画布是用户在界面上直接看到的创作图，你的改动会实时出现在他们面前。" + _HANDLES_NOTE + "只读，无副作用。"
    ),
    request_model=GetCanvasRequest,
    migration=Exempt("只读：读取画布与项目文件，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key="canvas",
    handler=get_canvas,
)

CANVAS_ADD_NODE = ToolDeclaration(
    name="canvas_add_node",
    description=(
        "在画布上新增一个节点（文本 / 图片供应商 / 视频供应商 / 语音供应商）。图片 / 视频 / 语音节点由用户上传或运行产出，不能新增。"
        "新增后用 canvas_connect 连线，再用 canvas_run_node 运行。"
    ),
    request_model=CanvasAddNodeRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_add_node,
)

CANVAS_PATCH_NODE = ToolDeclaration(
    name="canvas_patch_node",
    description="修改画布节点：位置、文本节点的正文、图片供应商节点的画幅。服务端维护的状态字段（status、path 等）不接受修改。",
    request_model=CanvasPatchNodeRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_patch_node,
)

CANVAS_DELETE_NODE = ToolDeclaration(
    name="canvas_delete_node",
    description="删除画布上的一个落盘节点及其连线；正在排队或运行的节点不能删。项目推导的只读节点不能删。",
    request_model=CanvasNodeRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_delete_node,
)

CANVAS_CONNECT = ToolDeclaration(
    name="canvas_connect",
    description="连接两个节点的句柄；两端数据类型须一致，否则拒绝。" + _HANDLES_NOTE,
    request_model=CanvasConnectRequest,
    migration=BLOCKED,
    domain_key="canvas_edge",
    handler=canvas_connect,
)

CANVAS_DISCONNECT = ToolDeclaration(
    name="canvas_disconnect",
    description="删除一条连线。",
    request_model=CanvasDisconnectRequest,
    migration=BLOCKED,
    domain_key="canvas_edge",
    handler=canvas_disconnect,
)

CANVAS_RUN_NODE = ToolDeclaration(
    name="canvas_run_node",
    description=(
        "运行一个节点并立即返回任务 id（不等结果）：image_generate / video_generate / audio_generate 读入连进来的文本（与参考图 / 起始帧），挂出一个图片 / 视频 / 语音输出节点；"
        "结果出现在画布上，用 get_canvas 查看进度。"
    ),
    request_model=CanvasNodeRequest,
    migration=BLOCKED,
    domain_key="canvas_run",
    handler=canvas_run_node,
)

CANVAS_SAVE_TEMPLATE = ToolDeclaration(
    name="canvas_save_template",
    description=(
        "把画布上选中的节点（文本、图片供应商、视频供应商、语音供应商）与它们之间的连线存成模板，便于在别的位置重用。"
        "运行产出的图片、上传的图片与项目推导节点不入模板。"
    ),
    request_model=CanvasSaveTemplateRequest,
    migration=BLOCKED,
    domain_key="canvas_template",
    handler=canvas_save_template,
)

CANVAS_USE_TEMPLATE = ToolDeclaration(
    name="canvas_use_template",
    description="在指定位置展开一个画布模板：新建其中的节点与连线（id 全部重新生成），返回新节点。",
    request_model=CanvasUseTemplateRequest,
    migration=BLOCKED,
    domain_key="canvas_template",
    handler=canvas_use_template,
)

CANVAS_IMPORT_EPISODE = ToolDeclaration(
    name="canvas_import_episode",
    description=(
        "把一集的制作流程摆上画布：分组「EPxx · 标题」，内含来源与规划 markdown 节点，"
        "每个单元一行（单元脚本 → 导演 → 关键帧 → H3 提示词 → 生成视频 → 视频 → 配音文本 → 生成音色 → 配音）。"
        "已在画布上则只补缺的：文档节点及其连线、还没上画布的单元行与关键帧板，已有节点不动；每完成一步就调一次让画布跟上。"
    ),
    request_model=CanvasImportEpisodeRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_import_episode,
)

CANVAS_IMPORT_ASSETS = ToolDeclaration(
    name="canvas_import_assets",
    description="把项目里有、画布上还没有分组的角色 / 场景 / 道具 / 商品摆上画布（每个一个分组）。",
    request_model=CanvasImportAssetsRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_import_assets,
)

CANVAS_CREATE_ASSET_GROUP = ToolDeclaration(
    name="canvas_create_asset_group",
    description=(
        "新建一个资产分组（角色 / 场景 / 道具 / 商品）：组内自带描述文本、图片供应商和资产节点，返回各子节点 id。"
        "描述不空就会在项目里建出对应资产。想用现成的图当资产图：再用 canvas_set_node_image 带上返回的资产节点 id。"
    ),
    request_model=CanvasCreateAssetGroupRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_create_asset_group,
)

CANVAS_SET_NODE_IMAGE = ToolDeclaration(
    name="canvas_set_node_image",
    description=(
        "把项目内的一张图片文件放上画布：新建图片节点，或填进已有的上传图片节点。"
        "给 asset_node_id 则同时连到该资产节点，这张图即成为项目资产的资产图。图片供应商产出的节点不能被替换。"
    ),
    request_model=CanvasSetNodeImageRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_set_node_image,
)

CANVAS_GROUP = ToolDeclaration(
    name="canvas_group",
    description="把若干节点放进一个新分组（只是画布上的归类，不改项目数据）。",
    request_model=CanvasGroupRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_group,
)

CANVAS_UNGROUP = ToolDeclaration(
    name="canvas_ungroup",
    description="拆掉一个分组：组内节点保留并回到画布，项目资产不受影响。",
    request_model=CanvasUngroupRequest,
    migration=BLOCKED,
    domain_key="canvas_node",
    handler=canvas_ungroup,
)

CANVAS_TOOLS = (
    GET_CANVAS,
    CANVAS_ADD_NODE,
    CANVAS_PATCH_NODE,
    CANVAS_DELETE_NODE,
    CANVAS_CONNECT,
    CANVAS_DISCONNECT,
    CANVAS_RUN_NODE,
    CANVAS_SAVE_TEMPLATE,
    CANVAS_USE_TEMPLATE,
    CANVAS_IMPORT_EPISODE,
    CANVAS_IMPORT_ASSETS,
    CANVAS_CREATE_ASSET_GROUP,
    CANVAS_SET_NODE_IMAGE,
    CANVAS_GROUP,
    CANVAS_UNGROUP,
)

__all__ = ["CANVAS_TOOLS"]
