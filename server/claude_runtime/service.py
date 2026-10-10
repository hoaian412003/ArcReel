"""Durable, resumable Claude Agent sessions with replayable event streams."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
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
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    UserMessage,
)
from claude_agent_sdk.types import (
    TERMINAL_TASK_STATUSES,
    ServerToolResultBlock,
    ToolPermissionContext,
    ToolResultBlock,
)

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.db.repositories.agent_event_repo import AgentEventRepository
from lib.db.repositories.agent_turn_repo import AgentTurnRepository
from lib.db.repositories.session_repo import SessionRepository
from lib.infra.api_errors import ConflictError, NotFoundError
from lib.infra.data_root_layout import DataRootLayout
from lib.project.agent_snapshot import (
    SnapshotNotFoundError,
    diff_snapshot,
    discard_snapshot,
    list_snapshots,
    restore_snapshot,
    take_snapshot,
)
from lib.project.data_validator import DataValidator
from lib.project.project_manager import ProjectManager
from server.claude_runtime.actions import ClaudeAction, render_action
from server.claude_runtime.auth import ClaudeAuthManager
from server.claude_runtime.mcp import project_mcp
from server.claude_runtime.serialization import json_safe, sdk_message_payload
from server.claude_runtime.usage import cost_delta, usage_delta
from server.claude_runtime.workspace import AgentWorkspace
from server.sse_channel import IDLE, DropSubscriber, SseChannel

logger = logging.getLogger(__name__)

ClientFactory = Callable[[ClaudeAgentOptions], Any]


_CHANGE_LIST_LIMIT = 30


#: 主 Agent 答完后，最多再等后台子智能体这么久。
BACKGROUND_TASK_TIMEOUT_SECONDS = 1800


def _track_background_tasks(message: Any, pending: set[str]) -> None:
    """按任务消息维护「还没结束的后台任务」；终态可能只来自 TaskUpdated，也可能只来自 TaskNotification。"""
    if isinstance(message, TaskStartedMessage):
        pending.add(message.task_id)
    elif isinstance(message, TaskNotificationMessage | TaskUpdatedMessage) and message.status in TERMINAL_TASK_STATUSES:
        pending.discard(message.task_id)


class ClaudeUnavailableError(RuntimeError):
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
        self._layout = layout
        self._project_manager = ProjectManager(Path(data_root) if data_root is not None else layout.root)
        self._session_factory = session_factory or safe_session_factory
        self._client_factory = client_factory or (lambda options: ClaudeSDKClient(options=options))
        self._auth_manager = auth_manager
        self._permission_timeout_seconds = permission_timeout_seconds
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_clients: dict[str, Any] = {}
        self._channels: dict[str, SseChannel] = {}
        self._accept_locks: dict[str, asyncio.Lock] = {}
        self._event_locks: dict[str, asyncio.Lock] = {}
        self._permission_waiters: dict[str, tuple[str, asyncio.Future[bool]]] = {}
        self._unstarted_sessions: set[str] = set()
        self._project_tasks: dict[str, asyncio.Task[None]] = {}
        self._project_locks: dict[str, asyncio.Lock] = {}
        self._stopping = False
        self._container_runtime = Path("/.dockerenv").exists()
        self._maintenance_tasks: set[asyncio.Task[None]] = set()

    async def startup(self) -> None:
        async with self._session_factory() as db:
            await SessionRepository(db).interrupt_running()
            await AgentTurnRepository(db).recover_running()
            pending = await AgentTurnRepository(db).pending()
            await db.commit()
        for item in pending:
            self._start_project_worker(item["project_name"])

    async def shutdown(self) -> None:
        self._stopping = True
        clients = list(self._active_clients.values())
        for client in clients:
            with contextlib.suppress(Exception):
                await client.interrupt()
        tasks = list(self._project_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        maintenance = list(self._maintenance_tasks)
        for task in maintenance:
            task.cancel()
        if maintenance:
            await asyncio.gather(*maintenance, return_exceptions=True)
        for _, future in self._permission_waiters.values():
            if not future.done():
                future.cancel()
        self._permission_waiters.clear()

    async def list_sessions(self, project_name: str, *, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        async with self._session_factory() as db:
            rows = await SessionRepository(db).list(project_name=project_name, user_id=user_id)
        return [self._public_session(row) for row in rows]

    async def clear_sessions(self, project_name: str, *, user_id: str = DEFAULT_USER_ID) -> int:
        """Clear one project's session history without touching project files or memory."""
        await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        project_task = self._project_tasks.get(project_name)
        if project_task is not None and not project_task.done():
            raise ConflictError("claude_session_busy")

        async with self._session_factory() as db:
            session_repo = SessionRepository(db)
            rows = await session_repo.list_for_project(project_name, user_id=user_id)
            if any(row["status"] == "running" for row in rows):
                raise ConflictError("claude_session_busy")
            pending = await AgentTurnRepository(db).pending(project_name, user_id=user_id)
            if pending:
                raise ConflictError("claude_session_busy")
            deleted = await session_repo.delete_for_project(project_name, user_id=user_id)

        workspace = await self.workspace(project_name, user_id)
        await asyncio.to_thread(workspace.clear_main_session, user_id)
        for row in deleted:
            session_id = row["id"]
            self._active_tasks.pop(session_id, None)
            self._active_clients.pop(session_id, None)
            self._channels.pop(session_id, None)
            self._accept_locks.pop(session_id, None)
            self._event_locks.pop(session_id, None)
            self._unstarted_sessions.discard(session_id)
        return len(deleted)

    async def send_message(
        self,
        *,
        project_name: str,
        content: str,
        session_id: str | None,
        client_key: str,
        user_id: str = DEFAULT_USER_ID,
        action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt = content.strip()
        if not prompt:
            raise ValueError("message content is empty")
        if self._auth_manager is not None and not await self._auth_manager.is_logged_in():
            async with self._session_factory() as db:
                credential = await AgentCredentialRepository(db).get_active(user_id)
            if credential is None and not os.environ.get("ANTHROPIC_API_KEY"):
                raise ClaudeUnavailableError("Claude authentication is required")
        await asyncio.to_thread(self._project_manager.get_project_path, project_name)

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
                    action=action,
                )

        resolved_session_id = session_id
        assert resolved_session_id is not None

        lock = self._accept_locks.setdefault(resolved_session_id, asyncio.Lock())
        async with lock, self._event_locks.setdefault(resolved_session_id, asyncio.Lock()):
            async with self._session_factory() as db:
                session_repo = SessionRepository(db)
                session_row = await session_repo.get_by_id(resolved_session_id, user_id=user_id)
                if session_row is None or session_row["project_name"] != project_name:
                    raise FileNotFoundError(session_id)

                turn_repo = AgentTurnRepository(db)
                existing = await turn_repo.find_by_client_key(resolved_session_id, client_key, user_id=user_id)
                if existing is not None:
                    return {"session": self._public_session(session_row), "turn": existing}

                turn = await turn_repo.create(
                    session_id=resolved_session_id, client_key=client_key, user_id=user_id, prompt=prompt, action=action
                )
                event = await AgentEventRepository(db).append(
                    session_id=resolved_session_id,
                    entry_type="user_message",
                    payload={"turn_id": turn["id"], "content": prompt, "action": action},
                    client_key=client_key,
                    user_id=user_id,
                )
                queued = await AgentEventRepository(db).append(
                    session_id=resolved_session_id,
                    entry_type="turn_queued",
                    payload={"turn_id": turn["id"]},
                    user_id=user_id,
                )
                await db.commit()

            self._broadcast(event)
            self._broadcast(queued)
            self._start_project_worker(project_name)
            return {"session": self._public_session(session_row), "turn": turn}

    async def workspace(self, project_name: str, user_id: str) -> AgentWorkspace:
        async with self._project_locks.setdefault(f"workspace:{project_name}", asyncio.Lock()):
            try:
                project_path = await asyncio.to_thread(self._project_manager.get_project_path, project_name)
                return await asyncio.to_thread(AgentWorkspace, self._layout, project_path, project_name, user_id)
            except FileNotFoundError as exc:
                raise NotFoundError("project_not_found", name=project_name) from exc
            except ValueError as exc:
                raise ConflictError("claude_workspace_unavailable") from exc

    async def send_action(self, project_name: str, action: ClaudeAction, *, user_id: str) -> dict[str, Any]:
        rendered = render_action(action)
        async with self._project_locks.setdefault(f"action:{project_name}", asyncio.Lock()):
            workspace = await self.workspace(project_name, user_id)
            session_id = workspace.main_session(user_id)
            if session_id is None:
                sessions = await self.list_sessions(project_name, user_id=user_id)
                session_id = sessions[0]["id"] if sessions else None
            result = await self.send_message(
                project_name=project_name,
                content=rendered.content,
                session_id=session_id,
                client_key=action.client_key,
                user_id=user_id,
                action=rendered.provenance,
            )
            await asyncio.to_thread(workspace.set_main_session, result["session"]["id"], user_id)
            return result

    def _start_project_worker(self, project_name: str) -> None:
        key = project_name
        task = self._project_tasks.get(key)
        if not self._stopping and (task is None or task.done()):
            worker = asyncio.create_task(self._drain_project(project_name))
            self._project_tasks[key] = worker
            worker.add_done_callback(lambda finished: self._worker_done(project_name, finished))

    def _worker_done(self, project_name: str, task: asyncio.Task[None]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            if task.exception() is not None:
                logger.error("Project agent worker failed", exc_info=task.exception())
                return
        if not self._stopping:
            maintenance = asyncio.create_task(self._restart_pending(project_name))
            self._maintenance_tasks.add(maintenance)
            maintenance.add_done_callback(self._maintenance_tasks.discard)

    async def _restart_pending(self, project_name: str) -> None:
        async with self._session_factory() as db:
            pending = await AgentTurnRepository(db).pending(project_name)
        if pending:
            self._start_project_worker(project_name)

    async def _drain_project(self, project_name: str) -> None:
        while not self._stopping:
            async with self._session_factory() as db:
                pending = await AgentTurnRepository(db).pending(project_name)
            if not pending:
                return
            item = pending[0]
            user_id = item["user_id"]
            session = await self.require_session(item["session_id"], project_name=project_name, user_id=user_id)
            try:
                workspace = await self.workspace(project_name, user_id)
            except (NotFoundError, ConflictError):
                await self._fail_turn(session["id"], item["turn"]["id"], "workspace_unavailable", user_id=user_id)
                continue
            task = asyncio.current_task()
            assert task is not None
            self._active_tasks[session["id"]] = task
            try:
                await self._run_turn(
                    session=session,
                    turn=item["turn"],
                    prompt=item["prompt"],
                    project_path=workspace.cwd,
                    user_id=user_id,
                )
            finally:
                self._active_tasks.pop(session["id"], None)

    async def interrupt(self, session_id: str, *, project_name: str, user_id: str = DEFAULT_USER_ID) -> bool:
        await self.require_session(session_id, project_name=project_name, user_id=user_id)
        async with self._session_factory() as db:
            repo = AgentTurnRepository(db)
            queued = [
                turn
                for turn in await repo.list_for_session(session_id, user_id=user_id)
                if turn["status"] == "accepted"
            ]
            for turn in queued:
                await repo.fail(turn["id"], error_code="interrupted", error_message="interrupted")
            await db.commit()
        for turn in queued:
            await self._append_event(
                session_id, "turn_error", {"turn_id": turn["id"], "code": "interrupted"}, user_id=user_id
            )
        client = self._active_clients.get(session_id)
        if client is None:
            return bool(queued)
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
        await self.require_session(session_id, project_name=project_name, user_id=user_id)
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
        await self.require_session(session_id, project_name=project_name, user_id=user_id)
        async with self._session_factory() as db:
            events = []
            cursor = after_seq
            while True:
                page = await AgentEventRepository(db).list_after(session_id, cursor, user_id=user_id)
                events.extend(page)
                if len(page) < 1000:
                    return events
                cursor = page[-1]["seq"]

    async def tail_cursor(
        self,
        session_id: str,
        *,
        turns: int,
        project_name: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> int:
        """`after_seq` that makes a replay start at the opening of the last `turns` turns."""
        await self.require_session(session_id, project_name=project_name, user_id=user_id)
        async with self._session_factory() as db:
            start = await AgentEventRepository(db).turn_start_seq(session_id, turns=turns, user_id=user_id)
        return max(start - 1, 0) if start is not None else 0

    async def earlier_events(
        self,
        session_id: str,
        *,
        before_seq: int,
        turns: int,
        project_name: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        """Whole turns that precede `before_seq`, oldest first, plus whether more remain."""
        await self.require_session(session_id, project_name=project_name, user_id=user_id)
        async with self._session_factory() as db:
            repo = AgentEventRepository(db)
            start = await repo.turn_start_seq(session_id, turns=turns, before_seq=before_seq, user_id=user_id)
            first = start if start is not None else 1
            events = await repo.list_before(session_id, after_seq=first - 1, before_seq=before_seq, user_id=user_id)
        return {"events": events, "has_more": first > 1}

    async def turns(
        self,
        session_id: str,
        *,
        project_name: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        await self.require_session(session_id, project_name=project_name, user_id=user_id)
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
        await self.require_session(session_id, project_name=project_name, user_id=user_id)
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
        session = await self.require_session(session_id, user_id=DEFAULT_USER_ID)
        task = self._project_tasks.get(session["project_name"])
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
        result_received = False
        snapshot_id: str | None = None
        try:
            snapshot_id = await self._take_turn_snapshot(project_path, turn_id)
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
            await self._append_event(session_id, "turn_running", {"turn_id": turn_id}, user_id=user_id)
            client = self._client_factory(options)
            async with client:
                self._active_clients[session_id] = client
                await client.query(prompt, session_id=session["sdk_session_id"])
                self._unstarted_sessions.discard(session_id)
                pending_tasks: set[str] = set()
                final_result: ResultMessage | None = None
                async for message in client.receive_response():
                    if await self._record_message(session_id, turn_id, message, user_id=user_id):
                        _track_background_tasks(message, pending_tasks)
                    if isinstance(message, ResultMessage):
                        final_result = message
                if final_result is not None and pending_tasks:
                    # 主 Agent 先答完、后台子智能体还在跑：关掉客户端会把它们一起杀掉，这一轮留到它们结束
                    # 并等到随后那条汇报结果（上限防止永远挂着）。
                    final_result = await self._await_background_tasks(
                        client, session_id, turn_id, pending_tasks, final_result, user_id=user_id
                    )
                if final_result is not None:
                    result_received = True
                    result_failed = final_result.is_error
                    await self._complete_turn(
                        session_id=session_id,
                        turn_id=turn_id,
                        result=final_result,
                        previous=previous,
                        user_id=user_id,
                    )
            if not result_received:
                raise RuntimeError("Claude response ended without a result")
            async with self._session_factory() as db:
                await SessionRepository(db).update_status_by_id(
                    session_id,
                    "error" if result_failed else "idle",
                    user_id=user_id,
                )
                await db.commit()
            await self._append_event(session_id, "turn_finished", {"turn_id": turn_id}, user_id=user_id)
        except asyncio.CancelledError:
            await self._fail_turn(session_id, turn_id, "interrupted", user_id=user_id)
            raise
        except Exception as exc:
            logger.exception("Claude turn failed session=%s turn=%s", session_id, turn_id)
            await self._fail_turn(session_id, turn_id, type(exc).__name__, user_id=user_id)
        finally:
            self._active_clients.pop(session_id, None)
            if snapshot_id is not None:
                await asyncio.shield(
                    self._record_project_changes(session_id, turn_id, project_path, snapshot_id, user_id)
                )

    async def _record_message(self, session_id: str, turn_id: str, message: Any, *, user_id: str) -> bool:
        """把一条 SDK 消息记进事件日志；纯用户回显（不含工具结果）不记，返回是否记了。"""
        if isinstance(message, UserMessage) and not self._has_tool_result(message):
            return False
        event_type = "message_delta" if isinstance(message, StreamEvent) else "sdk_message"
        await self._append_event(
            session_id,
            event_type,
            {"turn_id": turn_id, "message": sdk_message_payload(message)},
            user_id=user_id,
        )
        return True

    async def _await_background_tasks(
        self,
        client: Any,
        session_id: str,
        turn_id: str,
        pending_tasks: set[str],
        result: ResultMessage,
        *,
        user_id: str,
    ) -> ResultMessage:
        try:
            async with asyncio.timeout(BACKGROUND_TASK_TIMEOUT_SECONDS):
                async for message in client.receive_messages():
                    if await self._record_message(session_id, turn_id, message, user_id=user_id):
                        _track_background_tasks(message, pending_tasks)
                    if isinstance(message, ResultMessage):
                        result = message
                        if not pending_tasks:
                            break
        except TimeoutError:
            logger.warning("后台子智能体超时未结束 session=%s turn=%s pending=%s", session_id, turn_id, pending_tasks)
        return result

    @staticmethod
    async def _take_turn_snapshot(project_path: Path, turn_id: str) -> str | None:
        try:
            return await asyncio.to_thread(take_snapshot, project_path, label=turn_id)
        except Exception:
            logger.exception("Agent turn snapshot failed turn=%s", turn_id)
            return None

    async def _record_project_changes(
        self, session_id: str, turn_id: str, project_path: Path, snapshot_id: str, user_id: str
    ) -> None:
        """本轮改动了项目文本文件时，发 project_changes 事件（含改动清单、数据校验结果与可回退的快照 id）；没改动就丢掉快照。"""
        try:
            changes = await asyncio.to_thread(diff_snapshot, project_path, snapshot_id)
            if not any(changes.values()):
                await asyncio.to_thread(discard_snapshot, project_path, snapshot_id)
                return
            validation = await asyncio.to_thread(DataValidator().validate_project_dir, project_path)
            await self._append_event(
                session_id,
                "project_changes",
                {
                    "turn_id": turn_id,
                    "snapshot_id": snapshot_id,
                    **{key: files[:_CHANGE_LIST_LIMIT] for key, files in changes.items()},
                    "total": sum(len(files) for files in changes.values()),
                    "valid": validation.valid,
                    "errors": len(validation.error_messages),
                    "warnings": len(validation.warning_messages),
                },
                user_id=user_id,
            )
        except Exception:
            logger.exception("Recording project changes failed turn=%s", turn_id)

    async def list_snapshots(self, project_name: str) -> list[dict[str, Any]]:
        project_path = await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        return await asyncio.to_thread(list_snapshots, project_path)

    async def restore_snapshot(self, project_name: str, snapshot_id: str) -> dict[str, list[str]]:
        """把项目文本文件回退到某一轮开始前；该项目还有轮次在排队或运行时拒绝，避免与 Agent 的写入交错。"""
        project_path = await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        worker = self._project_tasks.get(project_name)
        if worker is not None and not worker.done():
            raise ConflictError("claude_project_busy")

        def _restore() -> dict[str, list[str]]:
            with self._project_manager.file_lock(project_path / "project.json"):
                return restore_snapshot(project_path, snapshot_id)

        try:
            return await asyncio.to_thread(_restore)
        except SnapshotNotFoundError as exc:
            raise NotFoundError("claude_snapshot_not_found") from exc

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
        if self._container_runtime:
            env["IS_SANDBOX"] = "1"
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

        workspace = await self.workspace(session["project_name"], user_id)
        if resume:
            history = await self.events(session["id"], user_id=user_id)
            resume = any(event["type"] == "sdk_message" for event in history)
        sdk_id = session["sdk_session_id"]
        return ClaudeAgentOptions(
            cwd=project_path,
            session_id=None if resume else sdk_id,
            resume=sdk_id if resume else None,
            model=model,
            env=env,
            setting_sources=["user", "project", "local"],
            tools={"type": "preset", "preset": "claude_code"},
            permission_mode="bypassPermissions",
            mcp_servers={"arcreel": project_mcp(self._project_manager, session["project_name"], user_id)},
            settings=json.dumps({"autoMemoryEnabled": True, "autoMemoryDirectory": str(workspace.memory_dir)}),
            add_dirs=[workspace.memory_dir],
            include_partial_messages=True,
            include_hook_events=True,
            forward_subagent_text=True,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": f"You are the persistent agent for ArcReel project {session['project_name']}. "
                "Use project skills and the project-bound ArcReel MCP tools to manage its artifacts. "
                f"Keep cross-session notes in {workspace.memory_dir}. Explain consequential edits. "
                "You own this project's data: assets, scripts and the node canvas are yours to manage through the tools; "
                "every turn is snapshotted so the user can revert. Ask before any paid provider run (image/video/audio). "
                "Run subagents in the foreground and wait for their result before answering; do not use run_in_background.",
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
        async with self._event_locks.setdefault(session_id, asyncio.Lock()), self._session_factory() as db:
            event = await AgentEventRepository(db).append(
                session_id=session_id,
                entry_type=event_type,
                payload=payload,
                user_id=user_id,
            )
            await db.commit()
        self._broadcast(event)
        return event

    async def require_session(
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
