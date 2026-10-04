"""Flow 看板路由：每一集落在哪一列、下一步动作是什么、是否被阻断。"""

from fastapi import APIRouter

from lib.infra.api_errors import NotFoundError
from lib.project.project_manager import get_project_manager
from lib.workflow.flow_board import build_flow_board
from server.services.project.workflow_planner import get_workflow_planner

router = APIRouter()


@router.get("/projects/{project_name}/flow-board")
async def get_flow_board(project_name: str):
    """看板数据：固定的列（Agent / 人工单元交替）与每集一张卡片。

    项目被阻断（升级失败、脚本损坏等）时照常返回，卡片带 ``blocked: true``，不因阻断而 4xx。
    """
    try:
        statuses = await get_workflow_planner(get_project_manager()).get_episode_statuses(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return build_flow_board(statuses)
