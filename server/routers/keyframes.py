"""起始帧路由：看板状态、生成联合图、人工审核格子、改挂单元（见 ``docs/adr/0096``）。

规划由 Agent 的 ``plan-keyframes`` 子智能体提交；这里是其后人工能做的事。生成走队列，立即返回
``task_id``，完成后界面按任务终态刷新看板。
"""

from typing import NoReturn

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from lib.infra.api_errors import ConflictError, NotFoundError, UnprocessableError
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.dependencies import require_project_migration_ok
from server.services.project.keyframe_board import KeyframeBoardService
from server.services.project.keyframe_plan import KeyframePlanError
from server.services.project.keyframe_sheets import enqueue_keyframe_sheet

router = APIRouter()

#: 领域错误码 → (HTTP 异常类型, 参数名)。i18n 键统一是 ``keyframe_<code>``；``id`` 参数是出问题的对象编号。
_ERRORS: dict[str, tuple[type[ConflictError] | type[NotFoundError] | type[UnprocessableError], str | None]] = {
    "not_applicable": (ConflictError, None),
    "episode_not_found": (NotFoundError, "episode"),
    "board_missing": (ConflictError, None),
    "sheet_missing": (NotFoundError, "id"),
    "cell_missing": (NotFoundError, "id"),
    "unit_missing": (NotFoundError, "id"),
    "cell_has_no_image": (ConflictError, "id"),
    "cell_stale": (ConflictError, "id"),
    "characters_not_in_unit": (UnprocessableError, "id"),
    "image_provider_unavailable": (ConflictError, None),
}


def _raise(exc: KeyframePlanError, *, episode: int, ident: str | None = None) -> NoReturn:
    error_type, param = _ERRORS.get(exc.code, (ConflictError, None))
    params: dict[str, object] = {}
    if param == "episode":
        params["episode"] = episode
    elif param == "id":
        params["id"] = ident or ""
    raise error_type(f"keyframe_{exc.code}", **params) from exc


class ReviewCellRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool


class AssignUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cell_id: str | None = None


@router.get("/projects/{project_name}/episodes/{episode}/keyframes")
async def get_keyframes(project_name: str, episode: int):
    """该集起始帧看板：联合图、格子（含是否过期）、单元与所挂格子的对应。项目不适用时 ``applicable`` 为 false。"""
    try:
        return await KeyframeBoardService(get_project_manager()).get_state(project_name, episode)
    except KeyframePlanError as exc:
        _raise(exc, episode=episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.get("/projects/{project_name}/episodes/{episode}/keyframes/sheets/{sheet_id}/request")
async def get_keyframe_sheet_request(project_name: str, episode: int, sheet_id: str):
    """这张联合图下次生成会发出的提示词与按序编号的参考图，供生成前核对。"""
    try:
        return await KeyframeBoardService(get_project_manager()).get_sheet_request(project_name, episode, sheet_id)
    except KeyframePlanError as exc:
        _raise(exc, episode=episode, ident=sheet_id)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.post(
    "/projects/{project_name}/episodes/{episode}/keyframes/sheets/{sheet_id}/generate",
    dependencies=[Depends(require_project_migration_ok)],
)
async def generate_keyframe_sheet(project_name: str, episode: int, sheet_id: str, user: CurrentUser):
    """把一张联合图的生成放进队列，立即返回 ``task_id``。"""
    try:
        return await enqueue_keyframe_sheet(
            get_project_manager(), project_name, episode, sheet_id, user_id=user.id, source="webui"
        )
    except KeyframePlanError as exc:
        _raise(exc, episode=episode, ident=sheet_id)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.post(
    "/projects/{project_name}/episodes/{episode}/keyframes/cells/{cell_id}/review",
    dependencies=[Depends(require_project_migration_ok)],
)
async def review_keyframe_cell(project_name: str, episode: int, cell_id: str, req: ReviewCellRequest):
    """审核一格：通过后才随视频请求发出；取消通过把它放回草稿。依据已变的格子不能通过。"""
    try:
        return await KeyframeBoardService(get_project_manager()).review_cell(
            project_name, episode, cell_id, approved=req.approved
        )
    except KeyframePlanError as exc:
        _raise(exc, episode=episode, ident=cell_id)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.post(
    "/projects/{project_name}/episodes/{episode}/keyframes/approve-all",
    dependencies=[Depends(require_project_migration_ok)],
)
async def approve_all_keyframe_cells(project_name: str, episode: int):
    """一次审核通过所有已有图且依据未变的格子；没有图或已过期的格子不动。"""
    try:
        return await KeyframeBoardService(get_project_manager()).approve_all(project_name, episode)
    except KeyframePlanError as exc:
        _raise(exc, episode=episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.put(
    "/projects/{project_name}/episodes/{episode}/keyframes/units/{unit_id}/cell",
    dependencies=[Depends(require_project_migration_ok)],
)
async def assign_keyframe_unit(project_name: str, episode: int, unit_id: str, req: AssignUnitRequest):
    """把单元改挂到另一格（``cell_id`` 为空表示不用起始帧）；单元原来的格子没有单元了就一并去掉。"""
    try:
        return await KeyframeBoardService(get_project_manager()).assign_unit(
            project_name, episode, unit_id, req.cell_id
        )
    except KeyframePlanError as exc:
        _raise(exc, episode=episode, ident=req.cell_id or unit_id)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
