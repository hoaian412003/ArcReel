"""视频单元目录：把剧本条目解析为剪辑可用的单元事实，并对时间线做读取诊断。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lib.edit_timeline.models import Clip, Timeline
from lib.edit_timeline.store import new_clip_id
from lib.script.script_editor import resolve_items


@dataclass(frozen=True)
class UnitInfo:
    """剪辑可用的视频单元：current 视频路径、版本与时长。"""

    unit_id: str
    video_path: str | None
    version: int
    duration_s: float | None


@dataclass(frozen=True)
class Diagnostics:
    """时间线读取诊断（ADR 0090）。"""

    deleted_unit_clips: list[str] = field(default_factory=list)
    unused_unit_ids: list[str] = field(default_factory=list)
    stale_trim_clips: list[str] = field(default_factory=list)
    missing_video_clips: list[str] = field(default_factory=list)


def build_catalog(script: dict[str, Any], version_of: Mapping[str, int] | None = None) -> list[UnitInfo]:
    """按剧本顺序列出视频单元；``version_of`` 给出各单元 current 视频版本号（缺省 0）。"""
    items, id_field, _kind = resolve_items(script)
    versions = version_of or {}
    units: list[UnitInfo] = []
    for item in items:
        if not isinstance(item, dict) or item.get(id_field) is None:
            continue
        unit_id = str(item[id_field])
        assets = item.get("generated_assets") or {}
        duration = item.get("duration_seconds")
        units.append(
            UnitInfo(
                unit_id=unit_id,
                video_path=assets.get("video_clip") or None,
                version=int(versions.get(unit_id, 0)),
                duration_s=float(duration) if isinstance(duration, (int, float)) else None,
            )
        )
    return units


def mechanical_clips(catalog: Sequence[UnitInfo]) -> list[Clip]:
    """按剧本顺序、整段使用、全部硬切的机械时间线；跳过尚无视频的单元。"""
    return [Clip(clip_id=new_clip_id(), unit_id=u.unit_id) for u in catalog if u.video_path]


def diagnose(timeline: Timeline, catalog: Sequence[UnitInfo]) -> Diagnostics:
    by_id = {u.unit_id: u for u in catalog}
    clips = timeline.latest.clips
    used = {c.unit_id for c in clips}
    deleted = [c.clip_id for c in clips if c.unit_id not in by_id]
    stale = [
        c.clip_id
        for c in clips
        if c.unit_id in by_id
        and c.trimmed_against_version is not None
        and c.trimmed_against_version != by_id[c.unit_id].version
    ]
    missing = [c.clip_id for c in clips if c.unit_id in by_id and not by_id[c.unit_id].video_path]
    unused = [u.unit_id for u in catalog if u.unit_id not in used]
    return Diagnostics(deleted, unused, stale, missing)
