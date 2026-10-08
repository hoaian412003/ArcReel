"""Unified human-review inbox."""

from typing import Literal

from fastapi import APIRouter, HTTPException

from lib.infra.api_errors import NotFoundError
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.services.project.review_inbox import ReviewInboxService

router = APIRouter()


@router.get("/projects/{project_name}/review-tasks")
async def list_review_tasks(project_name: str):
    try:
        return {"tasks": await ReviewInboxService(get_project_manager()).list_tasks(project_name)}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.post("/projects/{project_name}/review-tasks/{kind}/{episode}/summary")
async def create_review_summary(
    project_name: str,
    kind: Literal["screenplay", "script_plan", "direction"],
    episode: int,
    user: CurrentUser,
):
    try:
        return await ReviewInboxService(get_project_manager()).summarize(project_name, episode, kind, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
