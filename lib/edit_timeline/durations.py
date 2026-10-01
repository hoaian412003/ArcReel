"""视频单元的实测时长：剪辑以实际媒体时长为准，剧本里的 ``duration_seconds`` 只是估计。"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from pathlib import Path

from lib.edit_timeline.catalog import UnitInfo
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.render import Prober, ffmpeg_tools_available, probe_media

# (路径, mtime_ns, 大小) → 实测时长；文件被替换后键自然失效。
_cache: dict[tuple[str, int, int], float] = {}


async def _measure(path: Path, probe: Prober) -> float | None:
    try:
        stat = await asyncio.to_thread(path.stat)
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _cache:
        try:
            info = await probe(path)
        except (EditTimelineError, OSError, ValueError):
            return None
        if info.duration_s <= 0:
            return None
        _cache[key] = info.duration_s
    return _cache[key]


async def with_measured_durations(
    catalog: Sequence[UnitInfo], project_dir: Path, *, probe: Prober = probe_media
) -> list[UnitInfo]:
    """用 ffprobe 实测时长替换目录里的估计时长；探测不可用或失败的单元保留估计值。"""
    if probe is probe_media and not ffmpeg_tools_available():
        return list(catalog)

    async def one(unit: UnitInfo) -> UnitInfo:
        if not unit.video_path:
            return unit
        measured = await _measure(Path(project_dir) / unit.video_path, probe)
        return dataclasses.replace(unit, duration_s=round(measured, 3)) if measured else unit

    return list(await asyncio.gather(*(one(u) for u in catalog)))
