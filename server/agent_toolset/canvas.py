"""节点画布工具的声明：读画布、增删改节点与连线、选择展示的集、运行节点。

画布是项目的可视化创作图（见 CONTEXT.md「节点画布」）。Agent 与网页共用 ``CanvasService``，
改动经文件锁落盘，网页轮询读到后即时出现；运行类操作复用既有生成链路，立即返回任务 id。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.canvas.graph import CanvasGraphError
from lib.generation.generation_queue import get_generation_queue
from lib.i18n import _ as translate
from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.services.project.canvas_service import CanvasService
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolProblem, ToolRequest

_TEXT_PREVIEW_CHARS = 600

PositiveEpisode = Annotated[int, Field(ge=1)]


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(description="画布横坐标；向右为正")
    y: float = Field(description="画布纵坐标；向下为正")


class GetCanvasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanvasAddNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["text", "image_generate", "h3_writer"] = Field(
        description="节点类型：text 文本（提示词 / 备注）；image_generate 图片供应商（可运行）；h3_writer H3 提示词写作（可运行）"
    )
    position: Position = Field(
        description="放置位置。先 get_canvas 看现有节点的坐标，避免重叠；节点宽约 280，纵向间隔取 ≥ 300"
    )
    text: str | None = Field(default=None, description="type=text 时的正文")
    aspect_ratio: Literal["9:16", "16:9", "1:1", "3:4", "4:3"] | None = Field(
        default=None, description="type=image_generate 时的画幅，缺省 9:16"
    )


class CanvasPatchNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, description="节点 id（get_canvas 返回）")
    position: Position | None = Field(default=None, description="新位置；缺省不动")
    text: str | None = Field(default=None, description="新正文，仅文本节点；传空串即清空")
    aspect_ratio: Literal["9:16", "16:9", "1:1", "3:4", "4:3"] | None = Field(
        default=None, description="新画幅，仅图片供应商节点"
    )


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


class CanvasSetEpisodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episodes: list[PositiveEpisode] = Field(
        description="画布上展示项目节点（集分组、视频单元、起始帧联合图、H3 提示词）的集；整份替换，空列表即全部隐藏。仅参考生视频项目"
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
    return out


async def get_canvas(
    _request: ToolRequest[GetCanvasRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        view = await _service(services).view(scope.project_name)
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_canvas 失败: {exc}"))
    return ToolOutcome(
        value={
            "revision": view["revision"],
            "shown_episodes": view["view"]["episodes"],
            "available_episodes": view["episodes"],
            "nodes": [_digest(n) for n in view["nodes"]],
            "edges": [
                {k: e[k] for k in ("id", "source", "sourceHandle", "target", "targetHandle")}
                | ({"virtual": True} if e.get("virtual") else {})
                for e in view["edges"]
            ],
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
    try:
        node, revision = await _service(services).patch_node(
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


async def canvas_set_episodes(
    request: ToolRequest[CanvasSetEpisodesRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    episodes, revision = await _service(services).set_view(scope.project_name, list(request.value.episodes))
    return ToolOutcome(value={"shown_episodes": episodes, "revision": revision})


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


_HANDLES_NOTE = (
    "句柄（只有同数据类型的句柄能连）：text 节点输出 text；image 节点输出 image；"
    "image_generate 输入 prompt(text)、refs(image)，输出 text、image；h3_writer 输入 units(unit)，输出 h3；"
    "视频单元节点 p:unit:<集>:<单元> 输出 unit、text；起始帧联合图节点 p:keyframe:<集>:<联合图> 输出 image。"
)

GET_CANVAS = ToolDeclaration(
    name="get_canvas",
    description=(
        "读取项目的节点画布：节点（落盘节点与按选中集推导的只读项目节点）、连线、展示中的集与可展示的集。"
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
        "在画布上新增一个节点（文本 / 图片供应商 / H3 写作）。图片节点由用户上传或运行产出，不能新增。"
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
    description="删除画布上的一个落盘节点及其连线；正在排队或运行的节点不能删。项目推导的只读节点不能删（用 canvas_set_episodes 隐藏整集）。",
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

CANVAS_SET_EPISODES = ToolDeclaration(
    name="canvas_set_episodes",
    description=(
        "选择画布上展示哪几集的项目节点（集分组含视频单元、起始帧联合图与 H3 提示词），只影响展示，不动项目数据。"
        "仅参考生视频项目有可展示的集。"
    ),
    request_model=CanvasSetEpisodesRequest,
    migration=BLOCKED,
    domain_key="canvas_view",
    handler=canvas_set_episodes,
)

CANVAS_RUN_NODE = ToolDeclaration(
    name="canvas_run_node",
    description=(
        "运行一个节点并立即返回任务 id（不等结果）：image_generate 读入连进来的提示词与参考图，挂出「使用的提示词」与图片两个输出节点；"
        "h3_writer 为它所连的视频单元重写 H3 提示词（单元正文原样保留）；p:keyframe 节点生成这张起始帧联合图。"
        "结果出现在画布上，用 get_canvas 查看进度。"
    ),
    request_model=CanvasNodeRequest,
    migration=BLOCKED,
    domain_key="canvas_run",
    handler=canvas_run_node,
)

CANVAS_TOOLS = (
    GET_CANVAS,
    CANVAS_ADD_NODE,
    CANVAS_PATCH_NODE,
    CANVAS_DELETE_NODE,
    CANVAS_CONNECT,
    CANVAS_DISCONNECT,
    CANVAS_SET_EPISODES,
    CANVAS_RUN_NODE,
)

__all__ = ["CANVAS_TOOLS"]
