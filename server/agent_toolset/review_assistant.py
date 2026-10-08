"""One-call review summary tool used by the review-assistant skill."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.services.project.review_inbox import ReviewInboxService
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolProblem, ToolRequest


class CreateReviewSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode: int = Field(gt=0, description="剧集编号")
    review_kind: Literal["screenplay", "script_plan", "direction"] = Field(
        description="审阅类型：编剧剧本、脚本规划内容或导演报告"
    )


async def create_review_summary(
    request: ToolRequest[CreateReviewSummaryRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict]:
    value = request.value
    try:
        result = await ReviewInboxService(services.projects, services.workflow_planner).summarize(
            scope.project_name,
            value.episode,
            value.review_kind,
            user_id=caller.user_id,
        )
    except ValueError as exc:
        return ToolOutcome(problem=ToolProblem(str(exc), str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("review_summary_failed", str(exc)))
    return ToolOutcome(value=result)


CREATE_REVIEW_SUMMARY = ToolDeclaration(
    name="create_review_summary",
    description=(
        "为当前待人工审阅的内容调用一次 Review Model，返回准确的越南语摘要。"
        "只负责帮助用户阅读，不替用户通过、拒绝或修改内容；同一内容 fingerprint 会复用已有摘要。"
    ),
    request_model=CreateReviewSummaryRequest,
    migration=BLOCKED,
    domain_key="review_summary",
    handler=create_review_summary,
)

REVIEW_ASSISTANT_TOOLS = (CREATE_REVIEW_SUMMARY,)

__all__ = ["CREATE_REVIEW_SUMMARY", "REVIEW_ASSISTANT_TOOLS"]
