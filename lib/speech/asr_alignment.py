"""把语音识别得到的逐句时间对回剧本台词：求每条台词在视频里的实际开始时间。

识别文本和剧本不会逐字相同（同音字、繁简、漏字），分段边界也和台词边界不对应，所以不按段对句，而是
把剧本台词顺序拼成一串字符、识别结果拼成另一串，按字符相似度做有序对齐；每个识别字符的时间由所在
分段的起止时间按字位线性插出，台词的开始时间取它最早对上的那个字符的时间（再按该字在台词内的位置回推）。
对不上的台词返回 ``None``，由调用方回落到估算。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

#: 一条台词至少要对上这么多字（且占其字数的比例不低于 ``MIN_MATCH_RATIO``）才算检测到。
MIN_MATCHED_CHARS = 2
MIN_MATCH_RATIO = 0.3

_KEEP = re.compile(r"[\w]", re.UNICODE)


@dataclass(frozen=True, slots=True)
class AsrWord:
    """识别出的一个词（中文多为单字）及其起止时间（秒）。"""

    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class AsrSegment:
    """识别出的一段话及其在媒体里的起止时间（秒）。

    多句对白常被合并成一个长分段，分段内的停顿只有 ``words`` 的词级时间才看得出来；
    没有词级时间时退回按分段起止线性插值。
    """

    start: float
    end: float
    text: str
    words: tuple[AsrWord, ...] = ()


def _normalize(text: str) -> str:
    """去掉标点与空白、统一全半角与大小写，只留文字本身用于比对。"""
    folded = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in folded if _KEEP.match(ch) and ch != "_")


#: 单字时长上限：词的结束时间常把后面的停顿一并吞进去，按它均分会把词内各字摊得过开。
MAX_CHAR_SECONDS = 0.4


def _spread(text: str, start: float, end: float, *, cap: float | None) -> tuple[list[str], list[float], list[float]]:
    span = max(end - start, 0.0)
    step = span / len(text)
    if cap is not None:
        step = min(step, cap)
    return list(text), [start + step * i for i in range(len(text))], [step] * len(text)


def _asr_char_times(segments: Sequence[AsrSegment]) -> tuple[str, list[float], list[float]]:
    """识别结果拼成字符串，并给每个字符配开始时间与单字时长。

    有词级时间的分段按词分摊到字；没有的按分段起止线性插值。
    """
    chars: list[str] = []
    starts: list[float] = []
    per_char: list[float] = []
    for segment in segments:
        pieces = (
            [(_normalize(word.text), word.start, word.end, MAX_CHAR_SECONDS) for word in segment.words]
            if segment.words
            else [(_normalize(segment.text), segment.start, segment.end, None)]
        )
        for text, start, end, cap in pieces:
            if not text:
                continue
            piece_chars, piece_starts, piece_steps = _spread(text, start, end, cap=cap)
            chars += piece_chars
            starts += piece_starts
            per_char += piece_steps
    return "".join(chars), starts, per_char


def align_lines_to_segments(lines: Sequence[str], segments: Sequence[AsrSegment]) -> list[float | None]:
    """逐条台词的检测开始时间（秒）；与 ``lines`` 等长同序，对不上的为 ``None``。"""
    normalized = [_normalize(line) for line in lines]
    asr_text, asr_starts, asr_step = _asr_char_times(segments)
    result: list[float | None] = [None] * len(lines)
    script_text = "".join(normalized)
    if not script_text or not asr_text:
        return result

    # 台词在拼接串里的 [起, 止) 区间
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for line in normalized:
        bounds.append((cursor, cursor + len(line)))
        cursor += len(line)

    # 每个剧本字符 → 对上的识别字符下标（对不上为 -1）
    mapping = [-1] * len(script_text)
    matcher = SequenceMatcher(None, script_text, asr_text, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapping[block.a + offset] = block.b + offset

    for index, (begin, end) in enumerate(bounds):
        length = end - begin
        if length == 0:
            continue
        matched = [(pos, mapping[pos]) for pos in range(begin, end) if mapping[pos] >= 0]
        if len(matched) < MIN_MATCHED_CHARS or len(matched) / length < MIN_MATCH_RATIO:
            continue
        first_pos, first_asr = matched[0]
        # 最早对上的字不一定是台词第一个字：按单字时长把开头没识别出的字数回推
        start = asr_starts[first_asr] - (first_pos - begin) * asr_step[first_asr]
        result[index] = max(0.0, round(start, 3))

    # 开始时间必须随台词顺序不减；违反顺序的当作误配丢弃
    last = 0.0
    for index, value in enumerate(result):
        if value is None:
            continue
        if value < last:
            result[index] = None
        else:
            last = value
    return result


__all__ = ["MIN_MATCHED_CHARS", "MIN_MATCH_RATIO", "AsrSegment", "align_lines_to_segments"]
