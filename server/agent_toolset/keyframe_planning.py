"""起始帧规划工具的声明：读简报、提交格子清单。

规划由子智能体完成，服务端只读取、校验与落盘，不调文本模型（``docs/adr/0096``）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import ApiError
from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.services.project.keyframe_board import KeyframeBoardService
from server.services.project.keyframe_plan import KeyframePlanError
from server.services.project.keyframe_sheets import enqueue_keyframe_sheet
from server.tool_runtime import (
    CallerContext,
    KeyframeBriefRequest,
    ProjectScope,
    Services,
    SubmitKeyframePlanRequest,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    get_keyframe_brief,
    submit_keyframe_plan,
)


class KeyframeSheetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode: int = Field(ge=1, description="剧集编号")
    sheet_id: str = Field(min_length=1, description="联合图编号（get_keyframe_board 的 sheets[].id，如 S1）")


def _problem(exc: KeyframePlanError) -> ToolProblem:
    return ToolProblem(f"keyframe_{exc.code}", exc.message)


async def get_keyframe_board(
    request: ToolRequest[KeyframeBriefRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        state = await KeyframeBoardService(services.projects).get_state(scope.project_name, request.value.episode)
    except KeyframePlanError as exc:
        return ToolOutcome(problem=_problem(exc))
    return ToolOutcome(value=state)


async def generate_keyframe_sheet(
    request: ToolRequest[KeyframeSheetRequest], scope: ProjectScope, caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    try:
        queued = await enqueue_keyframe_sheet(
            services.projects,
            scope.project_name,
            value.episode,
            value.sheet_id,
            user_id=caller.user_id,
            source=caller.source,
        )
    except KeyframePlanError as exc:
        return ToolOutcome(problem=_problem(exc))
    except ApiError as exc:
        return ToolOutcome(problem=ToolProblem("keyframe_enqueue_failed", str(exc)))
    return ToolOutcome(value=queued)


GET_KEYFRAME_BRIEF = ToolDeclaration(
    name="get_keyframe_brief",
    description=(
        "读取起始帧规划简报：本集每个视频单元的正文与导演说明、已登记的角色（含 本体/衍生 形态）与场景、"
        "每张联合图的格子上限，以及已有看板的状态。plan_fingerprint 是这份内容的摘要，提交规划时带回它。"
        "只用于采用 H3 全参考提示词档的参考生视频项目。只读，无副作用。"
    ),
    request_model=KeyframeBriefRequest,
    migration=Exempt("只读：读取正式脚本与看板文件，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key="keyframe_brief",
    handler=get_keyframe_brief,
)

SUBMIT_KEYFRAME_PLAN = ToolDeclaration(
    name="submit_keyframe_plan",
    description=(
        "提交本集的起始帧格子清单。每一格是「某站位下某起始景别」的一帧画面，供一个或多个视频单元作首帧参考："
        "同一站位下起始景别相同的单元共用一格，站位变化（起身、换位）另起一格；景别须与使用它的单元第一镜一致。"
        "服务端逐条校验（场景与角色须已登记、一个单元至多一格、出镜角色须在使用该格的单元正文里被 @ 引用），"
        "任何违约整份拒绝并返回逐条问题；通过后整份替换看板，内容未变的格子沿用已有图与审核状态，"
        "并按场景把格子装进每张至多 4 格的联合图。这一步不生成图片。"
    ),
    request_model=SubmitKeyframePlanRequest,
    migration=BLOCKED,
    domain_key="keyframe_plan",
    handler=submit_keyframe_plan,
)

GET_KEYFRAME_BOARD = ToolDeclaration(
    name="get_keyframe_board",
    description=(
        "读取本集起始帧看板的当前状态：联合图（有没有出图、在不在队列里）、格子（是否有图、是否已审核、依据是否已过期）、"
        "每个单元挂在哪一格。生成联合图前先看哪些图缺、哪些过期；生成后用它确认出图与切分结果。只读，无副作用。"
    ),
    request_model=KeyframeBriefRequest,
    migration=Exempt("只读：读取看板文件，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key="keyframe_board",
    handler=get_keyframe_board,
)

GENERATE_KEYFRAME_SHEET = ToolDeclaration(
    name="generate_keyframe_sheet",
    description=(
        "把一张起始帧联合图的生成放进队列并立即返回任务 id，不等结果（付费生成，按一张图片计费；"
        "完成后服务端自动按格切分，格子随后由用户逐格审核，未审核的格子不会随视频请求发出）。"
        "调用前须先向用户说明要生成哪几张、费用与用途并得到同意；同一张图已在队列时返回 deduped。"
        "出图后用 get_keyframe_board 查看结果。不要替用户审核格子。"
    ),
    request_model=KeyframeSheetRequest,
    migration=BLOCKED,
    domain_key="keyframe_sheet",
    handler=generate_keyframe_sheet,
)

KEYFRAME_PLANNING_TOOLS = (GET_KEYFRAME_BRIEF, SUBMIT_KEYFRAME_PLAN, GET_KEYFRAME_BOARD, GENERATE_KEYFRAME_SHEET)

__all__ = [
    "GENERATE_KEYFRAME_SHEET",
    "GET_KEYFRAME_BOARD",
    "GET_KEYFRAME_BRIEF",
    "KEYFRAME_PLANNING_TOOLS",
    "SUBMIT_KEYFRAME_PLAN",
]
