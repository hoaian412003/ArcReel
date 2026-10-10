from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TaskNotificationMessage,
    TaskStartedMessage,
    TextBlock,
    UserMessage,
)
from claude_agent_sdk.types import ToolResultBlock, ToolUseBlock
from sqlalchemy import select

from lib.db.models.agent_turn import AgentTurn
from server.claude_runtime.actions import ClaudeAction
from server.claude_runtime.service import ClaudeSessionService


class FakeClaudeClient:
    def __init__(self, options: Any, *, call_number: int) -> None:
        self.options = options
        self.call_number = call_number
        self.interrupted = False

    async def __aenter__(self) -> FakeClaudeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.prompt = prompt
        self.session_id = session_id

    async def receive_response(self) -> AsyncIterator[Any]:
        yield StreamEvent(
            uuid=f"delta-{self.call_number}",
            session_id=self.session_id,
            event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
        )
        yield AssistantMessage(
            content=[TextBlock(text="ok")],
            model="claude-test",
            session_id=self.session_id,
        )
        yield AssistantMessage(
            content=[ToolUseBlock(id=f"tool-{self.call_number}", name="Read", input={"file_path": "README.md"})],
            model="claude-test",
            session_id=self.session_id,
        )
        yield UserMessage(
            content=[ToolResultBlock(tool_use_id=f"tool-{self.call_number}", content="contents", is_error=False)],
        )
        cumulative = (
            {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 10}
            if self.call_number == 1
            else {"input_tokens": 140, "output_tokens": 30, "cache_creation_input_tokens": 15}
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id=self.session_id,
            stop_reason="end_turn",
            total_cost_usd=0.20 if self.call_number == 1 else 0.32,
            usage=cumulative,
            result="ok",
        )

    async def interrupt(self) -> None:
        self.interrupted = True


async def test_service_persists_replayable_events_and_turn_usage_delta(
    tmp_path,
    db_factory,
    db_session,
) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text('{"name":"demo"}', encoding="utf-8")

    options_seen: list[Any] = []

    def client_factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        return FakeClaudeClient(options, call_number=len(options_seen))

    service = ClaudeSessionService(
        data_root=tmp_path,
        session_factory=db_factory,
        client_factory=client_factory,
    )

    first = await service.send_message(
        project_name="demo",
        content="First turn",
        session_id=None,
        client_key="request-1",
    )
    session_id = first["session"]["id"]
    assert "sdk_session_id" not in first["session"]
    await service.wait_for_idle(session_id)

    new_session_retry = await service.send_message(
        project_name="demo",
        content="First turn",
        session_id=None,
        client_key="request-1",
    )
    assert new_session_retry["session"]["id"] == session_id
    assert new_session_retry["turn"]["id"] == first["turn"]["id"]
    assert len(options_seen) == 1

    duplicate = await service.send_message(
        project_name="demo",
        content="First turn",
        session_id=session_id,
        client_key="request-1",
    )
    assert duplicate["turn"]["id"] == first["turn"]["id"]
    assert len(options_seen) == 1

    second = await service.send_message(
        project_name="demo",
        content="Second turn",
        session_id=session_id,
        client_key="request-2",
    )
    await service.wait_for_idle(session_id)
    listed_sessions = await service.list_sessions("demo")
    assert listed_sessions[0]["status"] == "idle"

    events = await service.events(session_id, project_name="demo")
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert {event["type"] for event in events} >= {"user_message", "message_delta", "sdk_message"}
    sdk_messages = [event["payload"]["message"] for event in events if event["type"] == "sdk_message"]
    assert any(message["message_type"] == "UserMessage" for message in sdk_messages)
    assert any(
        block.get("tool_use_id") == "tool-1"
        for message in sdk_messages
        for block in message.get("content", [])
        if isinstance(block, dict)
    )
    assert options_seen[0].resume is None
    assert options_seen[1].resume == options_seen[0].session_id
    assert options_seen[0].tools == {"type": "preset", "preset": "claude_code"}
    assert options_seen[0].permission_mode == "bypassPermissions"
    assert "arcreel" in options_seen[0].mcp_servers
    assert json.loads(options_seen[0].settings)["autoMemoryEnabled"] is True

    rows = (
        (
            await db_session.execute(
                select(AgentTurn).where(AgentTurn.session_id == session_id).order_by(AgentTurn.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert rows[0].input_tokens == 100
    assert rows[1].input_tokens == 40
    assert rows[1].output_tokens == 10
    assert rows[1].cache_creation_input_tokens == 5
    assert rows[1].total_cost_usd == Decimal("0.12000000")
    assert second["turn"]["id"] == rows[1].id

    usage = await service.turns(session_id, project_name="demo")
    assert usage["totals"] == {
        "input_tokens": 140,
        "output_tokens": 30,
        "cache_creation_input_tokens": 15,
        "cache_read_input_tokens": 0,
        "total_cost_usd": 0.32,
    }

    await service.shutdown()


async def test_actions_reuse_project_main_session_and_retry_without_duplicate_turn(tmp_path, db_factory) -> None:
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{"name":"demo"}', encoding="utf-8")
    clients = []

    def factory(options):
        client = FakeClaudeClient(options, call_number=len(clients) + 1)
        clients.append(client)
        return client

    service = ClaudeSessionService(data_root=tmp_path, session_factory=db_factory, client_factory=factory)
    action = ClaudeAction(action="create_screenplay", episode=4, client_key="create-ep04")
    first = await service.send_action("demo", action, user_id="default")
    await service.wait_for_idle(first["session"]["id"])
    retry = await service.send_action("demo", action, user_id="default")
    assert retry["turn"]["id"] == first["turn"]["id"]
    assert len(clients) == 1
    assert clients[0].prompt.startswith("Create the screenplay for episode EP04.")
    events = await service.events(first["session"]["id"], user_id="default")
    assert events[0]["payload"]["action"]["prompt_version"] == 1
    await service.shutdown()

    restored = ClaudeSessionService(data_root=tmp_path, session_factory=db_factory, client_factory=factory)
    second = await restored.send_action(
        "demo", ClaudeAction(action="create_screenplay", episode=5, client_key="ep05"), user_id="default"
    )
    await restored.wait_for_idle(second["session"]["id"])
    assert second["session"]["id"] == first["session"]["id"]
    assert clients[1].options.resume == clients[0].options.session_id
    assert clients[1].options.cwd == clients[0].options.cwd
    await restored.shutdown()


async def test_clear_sessions_only_removes_requested_project_history_and_keeps_memory(tmp_path, db_factory) -> None:
    for project_name in ("demo", "other"):
        project = tmp_path / "projects" / project_name
        project.mkdir(parents=True)
        (project / "project.json").write_text(f'{{"name":"{project_name}"}}', encoding="utf-8")

    service = ClaudeSessionService(
        data_root=tmp_path,
        session_factory=db_factory,
        client_factory=lambda options: FakeClaudeClient(options, call_number=1),
    )
    demo = await service.send_action(
        "demo", ClaudeAction(action="create_screenplay", episode=1, client_key="demo-1"), user_id="default"
    )
    await service.wait_for_idle(demo["session"]["id"])
    other = await service.send_message(project_name="other", content="Keep me", session_id=None, client_key="other-1")
    await service.wait_for_idle(other["session"]["id"])
    workspace = await service.workspace("demo", "default")
    workspace.write_memory("MEMORY.md", "Keep project memory")

    assert await service.clear_sessions("demo", user_id="default") == 1
    assert await service.list_sessions("demo", user_id="default") == []
    assert [session["id"] for session in await service.list_sessions("other", user_id="default")] == [
        other["session"]["id"]
    ]
    restored_workspace = await service.workspace("demo", "default")
    assert restored_workspace.main_session("default") is None
    assert workspace.memory() == [{"name": "MEMORY.md", "content": "Keep project memory"}]
    with pytest.raises(FileNotFoundError):
        await service.events(demo["session"]["id"], project_name="demo", user_id="default")
    await service.shutdown()


async def test_project_queue_serializes_different_sessions(tmp_path, db_factory) -> None:
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{"name":"demo"}', encoding="utf-8")
    started = asyncio.Event()
    release = asyncio.Event()
    prompts = []

    class BlockedClient(FakeClaudeClient):
        async def receive_response(self):
            prompts.append(self.prompt)
            if len(prompts) == 1:
                started.set()
                await release.wait()
            async for message in super().receive_response():
                yield message

    service = ClaudeSessionService(
        data_root=tmp_path,
        session_factory=db_factory,
        client_factory=lambda options: BlockedClient(options, call_number=1),
    )
    first = await service.send_message(project_name="demo", content="First", session_id=None, client_key="first")
    await asyncio.wait_for(started.wait(), timeout=3)
    second = await service.send_message(project_name="demo", content="Second", session_id=None, client_key="second")
    assert first["session"]["id"] != second["session"]["id"]
    assert prompts == ["First"]
    second_usage = await service.turns(second["session"]["id"], project_name="demo")
    assert second_usage["turns"][0]["status"] == "accepted"
    release.set()
    await service.wait_for_idle(first["session"]["id"])
    assert prompts == ["First", "Second"]
    assert (await service.turns(second["session"]["id"], project_name="demo"))["turns"][0]["status"] == "completed"
    await service.shutdown()


async def test_accepted_queue_recovers_after_service_restart(tmp_path, db_factory) -> None:
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{"name":"demo"}', encoding="utf-8")
    stopped = ClaudeSessionService(data_root=tmp_path, session_factory=db_factory)
    await stopped.shutdown()
    queued = await stopped.send_message(
        project_name="demo", content="Recover me", session_id=None, client_key="durable"
    )
    restored = ClaudeSessionService(
        data_root=tmp_path,
        session_factory=db_factory,
        client_factory=lambda options: FakeClaudeClient(options, call_number=1),
    )
    await restored.startup()
    await restored.wait_for_idle(queued["session"]["id"])
    turns = await restored.turns(queued["session"]["id"], project_name="demo")
    assert turns["turns"][0]["status"] == "completed"
    events = await restored.events(queued["session"]["id"], user_id="default")
    assert [event["payload"]["content"] for event in events if event["type"] == "user_message"] == ["Recover me"]
    await restored.shutdown()


class EditingClaudeClient(FakeClaudeClient):
    """模拟 Agent 在本轮里改了 project.json 并新增一个文件。"""

    project_dir: Any = None

    async def receive_response(self):
        (self.project_dir / "project.json").write_text('{"name":"demo","characters":{}}', encoding="utf-8")
        (self.project_dir / "notes.md").write_text("n", encoding="utf-8")
        async for message in super().receive_response():
            yield message


async def test_turn_that_edits_project_files_records_changes_and_can_be_reverted(tmp_path, db_factory) -> None:
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    original = '{"name":"demo","characters":{"A":{}}}'
    (project / "project.json").write_text(original, encoding="utf-8")
    EditingClaudeClient.project_dir = project

    service = ClaudeSessionService(
        data_root=tmp_path,
        session_factory=db_factory,
        client_factory=lambda options: EditingClaudeClient(options, call_number=1),
    )
    accepted = await service.send_action(
        "demo", ClaudeAction(action="create_screenplay", episode=1, client_key="k1"), user_id="default"
    )
    await service.wait_for_idle(accepted["session"]["id"])

    events = await service.events(accepted["session"]["id"], user_id="default")
    change = next(event["payload"] for event in events if event["type"] == "project_changes")
    assert change["modified"] == ["project.json"]
    assert change["added"] == ["notes.md"]
    assert change["total"] == 2

    await service.restore_snapshot("demo", change["snapshot_id"])

    assert (project / "project.json").read_text(encoding="utf-8") == original
    assert not (project / "notes.md").exists()
    await service.shutdown()


async def test_turn_without_file_changes_leaves_no_snapshot(tmp_path, db_factory) -> None:
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{"name":"demo"}', encoding="utf-8")
    service = ClaudeSessionService(
        data_root=tmp_path,
        session_factory=db_factory,
        client_factory=lambda options: FakeClaudeClient(options, call_number=1),
    )
    accepted = await service.send_action(
        "demo", ClaudeAction(action="create_screenplay", episode=1, client_key="k1"), user_id="default"
    )
    await service.wait_for_idle(accepted["session"]["id"])

    events = await service.events(accepted["session"]["id"], user_id="default")
    assert not any(event["type"] == "project_changes" for event in events)
    assert await service.list_snapshots("demo") == []
    await service.shutdown()


async def test_tail_cursor_and_earlier_events_page_whole_turns(tmp_path, db_factory, db_session) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text('{"name":"demo"}', encoding="utf-8")
    calls: list[Any] = []

    def client_factory(options: Any) -> FakeClaudeClient:
        calls.append(options)
        return FakeClaudeClient(options, call_number=len(calls))

    service = ClaudeSessionService(data_root=tmp_path, session_factory=db_factory, client_factory=client_factory)
    first = await service.send_message(project_name="demo", content="t1", session_id=None, client_key="k1")
    session_id = first["session"]["id"]
    await service.wait_for_idle(session_id)
    for index in range(2, 6):
        await service.send_message(
            project_name="demo", content=f"t{index}", session_id=session_id, client_key=f"k{index}"
        )
        await service.wait_for_idle(session_id)

    everything = await service.events(session_id, project_name="demo")
    starts = [event["seq"] for event in everything if event["type"] == "user_message"]
    assert len(starts) == 5

    cursor = await service.tail_cursor(session_id, turns=2, project_name="demo")
    tail = await service.events(session_id, project_name="demo", after_seq=cursor)
    assert tail[0]["seq"] == starts[-2]
    assert tail[0]["type"] == "user_message"
    assert await service.tail_cursor(session_id, turns=10, project_name="demo") == 0

    page = await service.earlier_events(session_id, before_seq=tail[0]["seq"], turns=2, project_name="demo")
    assert page["events"][0]["seq"] == starts[1]
    assert page["events"][-1]["seq"] == tail[0]["seq"] - 1
    assert page["has_more"] is True

    last = await service.earlier_events(session_id, before_seq=starts[1], turns=2, project_name="demo")
    assert last["events"][0]["seq"] == 1
    assert last["has_more"] is False


class BackgroundTaskClient(FakeClaudeClient):
    """主 Agent 先答完，后台子智能体随后才结束并带来第二条汇报。"""

    closed_early = False
    consumed_follow_up = False

    async def __aexit__(self, *_args: object) -> None:
        # 关客户端会杀掉后台任务：此时若汇报还没读完，就是过早关闭。
        type(self).closed_early = not type(self).consumed_follow_up

    async def receive_response(self) -> AsyncIterator[Any]:
        yield TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="t1",
            description="Episode 2 script plan",
            uuid="u1",
            session_id=self.session_id,
            tool_use_id="tool-1",
            task_type="local_agent",
        )
        yield self._result("started")

    async def receive_messages(self) -> AsyncIterator[Any]:
        yield TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id="t1",
            status="completed",
            output_file="",
            summary="done",
            uuid="u2",
            session_id=self.session_id,
        )
        type(self).consumed_follow_up = True
        yield self._result("report")

    def _result(self, text: str) -> ResultMessage:
        return ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id=self.session_id,
            stop_reason="end_turn",
            total_cost_usd=0.1,
            usage={"input_tokens": 1, "output_tokens": 1},
            result=text,
        )


async def test_turn_stays_open_until_background_subagents_report_back(tmp_path, db_factory) -> None:
    BackgroundTaskClient.closed_early = False
    BackgroundTaskClient.consumed_follow_up = False
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{"name":"demo"}', encoding="utf-8")
    service = ClaudeSessionService(
        data_root=tmp_path,
        session_factory=db_factory,
        client_factory=lambda options: BackgroundTaskClient(options, call_number=1),
    )
    accepted = await service.send_action(
        "demo", ClaudeAction(action="create_screenplay", episode=1, client_key="bg"), user_id="default"
    )
    await service.wait_for_idle(accepted["session"]["id"])

    assert BackgroundTaskClient.consumed_follow_up
    assert not BackgroundTaskClient.closed_early
    events = await service.events(accepted["session"]["id"], user_id="default")
    assert any(event["type"] == "turn_finished" for event in events)
    await service.shutdown()


class StaleNotificationClient(BackgroundTaskClient):
    """续接旧会话：先冒出上一轮遗留后台任务的通知和一条空结果，真正的回答随后才来。"""

    async def receive_response(self) -> AsyncIterator[Any]:
        yield TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id="old",
            status="stopped",
            output_file="",
            summary="stopped",
            uuid="u0",
            session_id=self.session_id,
        )
        yield self._result("empty")

    async def receive_messages(self) -> AsyncIterator[Any]:
        yield AssistantMessage(content=[TextBlock(text="real answer")], model="claude-test", session_id=self.session_id)
        type(self).consumed_follow_up = True
        yield self._result("real")


async def test_empty_result_from_a_leftover_task_notification_does_not_end_the_turn(tmp_path, db_factory) -> None:
    StaleNotificationClient.closed_early = False
    StaleNotificationClient.consumed_follow_up = False
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{"name":"demo"}', encoding="utf-8")
    service = ClaudeSessionService(
        data_root=tmp_path,
        session_factory=db_factory,
        client_factory=lambda options: StaleNotificationClient(options, call_number=1),
    )
    accepted = await service.send_action(
        "demo", ClaudeAction(action="create_screenplay", episode=1, client_key="stale"), user_id="default"
    )
    await service.wait_for_idle(accepted["session"]["id"])

    assert StaleNotificationClient.consumed_follow_up
    assert not StaleNotificationClient.closed_early
    await service.shutdown()
