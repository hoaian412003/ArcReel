"""Durable, resumable Claude Agent sessions with replayable event streams."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    UserMessage,
)
from claude_agent_sdk.types import ServerToolResultBlock, ToolPermissionContext, ToolResultBlock

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.db.repositories.agent_event_repo import AgentEventRepository
from lib.db.repositories.agent_turn_repo import AgentTurnRepository
from lib.db.repositories.session_repo import SessionRepository
from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_manager import ProjectManager
from server.claude_runtime.auth import ClaudeAuthManager
from server.claude_runtime.serialization import json_safe, sdk_message_payload
from server.claude_runtime.usage import cost_delta, usage_delta
from server.sse_channel import IDLE, DropSubscriber, SseChannel

logger = logging.getLogger(__name__)

ClientFactory = Callable[[ClaudeAgentOptions], Any]


class SessionBusyError(RuntimeError):
    pass


class PermissionRequestNotFoundError(LookupError):
    pass


class ClaudeSessionService:
    """Application-owned sessions around the official Claude Agent SDK."""

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        session_factory: Any = None,
        client_factory: ClientFactory | None = None,
        auth_manager: ClaudeAuthManager | None = None,
        permission_timeout_seconds: float = 600.0,
    ) -> None:
        layout = DataRootLayout.current() if data_root is None else DataRootLayout(Path(data_root))
        self._projects_dir = layout.projects_dir
        self._project_manager = ProjectManager(Path(data_root) if data_root is not None else layout.root)
        self._session_factory = session_factory or safe_session_factory
        self._client_factory = client_factory or (lambda options: ClaudeSDKClient(options=options))
        self._auth_manager = auth_manager
        self._permission_timeout_seconds = permission_timeout_seconds
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_clients: dict[str, Any] = {}
        self._channels: dict[str, SseChannel] = {}
        self._accept_locks: dict[str, asyncio.Lock] = {}
        self._permission_waiters: dict[str, tuple[str, asyncio.Future[bool]]] = {}
        self._unstarted_sessions: set[str] = set()

    async def startup(self) -> None:
        async with self._session_factory() as db:
            await SessionRepository(db).interrupt_running()

    async def shutdown(self) -> None:
        clients = list(self._active_clients.values())
        for client in clients:
            with contextlib.suppress(Exception):
                await client.interrupt()
        tasks = list(self._active_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for _, future in self._permission_waiters.values():
            if not future.done():
                future.cancel()
        self._permission_waiters.clear()

    async def list_sessions(self, project_name: str, *, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        async with self._session_factory() as db:
            rows = await SessionRepository(db).list(project_name=project_name, user_id=user_id)
        return [self._public_session(row) for row in rows]

    async def send_message(
        self,
        *,
        project_name: str,
        content: str,
        session_id: str | None,
        client_key: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        prompt = content.strip()
        if not prompt:
            raise ValueError("message content is empty")
        project_path = await asyncio.to_thread(self._project_manager.get_project_path, project_name)

        if session_id is None:
            new_lock = self._accept_locks.setdefault(f"new:{user_id}:{project_name}:{client_key}", asyncio.Lock())
            async with new_lock:
                async with self._session_factory() as db:
                    existing = await AgentTurnRepository(db).find_for_project_client_key(
                        project_name,
                        client_key,
                        user_id=user_id,
                    )
                if existing is not None:
                    turn, public_session = existing
                    return {"session": public_session, "turn": turn}
                sdk_session_id = str(uuid4())
                async with self._session_factory() as db:
                    session_row = await SessionRepository(db).create(
                        project_name,
                        sdk_session_id,
                        prompt[:120],
                        user_id,
                    )
                self._unstarted_sessions.add(session_row["id"])
                return await self.send_message(
                    project_name=project_name,
                    content=prompt,
                    session_id=session_row["id"],
                    client_key=client_key,
                    user_id=user_id,
                )

        resolved_session_id = session_id
        assert resolved_session_id is not None

        lock = self._accept_locks.setdefault(resolved_session_id, asyncio.Lock())
        async with lock:
            async with self._session_factory() as db:
                session_repo = SessionRepository(db)
                session_row = await session_repo.get_by_id(resolved_session_id, user_id=user_id)
                if session_row is None or session_row["project_name"] != project_name:
                    raise FileNotFoundError(session_id)

                turn_repo = AgentTurnRepository(db)
                existing = await turn_repo.find_by_client_key(resolved_session_id, client_key, user_id=user_id)
                if existing is not None:
                    return {"session": self._public_session(session_row), "turn": existing}

                active = self._active_tasks.get(resolved_session_id)
                if active is not None and not active.done():
                    raise SessionBusyError(session_id)

                turn = await turn_repo.create(session_id=resolved_session_id, client_key=client_key, user_id=user_id)
                event = await AgentEventRepository(db).append(
                    session_id=resolved_session_id,
                    entry_type="user_message",
                    payload={"turn_id": turn["id"], "content": prompt},
                    client_key=client_key,
                    user_id=user_id,
                )
                await db.commit()

            self._broadcast(event)
            task = asyncio.create_task(
                self._run_turn(
                    session=session_row,
                    turn=turn,
                    prompt=prompt,
                    project_path=project_path,
                    user_id=user_id,
                ),
                name=f"claude-turn-{turn['id']}",
            )
            self._active_tasks[resolved_session_id] = task
            task.add_done_callback(lambda finished, sid=resolved_session_id: self._turn_finished(sid, finished))
            return {"session": self._public_session(session_row), "turn": turn}

    async def interrupt(self, session_id: str, *, project_name: str, user_id: str = DEFAULT_USER_ID) -> bool:
        await self._require_session(session_id, project_name=project_name, user_id=user_id)
        client = self._active_clients.get(session_id)
        if client is None:
            return False
        await client.interrupt()
        return True

    async def resolve_permission(
        self,
        session_id: str,
        request_id: str,
        *,
        allow: bool,
        project_name: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        await self._require_session(session_id, project_name=project_name, user_id=user_id)
        pending = self._permission_waiters.get(request_id)
        if pending is None or pending[0] != session_id:
            raise PermissionRequestNotFoundError(request_id)
        future = pending[1]
        if not future.done():
            future.set_result(allow)

    async def events(
        self,
        session_id: str,
        *,
        project_name: str | None = None,
        after_seq: int = 0,
        user_id: str = DEFAULT_USER_ID,
    ) -> list[dict[str, Any]]:
        await self._require_session(session_id, project_name=project_name, user_id=user_id)
        async with self._session_factory() as db:
            return await AgentEventRepository(db).list_after(session_id, after_seq, user_id=user_id)

    async def turns(
        self,
        session_id: str,
        *,
        project_name: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        await self._require_session(session_id, project_name=project_name, user_id=user_id)
        async with self._session_factory() as db:
            turns = await AgentTurnRepository(db).list_for_session(session_id, user_id=user_id)
        token_fields = (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        totals: dict[str, int | float] = {field: sum(int(turn[field] or 0) for turn in turns) for field in token_fields}
        totals["total_cost_usd"] = sum(float(turn["total_cost_usd"] or 0.0) for turn in turns)
        return {"turns": turns, "totals": totals}

    @contextlib.asynccontextmanager
    async def stream_events(
        self,
        session_id: str,
        *,
        project_name: str,
        after_seq: int = 0,
        user_id: str = DEFAULT_USER_ID,
        idle_timeout: float = 15.0,
    ) -> AsyncGenerator[AsyncIterator[dict[str, Any] | object]]:
        await self._require_session(session_id, project_name=project_name, user_id=user_id)
        channel = self._channel(session_id)
        queue = channel.subscribe()
        history = await self.events(
            session_id,
            project_name=project_name,
            after_seq=after_seq,
            user_id=user_id,
        )

        async def _iterate() -> AsyncIterator[dict[str, Any] | object]:
            latest = after_seq
            for event in history:
                latest = max(latest, int(event["seq"]))
                yield event
            async for item in channel.iterate(queue, idle_timeout=idle_timeout):
                if item is IDLE:
                    yield IDLE
                    continue
                if int(item["seq"]) > latest:
                    latest = int(item["seq"])
                    yield item

        try:
            yield _iterate()
        finally:
            await channel.unsubscribe(queue)

    async def wait_for_idle(self, session_id: str) -> None:
        task = self._active_tasks.get(session_id)
        if task is not None:
            await asyncio.shield(task)

    async def _run_turn(
        self,
        *,
        session: dict[str, Any],
        turn: dict[str, Any],
        prompt: str,
        project_path: Path,
        user_id: str,
    ) -> None:
        session_id = session["id"]
        turn_id = turn["id"]
        result_failed = False
        try:
            async with self._session_factory() as db:
                turn_repo = AgentTurnRepository(db)
                previous = await turn_repo.latest_completed_before(session_id, turn_id, user_id=user_id)
                await turn_repo.mark_running(turn_id)
                await SessionRepository(db).update_status_by_id(session_id, "running", user_id=user_id)
                await db.commit()

            options = await self._build_options(
                session=session,
                project_path=project_path,
                user_id=user_id,
                resume=session_id not in self._unstarted_sessions,
            )
            client = self._client_factory(options)
            async with client:
                self._active_clients[session_id] = client
                await client.query(prompt, session_id=session["sdk_session_id"])
                self._unstarted_sessions.discard(session_id)
                async for message in client.receive_response():
                    if isinstance(message, UserMessage) and not self._has_tool_result(message):
                        continue
                    event_type = "message_delta" if isinstance(message, StreamEvent) else "sdk_message"
                    await self._append_event(
                        session_id,
                        event_type,
                        {"turn_id": turn_id, "message": sdk_message_payload(message)},
                        user_id=user_id,
                    )
                    if isinstance(message, ResultMessage):
                        result_failed = message.is_error
                        await self._complete_turn(
                            session_id=session_id,
                            turn_id=turn_id,
                            result=message,
                            previous=previous,
                            user_id=user_id,
                        )
            async with self._session_factory() as db:
                await SessionRepository(db).update_status_by_id(
                    session_id,
                    "error" if result_failed else "idle",
                    user_id=user_id,
                )
                await db.commit()
        except asyncio.CancelledError:
            await self._fail_turn(session_id, turn_id, "interrupted", user_id=user_id)
            raise
        except Exception as exc:
            logger.exception("Claude turn failed session=%s turn=%s", session_id, turn_id)
            await self._fail_turn(session_id, turn_id, type(exc).__name__, user_id=user_id)
        finally:
            self._active_clients.pop(session_id, None)

    @staticmethod
    def _has_tool_result(message: UserMessage) -> bool:
        """Keep SDK user messages that carry tool results, but not prompt echoes."""
        return isinstance(message.content, list) and any(
            isinstance(block, (ToolResultBlock, ServerToolResultBlock)) for block in message.content
        )

    async def _build_options(
        self,
        *,
        session: dict[str, Any],
        project_path: Path,
        user_id: str,
        resume: bool,
    ) -> ClaudeAgentOptions:
        env: dict[str, str] = {}
        model: str | None = None
        credential = None
        if self._auth_manager is None or not await self._auth_manager.is_logged_in():
            async with self._session_factory() as db:
                credential = await AgentCredentialRepository(db).get_active(user_id)
        if credential is not None:
            env["ANTHROPIC_API_KEY"] = credential.api_key
            if credential.base_url:
                env["ANTHROPIC_BASE_URL"] = credential.base_url
            model = credential.model or credential.sonnet_model

        async def can_use_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            return await self._request_permission(session["id"], tool_name, tool_input, context, user_id=user_id)

        sdk_id = session["sdk_session_id"]
        return ClaudeAgentOptions(
            cwd=project_path,
            session_id=None if resume else sdk_id,
            resume=sdk_id if resume else None,
            model=model,
            env=env,
            setting_sources=["project"],
            allowed_tools=["Read", "Glob", "Grep"],
            permission_mode="default",
            can_use_tool=can_use_tool,
            include_partial_messages=True,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": "Work only inside the current ArcReel project and explain consequential edits.",
            },
        )

    async def _request_permission(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
        *,
        user_id: str,
    ) -> PermissionResultAllow | PermissionResultDeny:
        request_id = context.tool_use_id or uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._permission_waiters[request_id] = (session_id, future)
        await self._append_event(
            session_id,
            "permission_request",
            {
                "request_id": request_id,
                "tool_name": tool_name,
                "tool_input": json_safe(tool_input),
                "title": context.title,
                "description": context.description,
            },
            user_id=user_id,
        )
        try:
            allow = await asyncio.wait_for(future, timeout=self._permission_timeout_seconds)
        except TimeoutError:
            allow = False
        finally:
            self._permission_waiters.pop(request_id, None)
        await self._append_event(
            session_id,
            "permission_result",
            {"request_id": request_id, "allowed": allow},
            user_id=user_id,
        )
        if allow:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Permission denied or expired")

    async def _complete_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        result: ResultMessage,
        previous: dict[str, Any] | None,
        user_id: str,
    ) -> None:
        snapshot = json_safe(result.usage) if result.usage is not None else {}
        previous_snapshot = (previous or {}).get("usage_snapshot") or {}
        delta = usage_delta(snapshot, previous_snapshot)
        current_cost = float(result.total_cost_usd) if result.total_cost_usd is not None else None
        prior_raw_cost = previous_snapshot.get("_total_cost_usd")
        prior_cost = float(prior_raw_cost) if isinstance(prior_raw_cost, (int, float)) else None
        snapshot["_total_cost_usd"] = current_cost
        async with self._session_factory() as db:
            await AgentTurnRepository(db).complete(
                turn_id,
                status="error" if result.is_error else "completed",
                usage=delta,
                usage_snapshot=snapshot,
                model_usage_snapshot=json_safe(result.model_usage),
                total_cost_usd=cost_delta(current_cost, prior_cost),
            )
            await db.commit()

    async def _fail_turn(self, session_id: str, turn_id: str, code: str, *, user_id: str) -> None:
        async with self._session_factory() as db:
            await AgentTurnRepository(db).fail(turn_id, error_code=code, error_message=code)
            await SessionRepository(db).update_status_by_id(session_id, "error", user_id=user_id)
            await db.commit()
        await self._append_event(
            session_id,
            "turn_error",
            {"turn_id": turn_id, "code": code},
            user_id=user_id,
        )

    async def _append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        user_id: str,
    ) -> dict[str, Any]:
        async with self._session_factory() as db:
            event = await AgentEventRepository(db).append(
                session_id=session_id,
                entry_type=event_type,
                payload=payload,
                user_id=user_id,
            )
            await db.commit()
        self._broadcast(event)
        return event

    async def _require_session(
        self,
        session_id: str,
        *,
        user_id: str,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_factory() as db:
            row = await SessionRepository(db).get_by_id(session_id, user_id=user_id)
        if row is None or (project_name is not None and row["project_name"] != project_name):
            raise FileNotFoundError(session_id)
        return row

    def _channel(self, session_id: str) -> SseChannel:
        channel = self._channels.get(session_id)
        if channel is None:
            channel = SseChannel(
                overflow=DropSubscriber(
                    on_removed=lambda count: logger.warning(
                        "Claude SSE subscribers dropped session=%s count=%s", session_id, count
                    )
                ),
                queue_maxsize=256,
            )
            self._channels[session_id] = channel
        return channel

    def _broadcast(self, event: dict[str, Any]) -> None:
        channel = self._channels.get(event["session_id"])
        if channel is not None:
            channel.broadcast(event)

    def _turn_finished(self, session_id: str, task: asyncio.Task[None]) -> None:
        if self._active_tasks.get(session_id) is task:
            self._active_tasks.pop(session_id, None)
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()

    @staticmethod
    def _public_session(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_name": row["project_name"],
            "title": row["title"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
