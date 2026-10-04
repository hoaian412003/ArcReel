from __future__ import annotations

from decimal import Decimal
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, UserMessage
from claude_agent_sdk.types import ToolResultBlock, ToolUseBlock
from sqlalchemy import select

from lib.db.models.agent_turn import AgentTurn
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
