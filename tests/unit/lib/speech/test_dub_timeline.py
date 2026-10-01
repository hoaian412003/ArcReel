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
