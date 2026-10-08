"""画布路由：节点画布的读取、节点与连线的增删改、节点运行、图片上传。

业务规则在 ``CanvasService``（Agent 工具共用）；这里只做参数校验、错误码映射与上传落盘。
写操作都是细粒度的（不整图覆盖），因为服务端的 worker 会并发回写节点状态。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from lib.canvas.graph import NODE_IMAGE, CanvasGraphError, add_node, make_node
from lib.canvas.store import CANVAS_UPLOAD_DIR
from lib.generation.generation_queue import get_generation_queue
from lib.infra.api_errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
)
from lib.infra.path_safety import safe_join
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.dependencies import require_project_migration_ok
from server.services.project.canvas_service import CanvasService

router = APIRouter()

_UPLOAD_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_UPLOAD_MAX_BYTES = 20 * 1024 * 1024

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
    type: Literal["text", "image", "image_generate", "h3_writer"]
    position: Position
    data: dict[str, Any] = Field(default_factory=dict)


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


class ViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episodes: list[int]


@router.get("/projects/{project_name}/canvas")
async def get_canvas(project_name: str):
    return await _service(project_name).view(project_name)


@router.put("/projects/{project_name}/canvas/view", dependencies=[Depends(require_project_migration_ok)])
async def update_view(project_name: str, req: ViewRequest):
    """选择画布上展示哪几集的项目节点；只影响展示，不动项目数据。"""
    episodes, revision = await _service(project_name).set_view(project_name, req.episodes)
    return {"revision": revision, "episodes": episodes}


@router.post("/projects/{project_name}/canvas/nodes", dependencies=[Depends(require_project_migration_ok)])
async def create_node(project_name: str, req: CreateNodeRequest):
    try:
        node, revision = await _service(project_name).add_node(
            project_name, req.type, req.position.model_dump(), req.data
        )
    except CanvasGraphError as exc:
        _raise(exc)
    return {"node": node, "revision": revision}


@router.patch("/projects/{project_name}/canvas/nodes/{node_id}", dependencies=[Depends(require_project_migration_ok)])
async def update_node(project_name: str, node_id: str, req: PatchNodeRequest):
    position = req.position.model_dump() if req.position else None
    try:
        node, revision = await _service(project_name).patch_node(project_name, node_id, position, req.data)
    except CanvasGraphError as exc:
        _raise(exc)
    return {"node": node, "revision": revision}


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
async def upload_canvas_image(project_name: str, x: float = 0, y: float = 0, file: UploadFile = File(...)):
    """上传图片，直接成为画布上的一个图片节点。"""
    suffix = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if suffix not in _UPLOAD_EXTS:
        raise BadRequestError("canvas_upload_ext_unsupported", allowed=", ".join(_UPLOAD_EXTS))
    content = await file.read()
    if len(content) > _UPLOAD_MAX_BYTES:
        raise BadRequestError("upload_too_large", max_mb=_UPLOAD_MAX_BYTES // (1024 * 1024))

    service = _service(project_name)
    project_path: Path = await asyncio.to_thread(service.pm.get_project_path, project_name)
    rel = f"{CANVAS_UPLOAD_DIR}/{uuid.uuid4().hex}{suffix}"
    target = safe_join(project_path, rel)

    def _save() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    await asyncio.to_thread(_save)
    node, graph = await service.mutate(
        project_name,
        lambda g: add_node(
            g, make_node(NODE_IMAGE, {"x": x, "y": y}, {"label": file.filename}, server_data={"path": rel})
        ),
    )
    return {"node": node, "revision": graph["revision"]}


@router.post(
    "/projects/{project_name}/canvas/nodes/{node_id}/run", dependencies=[Depends(require_project_migration_ok)]
)
async def run_node(project_name: str, node_id: str, user: CurrentUser):
    """运行节点：图片供应商节点读入连进来的提示词与参考图并挂出输出节点；H3 写作与联合图节点复用既有生成链路。"""
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
