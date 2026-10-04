from __future__ import annotations

from typing import Any

from lib.script.director_report import REPEATED_ANGLE_RUN, build_director_report, continuity_connects


def _direction(
    angle: str = "平视",
    *,
    intent: str = "交代场景，让观众先安定下来",
    performance: list[dict[str, str]] | None = None,
    continuity_in: str = "客厅 傍晚",
    continuity_out: str = "客厅 傍晚",
) -> dict[str, Any]:
    return {
        "camera": {"angle": angle, "shot_size": "中景", "movement": "固定", "intent": intent},
        "performance": performance if performance is not None else [{"character": "甲", "note": "低头不语"}],
        "continuity_in": continuity_in,
        "continuity_out": continuity_out,
    }


def _segment(segment_id: str, direction: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    segment: dict[str, Any] = {
        "segment_id": segment_id,
        "novel_text": f"{segment_id} 的原文",
        "characters_in_segment": ["甲"],
    }
    if direction is not None:
        segment["direction"] = direction
    segment.update(extra)
    return segment


def _plan() -> dict[str, Any]:
    return {
        "episode_intent": "让观众看清甲的处境",
        "arc": [{"beat": "压抑", "units": [1, 2]}, {"beat": "爆发", "units": [3]}],
        "tone": "压抑",
        "motifs": ["旧钟"],
    }


def test_script_without_any_director_data_has_no_report() -> None:
    assert build_director_report({"segments": [_segment("E1S01", None)]}, "segments") is None


def test_report_lists_plan_rows_and_angle_distribution_in_script_order() -> None:
    script = {
        "director_plan": _plan(),
        "segments": [
            _segment("E1S01", _direction("低角度", intent="低角度：托出对方的气势")),
            _segment("E1S02", _direction("平视")),
            _segment("E1S03", _direction("低角度")),
        ],
    }

    report = build_director_report(script, "segments")

    assert report is not None
    assert report["episode_intent"] == "让观众看清甲的处境"
    assert report["tone"] == "压抑"
    assert report["arc"] == [{"beat": "压抑", "units": [1, 2]}, {"beat": "爆发", "units": [3]}]
    assert report["motifs"] == ["旧钟"]
    assert [row["id"] for row in report["rows"]] == ["E1S01", "E1S02", "E1S03"]
    assert report["rows"][0]["camera"] == {
        "angle": "低角度",
        "shot_size": "中景",
        "movement": "固定",
        "intent": "低角度：托出对方的气势",
    }
    assert report["rows"][0]["description"] == "E1S01 的原文"
    assert report["angle_distribution"] == {"低角度": 2, "平视": 1}
    assert report["warnings"] == []


def test_report_is_deterministic() -> None:
    script = {"director_plan": _plan(), "segments": [_segment("E1S01", _direction()), _segment("E1S02", _direction())]}

    assert build_director_report(script, "segments") == build_director_report(script, "segments")


def test_long_runs_of_the_same_angle_are_flagged() -> None:
    segments = [_segment(f"E1S{index:02d}", _direction("平视")) for index in range(1, REPEATED_ANGLE_RUN + 1)]
    segments.append(_segment("E1S09", _direction("高角度")))

    report = build_director_report({"director_plan": _plan(), "segments": segments}, "segments")

    assert report is not None
    repeated = [warning for warning in report["warnings"] if warning["code"] == "repeated_angle"]
    assert [warning["entry_ids"] for warning in repeated] == [
        [f"E1S{index:02d}" for index in range(1, REPEATED_ANGLE_RUN + 1)]
    ]


def test_continuity_gap_between_consecutive_entries_is_flagged_unless_the_next_is_a_scene_break() -> None:
    gap = _direction(continuity_in="雨夜 街头", continuity_out="雨夜 街头")
    far = _direction(continuity_in="清晨 厨房", continuity_out="清晨 厨房")
    segments = [_segment("E1S01", gap), _segment("E1S02", far), _segment("E1S03", far)]
    report = build_director_report({"director_plan": _plan(), "segments": segments}, "segments")
    assert report is not None
    assert [w["entry_ids"] for w in report["warnings"] if w["code"] == "continuity_gap"] == [["E1S01", "E1S02"]]

    segments[1] = _segment("E1S02", far, segment_break=True)
    report = build_director_report({"director_plan": _plan(), "segments": segments}, "segments")
    assert report is not None
    assert [w for w in report["warnings"] if w["code"] == "continuity_gap"] == []


def test_characters_without_performance_and_missing_intent_are_flagged() -> None:
    script = {
        "director_plan": _plan(),
        "segments": [
            _segment("E1S01", _direction(performance=[])),
            _segment("E1S02", _direction(intent="")),
        ],
    }

    report = build_director_report(script, "segments")

    assert report is not None
    by_code = {warning["code"]: warning for warning in report["warnings"]}
    assert by_code["missing_performance"]["entry_ids"] == ["E1S01"]
    assert by_code["missing_intent"]["entry_ids"] == ["E1S02"]


def test_entries_without_direction_keep_a_stable_row_shape_and_are_flagged() -> None:
    script = {"director_plan": _plan(), "segments": [_segment("E1S01", _direction()), _segment("E1S02", None)]}

    report = build_director_report(script, "segments")

    assert report is not None
    assert report["rows"][1]["camera"] == {"angle": "", "shot_size": "", "movement": "", "intent": ""}
    assert [w["entry_ids"] for w in report["warnings"] if w["code"] == "missing_direction"] == [["E1S02"]]


def test_drama_rows_describe_the_scene_and_use_scene_characters() -> None:
    scene = {
        "scene_id": "E1S01",
        "scene_description": "甲站在门口",
        "characters_in_scene": ["甲"],
        "direction": _direction(performance=[]),
    }

    report = build_director_report({"director_plan": _plan(), "scenes": [scene]}, "scenes")

    assert report is not None
    assert report["rows"][0]["description"] == "甲站在门口"
    assert [w["code"] for w in report["warnings"]] == ["missing_performance"]


def test_continuity_connects_on_shared_words_or_cjk_bigrams() -> None:
    assert continuity_connects("甲站在厨房门口", "厨房里，甲转身")
    assert continuity_connects("standing at the door", "At the door, rain falling")
    assert not continuity_connects("雨夜街头", "清晨厨房")
    assert not continuity_connects("", "清晨厨房")


def _reference_unit(unit_id: str, **direction: object) -> dict:
    unit: dict = {"unit_id": unit_id, "text": "@[阿离]{我回来了。}", "duration_seconds": 8}
    unit.update(direction)
    return unit


def _reference_direction(**overrides: object) -> dict:
    direction: dict = {
        "brief": "阿离回到山门，既想念又怕被认出。",
        "shots": [{"at_seconds": 0, "framing": "全景", "camera": {"type": "static_shot"}, "action": "推门"}],
        "performance": [{"character": "阿离", "note": "迟疑", "voice": "清亮偏低"}],
        "lines": [{"speaker": "阿离", "text": "我回来了。", "shot": 1, "delivery": "轻声"}],
        "continuity_in": "山门外，黄昏",
        "continuity_out": "山门内，黄昏",
    }
    direction.update(overrides)
    return direction


def test_reference_report_lists_shots_lines_and_camera_distribution() -> None:
    script = {
        "director_plan": {"episode_intent": "归来", "arc": [], "tone": "克制", "motifs": []},
        "video_units": [
            _reference_unit("E1U01", **_reference_direction()),
            _reference_unit("E1U02", **_reference_direction(continuity_in="山门内，黄昏")),
        ],
    }

    report = build_director_report(script, "video_units")

    assert report is not None
    assert report["route"] == "reference"
    row = report["rows"][0]
    assert row["shots"][0]["camera"] == {"type": "static_shot", "amplitude": "medium", "speed": "normal"}
    assert row["lines"] == [{"speaker": "阿离", "text": "我回来了。", "shot": 1, "delivery": "轻声"}]
    assert row["performance"][0]["voice"] == "清亮偏低"
    assert report["camera_distribution"] == {"static_shot": 2}
    assert report["warnings"] == []


def test_reference_report_flags_undirected_units_silent_speakers_and_gaps() -> None:
    script = {
        "video_units": [
            _reference_unit("E1U01", **_reference_direction(performance=[{"character": "阿离", "note": "迟疑"}])),
            _reference_unit("E1U02", **_reference_direction(continuity_in="完全不同的地方")),
            _reference_unit("E1U03"),
        ],
    }

    report = build_director_report(script, "video_units")

    assert report is not None
    codes = {warning["code"]: warning["entry_ids"] for warning in report["warnings"]}
    assert codes["missing_direction"] == ["E1U03"]
    assert codes["missing_voice"] == ["E1U01"]
    assert codes["continuity_gap"] == ["E1U01", "E1U02"]


def test_reference_report_is_none_without_any_direction() -> None:
    assert build_director_report({"video_units": [_reference_unit("E1U01")]}, "video_units") is None
