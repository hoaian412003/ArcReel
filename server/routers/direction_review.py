"""导演审阅路由：读取导演结果与确定性报告，记录人工决定。

导演步骤（Agent 单元）把集级规划与逐条目导演说明写进正式脚本；本路由是其后人工单元的 REST 入口，
Agent 工具 ``get_direction_report`` / ``decide_direction_review`` 与它共用 ``DirectionReviewService``。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import NotFoundError
from lib.project.project_manager import get_project_manager
from server.dependencies import require_project_migration_ok
from server.routers._script_review_errors import direction_review_error
from server.services.project.direction_review import DirectionReviewError, DirectionReviewService

router = APIRouter()


class DirectionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "changes", "redo"] = Field(
        description="approve 通过；changes 要求修改指定条目 / 附说明；redo 整集重做"
    )
    note: str | None = Field(default=None, description="给导演的修改说明，approve 时忽略")
    entry_ids: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None, description="changes 时点名要改的条目 id；省略表示不限定条目"
    )


@router.get("/projects/{project_name}/episodes/{episode}/direction-review")
async def get_direction_review(project_name: str, episode: int):
    """该集导演审阅状态、导演数据指纹与确定性报告（无导演数据时 ``report`` 为 ``null``）。"""
    try:
        return await DirectionReviewService(get_project_manager()).get_state(project_name, episode)
    except DirectionReviewError as exc:
        direction_review_error(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.post(
    "/projects/{project_name}/episodes/{episode}/direction-review/decision",
    dependencies=[Depends(require_project_migration_ok)],
)
async def decide_direction_review(project_name: str, episode: int, req: DirectionDecisionRequest):
    """记录人工决定：通过，或要求修改 / 重做（记为 ``changes_requested``，由工作流下一步重新导演）。"""
    try:
        return await DirectionReviewService(get_project_manager()).decide(
            project_name, episode, req.decision, note=req.note, entry_ids=req.entry_ids
        )
    except DirectionReviewError as exc:
        direction_review_error(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
