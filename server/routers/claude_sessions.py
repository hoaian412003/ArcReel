"""HTTP and SSE boundary for the official Claude Agent SDK runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from server.auth import CurrentUser
from server.claude_runtime.auth import (
    ClaudeAuthBusyError,
    ClaudeAuthLoginNotFoundError,
    ClaudeAuthManager,
)
from server.claude_runtime.service import (
    ClaudeSessionService,
    PermissionRequestNotFoundError,
    SessionBusyError,
)
from server.sse_channel import IDLE

router = APIRouter()


class SendClaudeMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    session_id: str | None = None
    client_key: str | None = Field(default=None, max_length=200)


class PermissionDecisionRequest(BaseModel):
    allow: bool


class CompleteClaudeLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)


def get_claude_service(request: Request) -> ClaudeSessionService:
    return request.app.state.claude_session_service


def get_claude_auth(request: Request) -> ClaudeAuthManager:
    return request.app.state.claude_auth_manager


@router.get("/claude/auth/status")
async def get_claude_auth_status(request: Request) -> dict:
    return await get_claude_auth(request).status()


@router.post("/claude/auth/login")
async def start_claude_login(request: Request) -> dict:
    try:
        return await get_claude_auth(request).start_login()
    except ClaudeAuthBusyError as exc:
        raise ConflictError("claude_login_unavailable") from exc


@router.post("/claude/auth/login/{login_id}/complete")
async def complete_claude_login(
    login_id: str,
    body: CompleteClaudeLoginRequest,
    request: Request,
) -> dict:
    try:
        return await get_claude_auth(request).complete_login(login_id, body.code)
    except ClaudeAuthLoginNotFoundError as exc:
        raise NotFoundError("claude_login_not_found") from exc
    except ClaudeAuthBusyError as exc:
        raise ConflictError("claude_login_failed") from exc


@router.post("/claude/auth/logout")
async def logout_claude(request: Request) -> dict:
    try:
        return await get_claude_auth(request).logout()
    except (OSError, TimeoutError) as exc:
        raise ConflictError("claude_logout_failed") from exc


@router.get("/projects/{project_name}/claude/sessions")
async def list_claude_sessions(
    project_name: str,
    request: Request,
    user: CurrentUser,
) -> dict:
    service = get_claude_service(request)
    try:
        sessions = await service.list_sessions(project_name, user_id=user.id)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return {"sessions": sessions}


@router.post("/projects/{project_name}/claude/messages", status_code=202)
async def send_claude_message(
    project_name: str,
    body: SendClaudeMessageRequest,
    request: Request,
    user: CurrentUser,
) -> dict:
    service = get_claude_service(request)
    try:
        return await service.send_message(
            project_name=project_name,
            content=body.content,
            session_id=body.session_id,
            client_key=body.client_key or uuid4().hex,
            user_id=user.id,
        )
    except ValueError as exc:
        raise BadRequestError("claude_message_empty") from exc
    except FileNotFoundError as exc:
        if body.session_id:
            raise NotFoundError("claude_session_not_found") from exc
        raise NotFoundError("project_not_found", name=project_name) from exc
    except SessionBusyError as exc:
        raise ConflictError("claude_session_busy") from exc


@router.get("/projects/{project_name}/claude/sessions/{session_id}/events")
async def list_claude_events(
    project_name: str,
    session_id: str,
    request: Request,
    user: CurrentUser,
    after_seq: int = Query(default=0, ge=0),
) -> dict:
    try:
        events = await get_claude_service(request).events(
            session_id,
            project_name=project_name,
            after_seq=after_seq,
            user_id=user.id,
        )
    except FileNotFoundError as exc:
        raise NotFoundError("claude_session_not_found") from exc
    return {"events": events}


@router.get("/projects/{project_name}/claude/sessions/{session_id}/turns")
async def list_claude_turns(
    project_name: str,
    session_id: str,
    request: Request,
    user: CurrentUser,
) -> dict:
    try:
        return await get_claude_service(request).turns(
            session_id,
            project_name=project_name,
            user_id=user.id,
        )
    except FileNotFoundError as exc:
        raise NotFoundError("claude_session_not_found") from exc


@router.get(
    "/projects/{project_name}/claude/sessions/{session_id}/events/stream",
    response_class=EventSourceResponse,
)
async def stream_claude_events(
    project_name: str,
    session_id: str,
    request: Request,
    user: CurrentUser,
    after_seq: int | None = Query(default=None, ge=0),
) -> AsyncIterator[ServerSentEvent]:
    service = get_claude_service(request)
    header_cursor = request.headers.get("last-event-id", "")
    try:
        resumed_after = max(after_seq or 0, int(header_cursor or 0))
    except ValueError:
        resumed_after = after_seq or 0
    try:
        async with service.stream_events(
            session_id,
            project_name=project_name,
            after_seq=resumed_after,
            user_id=user.id,
        ) as stream:
            async for item in stream:
                if await request.is_disconnected():
                    break
                if item is IDLE:
                    yield ServerSentEvent(comment="keepalive")
                    continue
                if not isinstance(item, dict):
                    continue
                yield ServerSentEvent(id=str(item["seq"]), event=item["type"], data=item)
    except FileNotFoundError as exc:
        raise NotFoundError("claude_session_not_found") from exc


@router.post("/projects/{project_name}/claude/sessions/{session_id}/interrupt")
async def interrupt_claude_session(
    project_name: str,
    session_id: str,
    request: Request,
    user: CurrentUser,
) -> dict:
    try:
        interrupted = await get_claude_service(request).interrupt(
            session_id,
            project_name=project_name,
            user_id=user.id,
        )
    except FileNotFoundError as exc:
        raise NotFoundError("claude_session_not_found") from exc
    return {"interrupted": interrupted}


@router.post("/projects/{project_name}/claude/sessions/{session_id}/permissions/{request_id}")
async def resolve_claude_permission(
    project_name: str,
    session_id: str,
    request_id: str,
    body: PermissionDecisionRequest,
    request: Request,
    user: CurrentUser,
) -> dict:
    try:
        await get_claude_service(request).resolve_permission(
            session_id,
            request_id,
            allow=body.allow,
            project_name=project_name,
            user_id=user.id,
        )
    except (FileNotFoundError, PermissionRequestNotFoundError) as exc:
        raise NotFoundError("claude_permission_not_found") from exc
    return {"resolved": True, "allowed": body.allow}
