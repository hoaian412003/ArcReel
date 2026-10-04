"""独立队列服务与后端之间的项目事件转送。"""

from __future__ import annotations

import asyncio

from lib.infra.pg_bus import CHANNEL_PROJECT
from lib.project.project_change_hints import (
    emit_project_change_batch,
    emit_project_change_hint,
    register_project_change_batch_listener,
    register_project_change_listener,
)
from server import project_event_bridge


def test_batch_from_bus_is_replayed_into_the_local_bus_with_its_source():
    seen: list[tuple[str, str, tuple[dict, ...]]] = []
    unregister = register_project_change_batch_listener(
        lambda project, source, changes: seen.append((project, source, changes))
    )
    try:
        project_event_bridge.handle_project_event(
            {
                "kind": "batch",
                "project": "demo",
                "source": "worker",
                "changes": [{"entity_type": "task", "action": "task_succeeded"}],
            }
        )
    finally:
        unregister()

    assert seen == [("demo", "worker", ({"entity_type": "task", "action": "task_succeeded"},))]


def test_hint_from_bus_is_replayed_with_its_paths():
    seen: list[tuple[str, str, tuple[str, ...]]] = []
    unregister = register_project_change_listener(lambda project, source, paths: seen.append((project, source, paths)))
    try:
        project_event_bridge.handle_project_event(
            {"kind": "hint", "project": "demo", "source": "worker", "paths": ["a.json", "a.json", "b.json"]}
        )
    finally:
        unregister()

    assert seen == [("demo", "worker", ("a.json", "b.json"))]


def test_truncated_or_malformed_payloads_are_dropped():
    seen: list[object] = []
    unregister = register_project_change_batch_listener(lambda *args: seen.append(args))
    try:
        project_event_bridge.handle_project_event({"truncated": True})
        project_event_bridge.handle_project_event({"kind": "batch", "project": "", "changes": [{"a": 1}]})
        project_event_bridge.handle_project_event({"kind": "batch", "project": "demo", "truncated": True})
        project_event_bridge.handle_project_event({"kind": "unknown", "project": "demo"})
    finally:
        unregister()

    assert seen == []


async def test_publisher_forwards_local_events_to_the_bus_until_uninstalled(monkeypatch):
    sent: list[tuple[str, dict]] = []

    async def fake_notify(channel: str, payload: dict | None = None) -> None:
        sent.append((channel, payload or {}))

    monkeypatch.setattr(project_event_bridge, "notify", fake_notify)
    uninstall = project_event_bridge.install_publisher(asyncio.get_running_loop())

    emit_project_change_batch("demo", [{"entity_type": "task"}], source="worker")
    emit_project_change_hint("demo", source="worker", changed_paths=[f"f{i}" for i in range(100)])
    await asyncio.sleep(0.05)
    uninstall()
    emit_project_change_batch("demo", [{"entity_type": "task"}], source="worker")
    await asyncio.sleep(0.05)

    assert [c for c, _ in sent] == [CHANNEL_PROJECT, CHANNEL_PROJECT]
    batch, hint = (payload for _, payload in sent)
    assert batch == {"kind": "batch", "project": "demo", "source": "worker", "changes": [{"entity_type": "task"}]}
    assert hint["kind"] == "hint"
    assert len(hint["paths"]) == 40
