from __future__ import annotations

from typing import Any

import pytest

from lib.script import direction_review


def _direction() -> dict[str, Any]:
    return {
        "camera": {"angle": "低角度", "shot_size": "特写", "movement": "推进", "intent": "托出对方的气势感"},
        "performance": [],
        "continuity_in": "门口",
        "continuity_out": "桌前",
    }


def _script(*, directed: bool = True, pending: bool = True, plan: bool = True) -> dict[str, Any]:
    entry: dict[str, Any] = {"segment_id": "E1S01", "novel_text": "原文"}
    if pending:
        entry["pending_authoring"] = True
    if directed:
        entry["direction"] = _direction()
    script: dict[str, Any] = {"segments": [entry]}
    if plan:
        script["director_plan"] = {"episode_intent": "x", "arc": [], "tone": "y", "motifs": []}
    return script


def _project(mode: str = "narration", generation_mode: str = "storyboard", **review: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"episode": 1}
    if review:
        entry["direction_review"] = review
    return {"content_mode": mode, "generation_mode": generation_mode, "episodes": [entry]}


@pytest.mark.parametrize(
    ("mode", "generation_mode", "applies"),
    [
        ("narration", "storyboard", True),
        ("drama", "storyboard", True),
        ("ad", "storyboard", False),
        ("ad", "reference_video", False),
    ],
)
def test_the_storyboard_director_step_applies_to_narration_and_drama(
    mode: str, generation_mode: str, applies: bool
) -> None:
    project = _project(mode, generation_mode)
    assert direction_review.applies_to_project(project) is applies
    assert direction_review.entry_kind(project) == (
        {"narration": "segments", "drama": "scenes"}[mode] if applies else None
    )
    script = _script()
    if mode == "drama":
        script["scenes"] = script.pop("segments")
    assert direction_review.review_status(project, script, 1) == ("pending_review" if applies else "not_applicable")


def test_missing_script_or_missing_direction_is_no_direction() -> None:
    project = _project()
    assert direction_review.review_status(project, None, 1) == "no_direction"
    assert direction_review.review_status(project, _script(directed=False), 1) == "no_direction"
    assert direction_review.review_status(project, _script(plan=False), 1) == "no_direction"


def test_authored_episode_without_director_output_is_grandfathered() -> None:
    assert direction_review.review_status(_project(), _script(directed=False, pending=False), 1) == "not_applicable"


def test_decision_applies_only_while_the_direction_fingerprint_matches() -> None:
    project = _project()
    script = _script()
    fingerprint = direction_review.direction_fingerprint(script, "segments")
    assert fingerprint is not None

    direction_review.apply_decision(
        project,
        1,
        fingerprint=fingerprint,
        decision="approve",
        decided_at="2026-10-03T00:00:00+00:00",
        note=None,
        entry_ids=None,
    )
    assert direction_review.review_status(project, script, 1) == "approved"

    script["segments"][0]["direction"]["camera"]["angle"] = "高角度"
    assert direction_review.review_status(project, script, 1) == "pending_review"


@pytest.mark.parametrize("decision", ["changes", "redo"])
def test_change_decisions_read_back_as_changes_requested(decision: str) -> None:
    project = _project()
    script = _script()
    direction_review.apply_decision(
        project,
        1,
        fingerprint=direction_review.direction_fingerprint(script, "segments") or "",
        decision=decision,
        decided_at="2026-10-03T00:00:00+00:00",
        note="再狠一点",
        entry_ids=None,
    )

    assert direction_review.review_status(project, script, 1) == "changes_requested"
    assert direction_review.stored_review(project, 1)["note"] == "再狠一点"


def test_fingerprint_ignores_everything_but_the_director_data() -> None:
    script = _script()
    before = direction_review.direction_fingerprint(script, "segments")
    script["segments"][0]["novel_text"] = "改过的原文"
    script["segments"][0]["image_prompt"] = "新画面"
    del script["segments"][0]["pending_authoring"]

    assert direction_review.direction_fingerprint(script, "segments") == before
    assert direction_review.direction_fingerprint(_script(directed=False, plan=False), "segments") is None


def test_apply_decision_reports_an_unknown_episode() -> None:
    assert not direction_review.apply_decision(
        _project(), 9, fingerprint="f", decision="approve", decided_at="t", note=None, entry_ids=None
    )


def test_undirected_entry_ids_lists_entries_still_missing_direction() -> None:
    script = _script()
    script["segments"].append({"segment_id": "E1S02", "novel_text": "乙"})

    assert direction_review.undirected_entry_ids(script, "segments") == ["E1S02"]
    assert not direction_review.direction_complete(script, "segments")


def _reference_unit(**overrides: Any) -> dict[str, Any]:
    unit: dict[str, Any] = {"unit_id": "E1U01", "text": "@[阿离]{我回来了。}", "duration_seconds": 8}
    unit.update(overrides)
    return unit


def _shot() -> dict[str, Any]:
    return {"at_seconds": 0, "framing": "中景", "camera": {"type": "static_shot"}, "action": "阿离推门"}


@pytest.mark.parametrize("mode", ["narration", "drama"])
def test_reference_video_projects_direct_their_video_units(mode: str) -> None:
    project = _project(mode, "reference_video")

    assert direction_review.applies_to_project(project) is True
    assert direction_review.entry_kind(project) == "video_units"


def test_a_reference_unit_is_directed_once_it_has_a_brief_and_shots() -> None:
    kind = direction_review.REFERENCE_KIND

    assert direction_review.entry_direction(_reference_unit(), kind) is None
    assert direction_review.entry_direction(_reference_unit(brief="阿离回到山门，迎面撞上旧识。"), kind) is None
    directed = direction_review.entry_direction(_reference_unit(brief="阿离回到山门。", shots=[_shot()]), kind)
    assert directed == {"brief": "阿离回到山门。", "shots": [_shot()]}


def test_legacy_reference_units_with_beats_count_as_directed() -> None:
    unit = _reference_unit(brief="旧规划", beats=[{"at_pct": 0, "action": "推门"}])

    assert direction_review.entry_direction(unit, direction_review.REFERENCE_KIND) is not None


def test_reference_review_status_follows_the_flat_direction_fields() -> None:
    project = _project("drama", "reference_video")
    plan = {"episode_intent": "x", "arc": [], "tone": "y", "motifs": []}
    pending = {"director_plan": plan, "video_units": [_reference_unit(pending_authoring=True)]}
    directed_unit = _reference_unit(pending_authoring=True, brief="阿离回到山门。", shots=[_shot()])
    directed = {"director_plan": plan, "video_units": [directed_unit]}

    assert direction_review.review_status(project, pending, 1) == "no_direction"
    assert direction_review.review_status(project, directed, 1) == "pending_review"
    fingerprint = direction_review.direction_fingerprint(directed, "video_units")
    assert fingerprint is not None
    edited = {**directed, "video_units": [{**directed_unit, "music": "低沉大提琴"}]}
    assert direction_review.direction_fingerprint(edited, "video_units") != fingerprint
