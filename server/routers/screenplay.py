"""编剧剧本路由：读取剧本与审阅状态、按场次修改、记录人工通过。

编剧步骤（Agent 单元）把本集源文改编为按场次组织的可读剧本；本路由是其后人工审阅的 REST 入口，
Agent 工具 ``get_screenplay`` / ``patch_screenplay`` / ``approve_screenplay`` 与它共用 ``ScreenplayReviewService``。
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import ConflictError, NotFoundError, UnprocessableError
from lib.project.project_manager import get_project_manager
from server.dependencies import require_project_migration_ok
from server.services.project.screenplay_review import ScreenplayReviewError, ScreenplayReviewService

router = APIRouter()

# 剧本领域错误码 → (HTTP 异常类型, i18n key)。episode_not_found 带集号另行处理。
_ERRORS: dict[str, tuple[type[ConflictError] | type[UnprocessableError], str]] = {
    "not_applicable": (ConflictError, "screenplay_not_applicable"),
    "missing": (ConflictError, "screenplay_missing"),
    "revision_conflict": (ConflictError, "screenplay_revision_conflict"),
    "corrupt": (ConflictError, "screenplay_corrupt"),
    "bad_edit": (UnprocessableError, "screenplay_bad_edit"),
}


def _raise(exc: ScreenplayReviewError, episode: int) -> None:
    if exc.code == "episode_not_found":
        raise NotFoundError("episode_not_found", episode=episode)
    error_type, key = _ERRORS.get(exc.code, (UnprocessableError, "screenplay_bad_edit"))
    error = error_type(key)
    if exc.code in {"bad_edit", "corrupt"} and exc.message:
        error.with_diagnostic(exc.message)
    raise error


class ScreenplayOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["update", "insert", "delete", "set_meta"]
    index: int | None = Field(default=None, ge=0, description="场次序号，从 0 起；set_meta 不需要")
    fields: dict[str, Any] | None = None
    scene: dict[str, Any] | None = None


class ScreenplayPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_fingerprint: str = Field(min_length=1, description="读取时得到的内容指纹；内容已变则 409")
    operations: list[ScreenplayOperation] = Field(min_length=1)


class ScreenplayApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(min_length=1, description="用户审阅过的那一版的内容指纹；内容已变则 409")


@router.get("/projects/{project_name}/episodes/{episode}/screenplay")
async def get_screenplay(project_name: str, episode: int):
    """该集剧本、逐场预计时长、总时长、可读 Markdown 与审阅状态；尚未编写时 ``status`` 为 ``none``。"""
    try:
        return await ScreenplayReviewService(get_project_manager()).get_state(project_name, episode)
    except ScreenplayReviewError as exc:
        _raise(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.patch(
    "/projects/{project_name}/episodes/{episode}/screenplay",
    dependencies=[Depends(require_project_migration_ok)],
)
async def patch_screenplay(project_name: str, episode: int, req: ScreenplayPatchRequest):
    """按场次修改剧本；成功修改使既有的审阅通过失效。"""
    try:
        return await ScreenplayReviewService(get_project_manager()).edit(
            project_name,
            episode,
            [operation.model_dump(exclude_none=True) for operation in req.operations],
            expected_fingerprint=req.base_fingerprint,
        )
    except ScreenplayReviewError as exc:
        _raise(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.post(
    "/projects/{project_name}/episodes/{episode}/screenplay/approve",
    dependencies=[Depends(require_project_migration_ok)],
)
async def approve_screenplay(project_name: str, episode: int, req: ScreenplayApproveRequest):
    """记录人工审阅通过，放行单元拆解。"""
    try:
        return await ScreenplayReviewService(get_project_manager()).approve(
            project_name, episode, expected_fingerprint=req.fingerprint
        )
    except ScreenplayReviewError as exc:
        _raise(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
