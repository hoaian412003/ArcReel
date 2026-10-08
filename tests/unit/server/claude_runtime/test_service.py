from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, UserMessage
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

    async def receive_response(self):
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
