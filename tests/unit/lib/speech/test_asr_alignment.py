from __future__ import annotations

import pytest

from lib.speech.asr_alignment import AsrSegment, AsrWord, align_lines_to_segments

# 实测：macOS 中文语音合成的三句话，台词真实开始时间 1.0 / 6.0 / 11.0 秒，
# large-v3-turbo 的识别结果（第二句把「夜深了」听成了「变身了」）。
REAL_SEGMENTS = [
    AsrSegment(0.98, 3.04, "你好,今晚的月色真美。"),
    AsrSegment(5.96, 7.68, "变身了,我们回去吧。"),
    AsrSegment(10.99, 12.49, "明天还要早起上班。"),
]
LINES = ["你好，今晚的月色真美。", "夜深了，我们回去吧。", "明天还要早起上班。"]


def test_real_recognition_with_a_misheard_word_still_aligns_each_line():
    starts = align_lines_to_segments(LINES, REAL_SEGMENTS)

    assert starts == [pytest.approx(0.98, abs=0.1), pytest.approx(5.96, abs=0.3), pytest.approx(10.99, abs=0.1)]


def test_one_segment_can_cover_several_lines():
    segments = [AsrSegment(2.0, 8.0, "你好今晚的月色真美夜深了我们回去吧")]

    first, second, third = align_lines_to_segments(LINES, segments)

    assert first == pytest.approx(2.0, abs=0.1)
    assert second is not None
    assert 2.0 < second < 8.0
    assert third is None


def test_a_line_that_was_never_spoken_is_left_undetected():
    segments = [REAL_SEGMENTS[0], REAL_SEGMENTS[2]]

    starts = align_lines_to_segments(LINES, segments)

    assert starts[0] is not None
    assert starts[1] is None
    assert starts[2] == pytest.approx(10.99, abs=0.1)


def test_start_is_walked_back_when_the_first_characters_were_not_recognised():
    # 前两个字（你好）没识别出来，识别从「今晚」开始于 3.0 秒
    segments = [AsrSegment(3.0, 5.0, "今晚的月色真美")]

    (start,) = align_lines_to_segments(["你好，今晚的月色真美。"], segments)

    assert start is not None
    assert start < 3.0
    assert start == pytest.approx(3.0 - 2 * (2.0 / 7), abs=0.05)


def test_unrelated_speech_matches_nothing():
    segments = [AsrSegment(0.0, 3.0, "this is something else entirely")]

    assert align_lines_to_segments(LINES, segments) == [None, None, None]


def test_empty_inputs():
    assert align_lines_to_segments([], REAL_SEGMENTS) == []
    assert align_lines_to_segments(LINES, []) == [None, None, None]
    assert align_lines_to_segments(["，。"], REAL_SEGMENTS) == [None]


def test_times_never_go_backwards():
    # 第二句匹配到比第三句更晚的位置属于误配，被丢弃而不是让时间倒流
    segments = [AsrSegment(9.0, 10.0, "我们回去吧"), AsrSegment(1.0, 3.0, "明天还要早起上班")]

    starts = align_lines_to_segments(["夜深了，我们回去吧。", "明天还要早起上班。"], segments)

    present = [s for s in starts if s is not None]
    assert present == sorted(present)


def test_full_and_half_width_and_case_are_ignored():
    segments = [AsrSegment(1.0, 2.0, "Hello World")]

    (start,) = align_lines_to_segments(["ＨＥＬＬＯ，world！"], segments)

    assert start == pytest.approx(1.0, abs=0.05)


# 实测：两个声音的七句对白（含两句相同的「好的」、一句与上一句重叠），large-v3-turbo 把它合并成
# 一个 0.14–16.08 秒的长分段；只有词级时间才能还原句间停顿。台词真实开始时间见 MULTI_TRUTH。
MULTI_LINES = [
    "你好。",
    "你好，好久不见，最近怎么样？",
    "还不错，你呢？",
    "好的。",
    "好的。",
    "我们明天一起去吃饭吧，我知道一家很好吃的店。",
    "太好了！",
]
MULTI_TRUTH = [0.5, 2.5, 6.2, 8.8, 10.4, 12.0, 15.69]
_MULTI_WORDS = [
    ("你好,", 0.14, 2.56), ("你好,", 2.56, 3.10), ("好", 3.10, 3.38), ("久", 3.38, 3.58), ("不", 3.58, 3.78),
    ("见,", 3.78, 4.24), ("最近", 4.24, 4.66), ("怎么样?", 4.66, 5.78), ("还", 5.78, 6.34), ("不", 6.34, 6.52),
    ("错,", 6.52, 7.00), ("你", 7.00, 7.24), ("呢?", 7.24, 8.72), ("好的。", 8.72, 10.14), ("好的。", 10.14, 11.80),
    ("我们", 11.80, 12.16), ("明", 12.16, 12.40), ("天", 12.40, 12.68), ("一起", 12.68, 12.98), ("吃", 12.98, 13.28),
    ("吃", 13.28, 13.48), ("饭", 13.48, 13.68), ("吧,", 13.68, 14.16), ("我知道", 14.16, 14.52), ("一", 14.52, 14.76),
    ("家", 14.76, 14.92), ("很好", 14.92, 15.26), ("吃", 15.26, 15.54), ("的", 15.54, 15.72), ("饭。", 15.72, 15.94),
    ("太", 15.94, 16.00), ("好了。", 16.00, 16.08),
]  # fmt: skip
MULTI_SEGMENTS = [
    AsrSegment(
        0.14,
        16.08,
        "你好,你好,好久不见,最近怎么样?还不错,你呢?好的。好的。我们明天一起吃吃饭吧,我知道一家很好吃的饭。太好了。",
        tuple(AsrWord(start, end, text) for text, start, end in _MULTI_WORDS),
    )
]


def test_many_lines_merged_into_one_long_segment_are_told_apart_by_word_times():
    starts = align_lines_to_segments(MULTI_LINES, MULTI_SEGMENTS)

    assert None not in starts
    for detected, truth in zip(starts, MULTI_TRUTH, strict=True):
        assert detected == pytest.approx(truth, abs=0.5)


def test_the_two_identical_lines_each_get_their_own_time():
    starts = align_lines_to_segments(MULTI_LINES, MULTI_SEGMENTS)

    assert starts[3] is not None
    assert starts[4] is not None
    assert starts[4] - starts[3] == pytest.approx(1.6, abs=0.4)


def test_without_word_times_a_merged_segment_is_only_spread_evenly():
    # 回归：只有分段起止时，一个长分段里的句间停顿看不出来，误差可达数秒——这正是需要词级时间的原因
    flat = [AsrSegment(s.start, s.end, s.text) for s in MULTI_SEGMENTS]

    starts = align_lines_to_segments(MULTI_LINES, flat)

    assert max(abs(d - t) for d, t in zip(starts, MULTI_TRUTH, strict=True) if d is not None) > 1.5


def test_a_word_end_that_swallows_a_pause_does_not_stretch_its_characters():
    # 「你好,」的结束时间一直拖到 2.56 秒：它两个字只能各占不超过单字时长上限
    segments = [AsrSegment(0.0, 5.0, "你好今晚", (AsrWord(0.14, 2.56, "你好"), AsrWord(2.56, 3.0, "今晚")))]

    first, second = align_lines_to_segments(["你好", "今晚"], segments)

    assert first == pytest.approx(0.14, abs=0.05)
    assert second == pytest.approx(2.56, abs=0.05)
