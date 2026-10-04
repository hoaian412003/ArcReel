from __future__ import annotations

from typing import Any

from lib.script.director_validation import DIRECTOR_INTENT_MIN_CHARS, collect_director_violations


def _plan() -> dict[str, Any]:
    return {
        "episode_intent": "让观众感到压迫后释然",
        "arc": [{"beat": "压迫", "units": [1]}, {"beat": "释然", "units": [2]}],
        "tone": "先压后松",
        "motifs": [],
    }


def _direction(**camera: str) -> dict[str, Any]:
    return {
        "camera": {
            "angle": "低角度",
            "shot_size": "特写",
            "movement": "缓慢推进",
            "intent": "托出对方压人的气势",
            **camera,
        },
        "performance": [{"character": "甲", "note": "语速放慢，目光不动"}],
        "continuity_in": "门口",
        "continuity_out": "桌前",
    }


def _output(**overrides: Any) -> dict[str, Any]:
    output = {
        "director_plan": _plan(),
        "entries": [{"id": "E1S01", "direction": _direction()}, {"id": "E1S02", "direction": _direction()}],
    }
    output.update(overrides)
    return output


def _collect(output: dict[str, Any], **kwargs: Any):
    return collect_director_violations(
        output,
        expected_ids=kwargs.pop("expected_ids", ["E1S01", "E1S02"]),
        characters_by_id=kwargs.pop("characters_by_id", {"E1S01": ["甲"], "E1S02": ["甲"]}),
        total_entries=kwargs.pop("total_entries", 2),
    )


def test_complete_output_has_no_violations() -> None:
    assert _collect(_output()) == []


def test_camera_fields_are_free_text_without_a_fixed_vocabulary() -> None:
    direction = _direction(angle="荷兰角（略微倾斜）", shot_size="腰部以上的偏近景", movement="手持呼吸感")
    output = _output(entries=[{"id": "E1S01", "direction": direction}, {"id": "E1S02", "direction": _direction()}])

    assert _collect(output) == []


def test_blank_camera_fields_are_reported_per_entry() -> None:
    output = _output(
        entries=[{"id": "E1S01", "direction": _direction(angle="  ")}, {"id": "E1S02", "direction": _direction()}]
    )

    violations = _collect(output)

    assert [violation.label for violation in violations] == ["entry E1S01"]
    assert violations[0].code == "director_entry_invalid"
    assert "camera.angle" in str(violations[0])


def test_generic_or_short_intent_is_rejected() -> None:
    short = "好" * (DIRECTOR_INTENT_MIN_CHARS - 1)
    output = _output(
        entries=[
            {"id": "E1S01", "direction": _direction(intent=short)},
            {"id": "E1S02", "direction": _direction(intent="N/A")},
        ]
    )

    violations = _collect(output)

    assert [violation.label for violation in violations] == ["entry E1S01", "entry E1S02"]
    assert all("camera.intent" in str(violation) for violation in violations)


def test_every_character_in_the_entry_needs_a_performance_note() -> None:
    violations = _collect(_output(), characters_by_id={"E1S01": ["甲", "乙"], "E1S02": ["甲"]})

    assert len(violations) == 1
    assert violations[0].label == "entry E1S01"
    assert "乙" in str(violations[0])


def test_empty_continuity_is_rejected() -> None:
    direction = _direction()
    direction["continuity_out"] = ""
    output = _output(entries=[{"id": "E1S01", "direction": direction}, {"id": "E1S02", "direction": _direction()}])

    violations = _collect(output)

    assert len(violations) == 1
    assert "continuity_out" in str(violations[0])


def test_entries_must_match_the_requested_scope_exactly() -> None:
    output = _output(entries=[{"id": "E1S01", "direction": _direction()}, {"id": "E1S09", "direction": _direction()}])

    violations = _collect(output)

    mismatch = next(violation for violation in violations if violation.code == "director_entries_mismatch")
    assert "E1S02" in str(mismatch)
    assert "E1S09" in str(mismatch)


def test_director_plan_needs_two_arc_beats_that_reference_real_entries() -> None:
    plan = _plan()
    plan["arc"] = [{"beat": "压迫", "units": [5]}]

    violations = _collect(_output(director_plan=plan))

    assert [violation.code for violation in violations] == ["director_plan_invalid"]
    assert "arc" in str(violations[0])


def test_partial_revision_still_validates_the_plan_against_the_whole_episode() -> None:
    output = _output(entries=[{"id": "E1S02", "direction": _direction()}])

    assert _collect(output, expected_ids=["E1S02"], total_entries=2) == []
