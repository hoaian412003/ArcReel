from __future__ import annotations

import pytest

from server.claude_runtime.actions import ClaudeAction, render_action


@pytest.mark.parametrize(
    "action", ["create_screenplay", "create_direction", "continue_episode", "redo_episode", "author_video_unit"]
)
def test_episode_actions_require_episode_context(action) -> None:
    with pytest.raises(ValueError, match="episode is required"):
        render_action(ClaudeAction(action=action, client_key="test"))


def test_shot_action_requires_script_context() -> None:
    with pytest.raises(ValueError, match="script_file is required"):
        render_action(ClaudeAction(action="author_shot", resource_id="shot-4", client_key="test"))


def test_reference_unit_action_targets_only_requested_unit() -> None:
    rendered = render_action(
        ClaudeAction(
            action="author_video_unit", episode=4, resource_id="U7", note="Use a slow push in", client_key="test"
        )
    )
    assert "unit 'U7' in episode EP04" in rendered.content
    assert "Use a slow push in" in rendered.content
    assert rendered.provenance["resource_id"] == "U7"
    assert "client_key" not in rendered.provenance


def test_canvas_assist_points_the_agent_at_the_canvas_tools_and_the_selected_node() -> None:
    rendered = render_action(
        ClaudeAction(action="canvas_assist", resource_id="p:unit:1:E1U1", note="Draft a prompt", client_key="test")
    )
    assert "get_canvas" in rendered.content
    assert "'p:unit:1:E1U1'" in rendered.content
    assert "Draft a prompt" in rendered.content


def test_canvas_assist_works_without_a_selection_or_note() -> None:
    rendered = render_action(ClaudeAction(action="canvas_assist", client_key="test"))
    assert "selected node" not in rendered.content
