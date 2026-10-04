"""越南语配音台词在 unit 内的时间排布。

开始时间的优先级：人工覆盖 > 从成片检测到的时间（不早于前一条配音的结束）> 按顺序首尾相接（相邻之间
留一个固定间隙）。每行时长取已合成音频的真实时长，尚未合成时按 ``vi`` 语速估算；任何一行之后的行都
从该行结束处继续。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lib.speech.speech_rate import estimate_spoken_seconds

#: 相邻两行配音之间的固定间隙（秒）。
DUB_LINE_GAP_SECONDS = 0.2

DUB_LANGUAGE = "vi"


StartSource = Literal["manual", "detected", "estimated"]


@dataclass(frozen=True, slots=True)
class DubTimingInput:
    """排布一行配音所需的最小输入。"""

    text: str
    #: 人工覆盖的开始时间（相对 unit 起点，秒）；None 即顺序排布。
    start_override: float | None = None
    #: 已合成音频的真实时长（秒）；None 即按语速估算。
    audio_duration: float | None = None
    #: 从成片里检测到的原台词开始时间（秒）；无人工覆盖时优先于顺序排布。
    detected_start: float | None = None


@dataclass(frozen=True, slots=True)
class DubSlot:
    """一行配音在 unit 内的排布结果。"""

    index: int
    start_seconds: float
    duration_seconds: float
    #: 时长是估算值而非已合成音频的真实时长。
    estimated: bool
    #: 开始时间的来源：人工覆盖 / 从成片检测 / 顺序排布估算。
    source: StartSource = "estimated"
    #: 被推迟之前想要的开始时间（成片里检测到的时间）；只有检测来源才有。
    requested_start: float | None = None

    @property
    def delayed_seconds(self) -> float:
        """因为前一条配音还没念完，比成片里的实际开始时间晚了多少秒；没有被推迟为 0。"""
        if self.requested_start is None:
            return 0.0
        return max(0.0, self.start_seconds - self.requested_start)

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


def schedule_dub_lines(lines: Sequence[DubTimingInput], *, speech_rate_override: float | None = None) -> list[DubSlot]:
    """顺序排布配音台词，返回与 ``lines`` 等长同序的 {@link DubSlot}。"""

    slots: list[DubSlot] = []
    cursor = 0.0
    for index, line in enumerate(lines):
        estimated = line.audio_duration is None or line.audio_duration <= 0
        duration = (
            estimate_spoken_seconds(line.text, DUB_LANGUAGE, speech_rate_override)
            if estimated
            else float(line.audio_duration or 0.0)
        )
        requested: float | None = None
        if line.start_override is not None:
            start, source = line.start_override, "manual"
        elif line.detected_start is not None:
            # 检测到的时间不让相邻两条配音重叠：前一条还没念完就顺延到它结束之后
            requested = line.detected_start
            start, source = max(requested, cursor), "detected"
        else:
            start, source = cursor, "estimated"
        slots.append(
            DubSlot(
                index=index,
                start_seconds=start,
                duration_seconds=duration,
                estimated=estimated,
                source=source,
                requested_start=requested,
            )
        )
        cursor = start + duration + DUB_LINE_GAP_SECONDS
    return slots


def dub_overflow_seconds(slots: Sequence[DubSlot], unit_duration_seconds: float) -> float:
    """配音末行结束时刻超出 unit 时长的秒数；未超出为 0。"""

    if not slots:
        return 0.0
    return max(0.0, max(slot.end_seconds for slot in slots) - unit_duration_seconds)


__all__ = [
    "DUB_LANGUAGE",
    "DUB_LINE_GAP_SECONDS",
    "DubSlot",
    "DubTimingInput",
    "StartSource",
    "dub_overflow_seconds",
    "schedule_dub_lines",
]
