"""Versioned project-agent actions independent of translated UI copy."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClaudeAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "create_screenplay",
        "create_direction",
        "continue_episode",
        "redo_episode",
        "author_shot",
        "author_video_unit",
        "repair_project",
        "remember",
        "canvas_assist",
    ]
    episode: int | None = Field(default=None, ge=1, le=10000)
    resource_id: str | None = Field(default=None, min_length=1, max_length=200)
    script_file: str | None = Field(default=None, max_length=500)
    note: str = Field(default="", max_length=10000)
    client_key: str = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class ActionPrompt:
    content: str
    provenance: dict


def render_action(action: ClaudeAction) -> ActionPrompt:
    if (
        action.action
        in {"create_screenplay", "create_direction", "continue_episode", "redo_episode", "author_video_unit"}
        and action.episode is None
    ):
        raise ValueError("episode is required")
    if action.action in {"author_shot", "author_video_unit"} and action.resource_id is None:
        raise ValueError("resource_id is required")
    if action.action == "author_shot" and not action.script_file:
        raise ValueError("script_file is required")
    if action.action == "remember" and not action.note.strip():
        raise ValueError("memory note is required")
    episode = f"EP{action.episode:02d}" if action.episode is not None else ""
    templates = {
        "create_screenplay": f"Create the screenplay for episode {episode}.",
        "redo_episode": f"Revise the episode direction and storyboard for {episode} according to the review feedback.",
        "create_direction": f"Create the missing episode direction for {episode}.",
        "continue_episode": f"Continue the next required workflow step for episode {episode}. Inspect its current state before proceeding.",
        "author_shot": f"Author the missing storyboard and video prompts for shot {action.resource_id!r} in script {action.script_file!r}.",
        "author_video_unit": f"Author the missing video prompt for unit {action.resource_id!r} in episode {episode}.",
        "repair_project": "Inspect and repair the damaged project data using the ArcReel repair workflow.",
        "remember": "Remember the following information in this project's persistent memory for future sessions.",
        "canvas_assist": (
            "Work directly on this project's node canvas, which the user is watching live. Start with get_canvas,"
            " then use the canvas_* tools to add, connect, edit and run nodes."
            + (f" The user has selected node {action.resource_id!r}." if action.resource_id else "")
        ),
    }
    prompt = templates[action.action] + (
        " Inspect the existing project context, follow its skills and workflow, and use the ArcReel MCP tools."
        " Preserve approved assets unless this request explicitly asks to revise them."
    )
    if action.note.strip():
        prompt += "\n\nUser instructions:\n" + action.note.strip()
    return ActionPrompt(prompt, {**action.model_dump(exclude={"client_key"}), "prompt_version": 1})
