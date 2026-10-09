"""HTTP and SSE boundary for the official Claude Agent SDK runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from server.auth import CurrentUser
from server.claude_runtime.actions import ClaudeAction
from server.claude_runtime.auth import (
    ClaudeAuthBusyError,
    ClaudeAuthLoginNotFoundError,
    ClaudeAuthManager,
)
from server.claude_runtime.service import (
    ClaudeSessionService,
    ClaudeUnavailableError,
    PermissionRequestNotFoundError,
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


class WriteClaudeMemoryRequest(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+\.md$")
    content: str = Field(max_length=200_000)


class PromoteClaudeMemoryRequest(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+\.md$")


def get_claude_service(request: Request) -> ClaudeSessionService:
    return request.app.state.claude_session_service


def get_claude_auth(request: Request) -> ClaudeAuthManager:
    return request.app.state.claude_auth_manager


@router.post("/projects/{project_name}/claude/actions", status_code=202)
async def send_claude_action(project_name: str, body: ClaudeAction, request: Request, user: CurrentUser) -> dict:
    try:
        return await get_claude_service(request).send_action(project_name, body, user_id=user.id)
    except ValueError as exc:
        raise BadRequestError("claude_action_invalid").with_diagnostic(str(exc)) from exc
    except ClaudeUnavailableError as exc:
        raise ConflictError("claude_auth_required") from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.get("/projects/{project_name}/claude/agent")
async def get_project_claude_agent(project_name: str, request: Request, user: CurrentUser) -> dict:
    service = get_claude_service(request)
    try:
        workspace = await service.workspace(project_name, user.id)
        sessions = await service.list_sessions(project_name, user_id=user.id)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    auth = await get_claude_auth(request).status()
    return {
        "agent_id": workspace.agent_id,
        "main_session_id": workspace.main_session(user.id),
        "workspace": str(workspace.cwd),
        "memory_path": str(workspace.memory_dir),
        "memory_enabled": True,
        "logged_in": auth["logged_in"],
        "mcp_available": True,
        "skills": [p.parent.name for p in (workspace.cwd / ".claude" / "skills").glob("*/SKILL.md")],
        "sessions": sessions,
    }


@router.get("/projects/{project_name}/claude/memory")
async def get_claude_memory(project_name: str, request: Request, user: CurrentUser) -> dict:
    try:
        workspace = await get_claude_service(request).workspace(project_name, user.id)
        return {"files": await asyncio.to_thread(workspace.memory)}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.put("/projects/{project_name}/claude/memory")
async def write_claude_memory(
    project_name: str, body: WriteClaudeMemoryRequest, request: Request, user: CurrentUser
) -> dict:
    workspace = await get_claude_service(request).workspace(project_name, user.id)
    try:
        await asyncio.to_thread(workspace.write_memory, body.name, body.content)
    except ValueError as exc:
        raise BadRequestError("claude_memory_invalid").with_diagnostic(str(exc)) from exc
    return {"saved": True}


@router.post("/projects/{project_name}/claude/memory/promote")
async def promote_claude_memory(
    project_name: str, body: PromoteClaudeMemoryRequest, request: Request, user: CurrentUser
) -> dict:
    workspace = await get_claude_service(request).workspace(project_name, user.id)
    try:
        await asyncio.to_thread(workspace.promote, body.name)
    except ValueError as exc:
        raise BadRequestError("claude_memory_invalid").with_diagnostic(str(exc)) from exc
    return {"promoted": True}


@router.post("/projects/{project_name}/claude/memory/reset")
async def reset_claude_memory(project_name: str, request: Request, user: CurrentUser) -> dict:
    service = get_claude_service(request)
    workspace = await service.workspace(project_name, user.id)
    sessions = await service.list_sessions(project_name, user_id=user.id)
    if any(s["status"] == "running" for s in sessions):
        raise ConflictError("claude_session_busy")
    await asyncio.to_thread(workspace.reset)
    return {"reset": True, "backup_retained": True}


@router.get("/projects/{project_name}/claude/export")
async def export_claude_agent(project_name: str, request: Request, user: CurrentUser) -> Response:
    service = get_claude_service(request)
    workspace = await service.workspace(project_name, user.id)
    history = {}
    sdk_session_ids = []
    for session in await service.list_sessions(project_name, user_id=user.id):
        row = await service.require_session(session["id"], project_name=project_name, user_id=user.id)
        sdk_session_ids.append(row["sdk_session_id"])
        history[session["id"]] = {
            "session": session,
            "events": await service.events(session["id"], user_id=user.id),
            "usage": await service.turns(session["id"], project_name=project_name, user_id=user.id),
        }
    archive = await asyncio.to_thread(workspace.export, history, sdk_session_ids)
    return Response(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="claude-{workspace.agent_id}.zip"'},
    )


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


@router.delete("/projects/{project_name}/claude/sessions")
async def clear_claude_sessions(
    project_name: str,
    request: Request,
    user: CurrentUser,
) -> dict:
    try:
        deleted = await get_claude_service(request).clear_sessions(project_name, user_id=user.id)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return {"deleted": deleted}


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
    except ClaudeUnavailableError as exc:
        raise ConflictError("claude_auth_required") from exc


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


@router.get("/projects/{project_name}/claude/sessions/{session_id}/events/earlier")
async def list_earlier_claude_events(
    project_name: str,
    session_id: str,
    request: Request,
    user: CurrentUser,
    before_seq: int = Query(ge=1),
    turns: int = Query(default=10, ge=1, le=50),
) -> dict:
    try:
        return await get_claude_service(request).earlier_events(
            session_id,
            before_seq=before_seq,
            turns=turns,
            project_name=project_name,
            user_id=user.id,
        )
    except FileNotFoundError as exc:
        raise NotFoundError("claude_session_not_found") from exc


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
    tail_turns: int | None = Query(default=None, ge=1, le=50),
) -> AsyncIterator[ServerSentEvent]:
    service = get_claude_service(request)
    header_cursor = request.headers.get("last-event-id", "")
    try:
        resumed_after = max(after_seq or 0, int(header_cursor or 0))
    except ValueError:
        resumed_after = after_seq or 0
    try:
        if tail_turns is not None and resumed_after == 0:
            resumed_after = await service.tail_cursor(
                session_id, turns=tail_turns, project_name=project_name, user_id=user.id
            )
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


@router.get("/projects/{project_name}/claude/snapshots")
async def list_claude_snapshots(project_name: str, request: Request, user: CurrentUser) -> dict:
    try:
        return {"snapshots": await get_claude_service(request).list_snapshots(project_name)}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.post("/projects/{project_name}/claude/snapshots/{snapshot_id}/restore")
async def restore_claude_snapshot(project_name: str, snapshot_id: str, request: Request, user: CurrentUser) -> dict:
    try:
        return {"restored": await get_claude_service(request).restore_snapshot(project_name, snapshot_id)}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
