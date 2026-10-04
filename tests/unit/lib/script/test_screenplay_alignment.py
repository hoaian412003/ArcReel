"""单元拆解与已通过剧本的对齐判定：场次覆盖、顺序、台词保真与时长偏差。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.script.screenplay_alignment import collect_screenplay_violations, scene_numbers_from_response


def _screenplay() -> dict[str, Any]:
    return {
        "scenes": [
            {
                "heading": "内景 · 府门 · 日",
                "summary": "s",
                "estimated_seconds": 10,
                "text": "**老管家**：夫人，这是侯爷的亲笔信。",
            },
            {
                "heading": "外景 · 官道 · 夜",
                "summary": "s",
                "estimated_seconds": 10,
                "text": "（画外音）他再也没有回来。",
            },
        ]
    }


def _label(entry: dict[str, Any], index: int) -> str:
    return f"scene E1S{index + 1:02d}"


def _drama(text: str, duration: int = 10) -> dict[str, Any]:
    return {"duration_seconds": duration, "utterances": [{"kind": "dialogue", "speaker": "老管家", "text": text}]}


def _codes(violations) -> list[str]:
    return [violation.code for violation in violations]


def _check(entries, numbers, *, kind="drama", source_text=None):
    return collect_screenplay_violations(
        entries, numbers, _screenplay(), kind=kind, label_of=_label, source_text=source_text
    )


def test_a_faithful_split_has_no_violations_and_no_warnings() -> None:
    violations, warnings = _check([_drama("夫人，这是侯爷的亲笔信。"), _drama("他再也没有回来。")], [1, 2])

    assert violations == []
    assert warnings == []


def test_missing_scene_numbers_are_reported_once() -> None:
    violations, _ = _check([_drama("夫人，这是侯爷的亲笔信。")], [])

    assert _codes(violations) == ["scene_no_missing"]


@pytest.mark.parametrize("bad", [0, 3, None, "1", True, 1.5])
def test_out_of_range_or_non_integer_scene_numbers_are_invalid(bad: object) -> None:
    violations, _ = _check([_drama("夫人，这是侯爷的亲笔信。"), _drama("他再也没有回来。")], [bad, 2])

    assert "scene_no_invalid" in _codes(violations)


def test_scene_numbers_must_not_regress() -> None:
    violations, _ = _check([_drama("他再也没有回来。"), _drama("夫人，这是侯爷的亲笔信。")], [2, 1])

    assert "scene_no_regress" in _codes(violations)


def test_every_scene_needs_at_least_one_unit() -> None:
    violations, _ = _check([_drama("夫人，这是侯爷的亲笔信。")], [1])

    assert _codes(violations) == ["scene_not_covered"]
    assert "[2]" in violations[0].args[0]


def test_dialogue_must_come_verbatim_from_the_screenplay() -> None:
    violations, _ = _check([_drama("夫人，侯爷来信了。"), _drama("他再也没有回来。")], [1, 2])

    assert _codes(violations) == ["utterance_not_in_screenplay"]
    assert violations[0].label == "scene E1S01"


def test_comparison_ignores_whitespace_and_markdown_emphasis() -> None:
    violations, _ = _check([_drama("夫人， 这是 侯爷的亲笔信。"), _drama("他再也没有回来。")], [1, 2])

    assert violations == []


def test_source_text_is_an_accepted_origin_for_finished_screenplay_sources() -> None:
    entries = [_drama("夫人，这是侯爷的亲笔信。"), _drama("原作里的另一句话")]

    without_source, _ = _check(entries, [1, 2])
    with_source, _ = _check(entries, [1, 2], source_text="……原作里的另一句话……")

    assert _codes(without_source) == ["utterance_not_in_screenplay"]
    assert with_source == []


def test_narration_spoken_text_comes_from_the_source_so_it_is_not_judged_here() -> None:
    entries = [{"duration_seconds": 10, "novel_text": "任意原文"}, {"duration_seconds": 10, "novel_text": "任意原文二"}]

    violations, _ = _check(entries, [1, 2], kind="narration")

    assert violations == []


def test_reference_video_dialogue_marks_are_read_from_the_unit_text() -> None:
    entries = [
        {"duration_seconds": 10, "text": "@[老管家] 递上书信。\n@[老管家]{夫人，这是侯爷的亲笔信。}"},
        {"duration_seconds": 10, "text": "夜色沉沉。{他再也没有回来。}\n@[老管家]{我编的一句}"},
    ]

    violations, _ = _check(entries, [1, 2], kind="reference_video")

    assert _codes(violations) == ["utterance_not_in_screenplay"]
    assert "我编的一句" in violations[0].args[0]


def test_a_large_drift_from_the_estimated_scene_time_is_only_a_warning() -> None:
    violations, warnings = _check([_drama("夫人，这是侯爷的亲笔信。", 30), _drama("他再也没有回来。", 11)], [1, 2])

    assert violations == []
    assert warnings == ["剧本第 1 场预计 10 秒，拆出的单元合计 30 秒，偏差较大"]


def test_scene_numbers_are_read_from_the_raw_response_in_item_order() -> None:
    response = "```json\n" + json.dumps({"scenes": [{"scene_no": 1}, {"x": 1}, {"scene_no": 2}]}) + "\n```"

    assert scene_numbers_from_response(response, "scenes") == [1, None, 2]
    assert scene_numbers_from_response("not json", "scenes") == []
    assert scene_numbers_from_response('{"other": []}', "scenes") == []
