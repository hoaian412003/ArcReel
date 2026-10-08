from __future__ import annotations

import pytest

from lib.speech.dub_timeline import (
    DUB_LINE_GAP_SECONDS,
    DubTimingInput,
    dub_overflow_seconds,
    schedule_dub_lines,
)


def test_sequential_with_audio_durations_and_gap():
    slots = schedule_dub_lines(
        [DubTimingInput("a", audio_duration=2.0), DubTimingInput("b", audio_duration=1.5)],
    )
    assert [s.start_seconds for s in slots] == [0.0, pytest.approx(2.0 + DUB_LINE_GAP_SECONDS)]
    assert [s.duration_seconds for s in slots] == [2.0, 1.5]
    assert not any(s.estimated for s in slots)


def test_estimates_from_vietnamese_word_rate_when_no_audio():
    # vi 语速 2.5 词/秒：5 个词 = 2 秒
    (slot,) = schedule_dub_lines([DubTimingInput("một hai ba bốn năm")])
    assert slot.estimated
    assert slot.duration_seconds == pytest.approx(2.0)


def test_override_wins_and_following_lines_continue_from_it():
    slots = schedule_dub_lines(
        [
            DubTimingInput("a", audio_duration=1.0),
            DubTimingInput("b", start_override=5.0, audio_duration=1.0),
            DubTimingInput("c", audio_duration=1.0),
        ]
    )
    assert slots[1].start_seconds == 5.0
    assert slots[2].start_seconds == pytest.approx(5.0 + 1.0 + DUB_LINE_GAP_SECONDS)


def test_overflow_seconds():
    slots = schedule_dub_lines([DubTimingInput("a", audio_duration=3.0)])
    assert dub_overflow_seconds(slots, 5.0) == 0.0
    assert dub_overflow_seconds(slots, 2.0) == pytest.approx(1.0)
    assert dub_overflow_seconds([], 2.0) == 0.0


def test_detected_start_is_used_when_there_is_no_manual_override():
    slots = schedule_dub_lines(
        [
            DubTimingInput("a", audio_duration=1.0, detected_start=3.0),
            DubTimingInput("b", audio_duration=1.0, detected_start=8.0),
        ]
    )

    assert [s.start_seconds for s in slots] == [3.0, 8.0]
    assert [s.source for s in slots] == ["detected", "detected"]


def test_detected_start_never_overlaps_the_previous_dub_line():
    # 第二句在原视频里 2.5 秒就开始了，但第一句配音要念到 4.0 秒，顺延而不是叠在一起
    slots = schedule_dub_lines(
        [
            DubTimingInput("a", audio_duration=4.0, detected_start=0.0),
            DubTimingInput("b", audio_duration=1.0, detected_start=2.5),
        ]
    )

    assert slots[1].start_seconds == pytest.approx(4.0 + DUB_LINE_GAP_SECONDS)
    assert slots[1].source == "detected"


def test_manual_override_beats_detected_and_sources_are_labelled():
    slots = schedule_dub_lines(
        [
            DubTimingInput("a", audio_duration=1.0, start_override=5.0, detected_start=2.0),
            DubTimingInput("b", audio_duration=1.0),
        ]
    )

    assert slots[0].start_seconds == 5.0
    assert [s.source for s in slots] == ["manual", "estimated"]


def test_a_line_pushed_back_by_the_previous_dub_reports_how_late_it_is():
    slots = schedule_dub_lines(
        [
            DubTimingInput("a", audio_duration=2.8, detected_start=1.0),
            DubTimingInput("b", audio_duration=1.0, detected_start=3.0),
            DubTimingInput("c", audio_duration=1.0, detected_start=9.0),
        ]
    )

    # a 在 1.0 秒开始、念到 3.8 秒；b 想在 3.0 秒开始，只能顺延到 4.0 秒（3.8 加间隙 0.2），晚了 1.0 秒
    assert slots[0].delayed_seconds == 0.0
    assert slots[1].start_seconds == pytest.approx(4.0)
    assert slots[1].delayed_seconds == pytest.approx(1.0)
    assert slots[2].delayed_seconds == 0.0


def test_only_detected_lines_can_be_delayed():
    slots = schedule_dub_lines(
        [
            DubTimingInput("a", audio_duration=5.0, start_override=0.0),
            DubTimingInput("b", audio_duration=1.0),
            DubTimingInput("c", audio_duration=1.0, start_override=1.0),
        ]
    )

    assert [s.delayed_seconds for s in slots] == [0.0, 0.0, 0.0]


def test_planned_start_is_used_when_nothing_else_places_the_line():
    slots = schedule_dub_lines(
        [
            DubTimingInput("a", audio_duration=2.0, planned_start=1.0),
            DubTimingInput("b", audio_duration=2.0, planned_start=5.0),
        ]
    )

    assert [s.start_seconds for s in slots] == [1.0, 5.0]
    assert [s.source for s in slots] == ["planned", "planned"]


def test_the_directors_planned_start_outranks_the_detected_start():
    (slot,) = schedule_dub_lines([DubTimingInput("a", audio_duration=1.0, detected_start=3.0, planned_start=1.0)])

    assert (slot.start_seconds, slot.source) == (1.0, "planned")


def test_manual_override_outranks_the_directors_planned_start():
    (slot,) = schedule_dub_lines([DubTimingInput("a", audio_duration=1.0, start_override=2.0, planned_start=1.0)])

    assert (slot.start_seconds, slot.source) == (2.0, "manual")


def test_planned_start_is_pushed_back_when_the_previous_line_is_still_running():
    slots = schedule_dub_lines(
        [
            DubTimingInput("a", audio_duration=4.0, planned_start=1.0),
            DubTimingInput("b", audio_duration=1.0, planned_start=3.0),
        ]
    )

    assert slots[1].start_seconds == pytest.approx(5.0 + DUB_LINE_GAP_SECONDS)
    assert slots[1].delayed_seconds == pytest.approx(2.0 + DUB_LINE_GAP_SECONDS)
