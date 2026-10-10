"""画布路由：节点画布的读取、节点与连线的增删改、节点运行、图片上传。

业务规则在 ``CanvasService``（Agent 工具共用）；这里只做参数校验、错误码映射与上传落盘。
写操作都是细粒度的（不整图覆盖），因为服务端的 worker 会并发回写节点状态。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from lib.canvas.graph import CanvasGraphError
from lib.canvas.store import canvas_path
from lib.generation.generation_queue import get_generation_queue
from lib.infra.api_errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
)
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.dependencies import require_project_migration_ok
from server.services.project.canvas_service import UPLOAD_EXTS, UPLOAD_MAX_BYTES, CanvasService

router = APIRouter()

_UPLOAD_EXTS = UPLOAD_EXTS
_UPLOAD_MAX_BYTES = UPLOAD_MAX_BYTES

_GRAPH_ERRORS: dict[
    str, type[ConflictError] | type[NotFoundError] | type[UnprocessableError] | type[ServiceUnavailableError]
] = {
    "canvas_node_not_found": NotFoundError,
    "canvas_edge_not_found": NotFoundError,
    "canvas_node_busy": ConflictError,
    "canvas_worker_offline": ServiceUnavailableError,
    "canvas_template_not_found": NotFoundError,
}


def _raise(exc: CanvasGraphError) -> NoReturn:
    error_type = _GRAPH_ERRORS.get(exc.code, UnprocessableError)
    raise error_type(exc.code, **exc.params) from exc


def _service(project_name: str) -> CanvasService:
    pm = get_project_manager()
    try:
        pm.get_project_path(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return CanvasService(pm, get_generation_queue())


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float


class CreateNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "image", "image_generate", "video_generate", "audio_generate", "asset"]
    position: Position
    data: dict[str, Any] = Field(default_factory=dict)


class GroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_ids: list[str] = Field(min_length=2)
    label: str = Field(default="", max_length=80)
    position: Position
    width: float = Field(gt=0, le=20000)
    height: float = Field(gt=0, le=20000)


class AssetGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["character", "scene", "prop", "product"]
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=20000)
    position: Position


class MembersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_ids: list[str] = Field(min_length=1)
    group_id: str | None = None
    positions: dict[str, Position] = Field(default_factory=dict)


class PatchNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: Position | None = None
    data: dict[str, Any] | None = None


class LayoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    positions: dict[str, Position]


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    source_handle: str
    target: str
    target_handle: str


class SaveTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    node_ids: list[str] = Field(min_length=1)


class UseTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: Position


_STREAM_SCAN_SECONDS = 0.5
_STREAM_HEARTBEAT_SECONDS = 15.0
_WATCHED_DIRS = ("characters", "scenes", "props", "products", "scripts", "drafts")


def _canvas_signature(project_path: Path) -> str:
    """画布内容与它推导所依据的项目文件（project.json、各类资产图、剧本目录）的 mtime 指纹。"""
    parts: list[str] = []
    for path in (canvas_path(project_path), project_path / "project.json"):
        try:
            stat = path.stat()
            parts.append(f"{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append("-")
    for name in _WATCHED_DIRS:
        directory = project_path / name
        try:
            newest = max((p.stat().st_mtime_ns for p in directory.iterdir()), default=0)
            parts.append(f"{directory.stat().st_mtime_ns}:{newest}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


@router.get("/projects/{project_name}/canvas/stream", response_class=EventSourceResponse)
async def stream_canvas(project_name: str, request: Request, _user: CurrentUser) -> AsyncIterator[ServerSentEvent]:
    """画布变化推送：worker / Agent 写盘后立刻通知网页重新读取，网页不必定时轮询。

    写盘可能发生在另一个进程（队列 worker），所以这里只比对文件指纹，不依赖进程内通知。
    """
    try:
        project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc

    last = await asyncio.to_thread(_canvas_signature, project_path)
    yield ServerSentEvent(event="ready", data={"signature": last})
    idle = 0.0
    while not await request.is_disconnected():
        await asyncio.sleep(_STREAM_SCAN_SECONDS)
        current = await asyncio.to_thread(_canvas_signature, project_path)
        if current != last:
            last = current
            idle = 0.0
            yield ServerSentEvent(event="changed", data={"signature": current})
            continue
        idle += _STREAM_SCAN_SECONDS
        if idle >= _STREAM_HEARTBEAT_SECONDS:
            idle = 0.0
            yield ServerSentEvent(event="ping", data={})


@router.get("/projects/{project_name}/canvas")
async def get_canvas(project_name: str):
    return await _service(project_name).view(project_name)


@router.post("/projects/{project_name}/canvas/nodes", dependencies=[Depends(require_project_migration_ok)])
async def create_node(project_name: str, req: CreateNodeRequest):
    # 手动新建的资产节点不属于任何资产分组：名称自己定，不发布成项目资产。
    data = {**req.data, "standalone": True} if req.type == "asset" else req.data
    try:
        node, revision = await _service(project_name).add_node(project_name, req.type, req.position.model_dump(), data)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"node": node, "revision": revision}


@router.post(
    "/projects/{project_name}/canvas/nodes/{node_id}/duplicate", dependencies=[Depends(require_project_migration_ok)]
)
async def duplicate_node(project_name: str, node_id: str):
    """复制资产节点成可到处摆放的独立资产节点（名称、说明、图的来源随带）。"""
    try:
        node, revision = await _service(project_name).duplicate_asset(project_name, node_id)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"node": node, "revision": revision}


@router.post("/projects/{project_name}/canvas/groups", dependencies=[Depends(require_project_migration_ok)])
async def create_group(project_name: str, req: GroupRequest):
    """把选中的节点收进一个新分组（分组框的位置与大小由网页按节点实际尺寸算好）。"""
    try:
        group, revision = await _service(project_name).group_nodes(
            project_name,
            req.node_ids,
            req.label,
            req.position.model_dump(),
            {"width": req.width, "height": req.height},
        )
    except CanvasGraphError as exc:
        _raise(exc)
    return {"node": group, "revision": revision}


@router.post("/projects/{project_name}/canvas/asset-groups", dependencies=[Depends(require_project_migration_ok)])
async def create_asset_group(project_name: str, req: AssetGroupRequest):
    """新建一个资产分组（描述文本 + 图片供应商 + 资产节点）；描述有了就会在项目里建出对应资产。"""
    group, revision = await _service(project_name).create_asset_group(
        project_name, req.kind, req.name.strip(), req.description, req.position.model_dump()
    )
    return {"node": group, "revision": revision}


@router.post("/projects/{project_name}/canvas/assets/import", dependencies=[Depends(require_project_migration_ok)])
async def import_assets(project_name: str):
    """把项目里已有、画布上还没有的资产摆上画布（各成一个分组）。"""
    imported, revision = await _service(project_name).import_assets(project_name)
    return {"imported": imported, "revision": revision}


class UnitFieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["text", "direction"]
    value: str = Field(max_length=100000)


@router.get("/projects/{project_name}/canvas/flow")
async def get_flow(project_name: str):
    """总览：每一集的来源 / 分集计划 / 单元各步骤状态，及其在画布上对应的节点。"""
    return {"episodes": await _service(project_name).flow(project_name)}


@router.post(
    "/projects/{project_name}/canvas/episodes/{episode}/import", dependencies=[Depends(require_project_migration_ok)]
)
async def import_episode(project_name: str, episode: int):
    """把一集的流程（来源、分集计划、每个单元的流水线、起始帧联合图）摆上画布。"""
    try:
        group, revision = await _service(project_name).import_episode(project_name, episode)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"node": group, "revision": revision}


@router.put(
    "/projects/{project_name}/canvas/episodes/{episode}/units/{unit_id}/field",
    dependencies=[Depends(require_project_migration_ok)],
)
async def save_unit_field(project_name: str, episode: int, unit_id: str, req: UnitFieldRequest):
    """把单元文本 / 导演说明存回剧本（依赖它的 H3 提示词与产出会按规则过期）。"""
    try:
        await _service(project_name).save_unit_field(project_name, episode, unit_id, req.field, req.value)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"saved": True}


@router.get("/projects/{project_name}/canvas/markdown")
async def get_markdown(project_name: str, file: str):
    """文档节点的内容（来源文本 / 分集计划等）。"""
    try:
        return {"content": await _service(project_name).markdown(project_name, file)}
    except CanvasGraphError as exc:
        _raise(exc)


@router.post("/projects/{project_name}/canvas/members", dependencies=[Depends(require_project_migration_ok)])
async def update_members(project_name: str, req: MembersRequest):
    """把节点放进某个分组（含项目推导的分组），或从分组里放出来（group_id 为空）。"""
    try:
        revision = await _service(project_name).set_members(
            project_name,
            req.node_ids,
            req.group_id,
            {node_id: pos.model_dump() for node_id, pos in req.positions.items()},
        )
    except CanvasGraphError as exc:
        _raise(exc)
    return {"revision": revision}


@router.patch("/projects/{project_name}/canvas/nodes/{node_id}", dependencies=[Depends(require_project_migration_ok)])
async def update_node(project_name: str, node_id: str, req: PatchNodeRequest):
    position = req.position.model_dump() if req.position else None
    try:
        node, revision = await _service(project_name).patch_node(project_name, node_id, position, req.data)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"node": node, "revision": revision}


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(max_length=2000, description="参考说明；空串恢复默认（资产图）或清除（其余图片）")


@router.put(
    "/projects/{project_name}/canvas/nodes/{node_id}/note", dependencies=[Depends(require_project_migration_ok)]
)
async def update_note(project_name: str, node_id: str, req: NoteRequest):
    """给图片节点（含项目资产图）写参考说明：这张图被别的节点当参考图用时，说明连同图一起交给供应商。"""
    revision = await _service(project_name).set_note(project_name, node_id, req.note)
    return {"revision": revision}


@router.delete("/projects/{project_name}/canvas/nodes/{node_id}", dependencies=[Depends(require_project_migration_ok)])
async def delete_node(project_name: str, node_id: str):
    try:
        revision = await _service(project_name).delete_node(project_name, node_id)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"revision": revision}


@router.post("/projects/{project_name}/canvas/layout", dependencies=[Depends(require_project_migration_ok)])
async def update_layout(project_name: str, req: LayoutRequest):
    """批量更新节点位置（拖动结束 / 自动布局）。不存在的节点 id 忽略。"""
    revision = await _service(project_name).set_layout(
        project_name, {node_id: pos.model_dump() for node_id, pos in req.positions.items()}
    )
    return {"revision": revision}


@router.post("/projects/{project_name}/canvas/edges", dependencies=[Depends(require_project_migration_ok)])
async def create_edge(project_name: str, req: ConnectRequest):
    try:
        edge, revision = await _service(project_name).connect(
            project_name, req.source, req.source_handle, req.target, req.target_handle
        )
    except CanvasGraphError as exc:
        _raise(exc)
    return {"edge": edge, "revision": revision}


@router.delete("/projects/{project_name}/canvas/edges/{edge_id}", dependencies=[Depends(require_project_migration_ok)])
async def delete_edge(project_name: str, edge_id: str):
    try:
        revision = await _service(project_name).disconnect(project_name, edge_id)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"revision": revision}


@router.post("/projects/{project_name}/canvas/images", dependencies=[Depends(require_project_migration_ok)])
async def upload_canvas_image(
    project_name: str, x: float = 0, y: float = 0, node_id: str | None = None, file: UploadFile = File(...)
):
    """上传图片：默认成为画布上新的图片节点；带 ``node_id`` 时填进（或换掉）这个已有的图片节点。"""
    suffix = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if suffix not in _UPLOAD_EXTS:
        raise BadRequestError("canvas_upload_ext_unsupported", allowed=", ".join(_UPLOAD_EXTS))
    content = await file.read()
    if len(content) > _UPLOAD_MAX_BYTES:
        raise BadRequestError("upload_too_large", max_mb=_UPLOAD_MAX_BYTES // (1024 * 1024))

    try:
        node, revision = await _service(project_name).place_image(
            project_name, content, suffix, file.filename, {"x": x, "y": y}, node_id
        )
    except CanvasGraphError as exc:
        _raise(exc)
    return {"node": node, "revision": revision}


@router.post(
    "/projects/{project_name}/canvas/nodes/{node_id}/run", dependencies=[Depends(require_project_migration_ok)]
)
async def run_node(project_name: str, node_id: str, user: CurrentUser):
    """运行节点：图片供应商节点读入连进来的提示词与参考图并挂出输出节点。"""
    try:
        return await _service(project_name).run(project_name, node_id, user_id=user.id, source="webui")
    except CanvasGraphError as exc:
        _raise(exc)


@router.get("/projects/{project_name}/canvas/nodes/{node_id}/branch")
async def get_branch(project_name: str, node_id: str):
    """运行此节点前须先跑的上游节点（缺产物或已过期的），先上游后下游，最后一项是它自己。"""
    try:
        return {"nodes": await _service(project_name).branch(project_name, node_id)}
    except CanvasGraphError as exc:
        _raise(exc)


@router.post("/projects/{project_name}/canvas/templates", dependencies=[Depends(require_project_migration_ok)])
async def save_template(project_name: str, req: SaveTemplateRequest):
    """把选中的节点与它们之间的连线存成模板（产物图片与项目推导节点不入模板）。"""
    try:
        template, revision = await _service(project_name).save_template(project_name, req.name, req.node_ids)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"template": template, "revision": revision}


@router.post(
    "/projects/{project_name}/canvas/templates/{template_id}/use", dependencies=[Depends(require_project_migration_ok)]
)
async def use_template(project_name: str, template_id: str, req: UseTemplateRequest):
    try:
        nodes, revision = await _service(project_name).use_template(
            project_name, template_id, req.position.model_dump()
        )
    except CanvasGraphError as exc:
        _raise(exc)
    return {"nodes": nodes, "revision": revision}


@router.delete(
    "/projects/{project_name}/canvas/templates/{template_id}", dependencies=[Depends(require_project_migration_ok)]
)
async def delete_template(project_name: str, template_id: str):
    try:
        revision = await _service(project_name).delete_template(project_name, template_id)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"revision": revision}
