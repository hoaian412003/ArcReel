from __future__ import annotations

from typing import Any

import pytest

from lib.workflow.voice_over import VoiceOverFacts, unit_voice_over_complete, voice_over_applies
from lib.workflow.workflow_plan import WorkflowStepState, build_workflow_plan
from lib.workflow.workflow_rules import VOICE_OVER_WORKFLOW_RULES, WORKFLOW_RULES, workflow_rule
from lib.workflow.workflow_state import (
    WorkflowActionType,
    WorkflowNextAction,
    WorkflowProject,
    WorkflowStatus,
    WorkflowTarget,
)

_TEXT = "海面\n{风从远方吹来。}"


def _line(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "index": 0,
        "speaker": None,
        "gender": "male",
        "source_text": "风从远方吹来。",
        "text_vi": "Gió thổi từ phương xa.",
        "audio_path": "audio/dub_E1U1_0.wav",
        "audio_text": "Gió thổi từ phương xa.",
        "audio_duration_seconds": 2.0,
    }
    line.update(overrides)
    return line


def _unit(*lines: dict[str, Any], video: bool = True, unit_id: str = "E1U1", text: str = _TEXT) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "text": text,
        "duration_seconds": 8,
        "dub_lines": list(lines),
        "generated_assets": {"video_clip": "videos/x.mp4"} if video else {},
    }


def _status(state: str = "EXPORT_READY", generation_mode: str = "reference_video") -> WorkflowStatus:
    action = "export" if state == "EXPORT_READY" else "generate_videos"
    return WorkflowStatus.model_validate(
        {
            "schema_version": 1,
            "project_revision": "sha256-v1:project",
            "source_revision": "sha256-v1:source",
            "project": WorkflowProject(
                content_mode="narration", generation_mode=generation_mode, grid_storyboard=False
            ),
            "target": WorkflowTarget(
                episode=1, script="scripts/episode_1.json", script_filename="episode_1.json", source="source/e.txt"
            ),
            "state": state,
            "blockers": [],
            "gates": {},
            "artifacts": {"script": {"state": "current", "path": "scripts/episode_1.json"}},
            "next_action": WorkflowNextAction(type=WorkflowActionType(action), reason="r"),
        }
    )


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ({"generation_mode": "reference_video", "dub_target_language": "vi"}, True),
        ({"generation_mode": "reference_video"}, False),
        ({"generation_mode": "reference_video", "dub_target_language": "en"}, False),
        ({"generation_mode": "storyboard", "dub_target_language": "vi"}, False),
    ],
)
def test_voice_over_applies_only_to_reference_video_with_vietnamese_dub(
    project: dict[str, Any], expected: bool
) -> None:
    assert voice_over_applies(project) is expected


def test_unit_is_complete_when_every_line_has_current_audio() -> None:
    assert unit_voice_over_complete(_unit(_line())) is True


@pytest.mark.parametrize(
    "unit",
    [
        _unit(),
        _unit(_line(audio_path=None)),
        _unit(_line(audio_text="đã cũ")),
        _unit(_line(source_text="câu gốc đã đổi")),
    ],
    ids=["missing-line", "no-audio", "stale-audio", "stale-line"],
)
def test_unit_is_incomplete_without_current_lines_and_audio(unit: dict[str, Any]) -> None:
    assert unit_voice_over_complete(unit) is False


def test_unit_without_spoken_lines_has_nothing_to_dub() -> None:
    assert unit_voice_over_complete(_unit(text="只有画面")) is True


def test_facts_cover_only_units_with_video() -> None:
    script = {
        "video_units": [
            _unit(_line(), unit_id="U1"),
            _unit(unit_id="U2"),
            _unit(unit_id="U3", video=False),
        ]
    }

    facts = VoiceOverFacts.from_script(script)

    assert facts.complete_ids == ("U1",)
    assert facts.missing_ids == ("U2",)
    assert facts.complete is False


def test_plain_rules_never_carry_the_step() -> None:
    for rule in WORKFLOW_RULES.values():
        assert "voice_over" not in [step.id for step in rule.steps]
    assert set(VOICE_OVER_WORKFLOW_RULES) == {
        (content_mode, "reference_video") for content_mode in ("narration", "drama", "ad")
    }


def test_step_sits_between_video_and_export_without_a_checkpoint() -> None:
    rule = workflow_rule("drama", "reference_video", voice_over=True)
    ids = [step.id for step in rule.steps]

    assert ids.index("video") + 1 == ids.index("voice_over") == ids.index("export") - 1
    assert next(step for step in rule.steps if step.id == "voice_over").checkpoint is None


def test_plan_without_voice_over_facts_is_unchanged() -> None:
    plan = build_workflow_plan(_status())

    assert "voice_over" not in [step.id for step in plan.steps]


@pytest.mark.parametrize(
    ("facts", "state"),
    [
        (VoiceOverFacts.from_script({"video_units": [_unit(_line())]}), WorkflowStepState.COMPLETED),
        (VoiceOverFacts.from_script({"video_units": [_unit()]}), WorkflowStepState.READY),
        (VoiceOverFacts.from_script({"video_units": [_unit()]}, active=True), WorkflowStepState.ACTIVE),
        (VoiceOverFacts(), WorkflowStepState.PENDING),
    ],
)
def test_voice_over_never_blocks_export(facts: VoiceOverFacts, state: WorkflowStepState) -> None:
    plan = build_workflow_plan(_status(), voice_over=facts)

    step = next(step for step in plan.steps if step.id == "voice_over")
    assert step.state is state
    assert step.required is False
    assert next(step for step in plan.steps if step.id == "export").state is WorkflowStepState.READY
    assert plan.next_action.type is WorkflowActionType.EXPORT
    assert plan.blockers == []


def test_incomplete_step_points_at_the_dub_tools() -> None:
    facts = VoiceOverFacts.from_script({"video_units": [_unit(_line(), unit_id="U1"), _unit(unit_id="U2")]})

    plan = build_workflow_plan(_status(), voice_over=facts)

    artifacts = next(step for step in plan.steps if step.id == "voice_over").artifacts
    assert artifacts["current_ids"] == ["U1"]
    assert artifacts["missing_ids"] == ["U2"]
    assert artifacts["tool"] == "generate_dub_voiceover"
    assert artifacts["status_tool"] == "get_dub_status"


def test_step_waits_while_videos_are_still_being_generated() -> None:
    facts = VoiceOverFacts.from_script({"video_units": [_unit(_line())]})

    plan = build_workflow_plan(_status(state="VIDEO"), voice_over=facts)

    assert next(step for step in plan.steps if step.id == "voice_over").state is WorkflowStepState.PENDING
    assert next(step for step in plan.steps if step.id == "video").state is not WorkflowStepState.COMPLETED
