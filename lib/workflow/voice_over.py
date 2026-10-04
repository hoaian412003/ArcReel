"""可选配音步骤（voice_over）的事实：哪些已有成片的视频还缺完整配音。

只读剧本，不依赖任务队列与服务端；完成口径与配音时间线的「音频仍对应现行台词」同一定义。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lib.project.project_manager import is_reference_video_project
from lib.script.reference_video.dub_lines import align_dub_lines, dub_audio_is_current
from lib.script.script_models import DubLine, get_generated_assets
from lib.speech.dub_settings import project_dub_target_language


def voice_over_applies(project: Mapping[str, Any]) -> bool:
    """项目是否带可选配音步骤：参考生视频且启用了越南语配音。"""
    return is_reference_video_project(project) and project_dub_target_language(project) == "vi"


def _unit_has_video(unit: Mapping[str, Any]) -> bool:
    return bool(get_generated_assets(dict(unit)).get("video_clip"))


def unit_voice_over_complete(unit: Mapping[str, Any]) -> bool:
    """每条现行发声都有与正文相符的配音行，且各行都有对应现行台词的音频。"""
    lines: list[DubLine] = []
    for raw in unit.get("dub_lines") or []:
        try:
            lines.append(DubLine.model_validate(raw))
        except ValueError:
            return False
    alignments = [item for item in align_dub_lines(str(unit.get("text") or ""), lines) if item.status != "orphan"]
    return all(
        item.status == "current" and item.line is not None and dub_audio_is_current(item.line) for item in alignments
    )


@dataclass(frozen=True, slots=True)
class VoiceOverFacts:
    """配音步骤的进度快照；``measured=False`` 表示剧本尚不可读，只知道步骤存在。"""

    measured: bool = False
    complete_ids: tuple[str, ...] = ()
    missing_ids: tuple[str, ...] = ()
    #: 项目里是否还有排队 / 运行中的配音任务。
    active: bool = False

    @property
    def complete(self) -> bool:
        return self.measured and not self.missing_ids

    @classmethod
    def from_script(cls, script: Mapping[str, Any], *, active: bool = False) -> VoiceOverFacts:
        complete: list[str] = []
        missing: list[str] = []
        for unit in script.get("video_units") or []:
            if not isinstance(unit, Mapping) or not _unit_has_video(unit):
                continue
            unit_id = str(unit.get("unit_id"))
            (complete if unit_voice_over_complete(unit) else missing).append(unit_id)
        return cls(measured=True, complete_ids=tuple(complete), missing_ids=tuple(missing), active=active)


__all__ = ["VoiceOverFacts", "unit_voice_over_complete", "voice_over_applies"]
