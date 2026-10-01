"""把越南语配音混入原视频：原声压到指定音量，配音按开始时间叠加，输出新的 mp4。

视频流直接拷贝不重编码；音频统一重编码为 AAC。原视频没有音轨时只有配音一路。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from lib.infra.subprocess_deadline import Spawner, run_with_deadline

logger = logging.getLogger(__name__)

#: 原视频音量缺省为一半，让配音盖过原声又保留环境氛围。
DEFAULT_ORIGINAL_VOLUME = 0.5

_RENDER_DEADLINE_SECONDS = 300.0
_RENDER_GRACE_SECONDS = 5.0
_PROBE_DEADLINE_SECONDS = 15.0


class DubRenderError(RuntimeError):
    """配音混流失败。"""


@dataclass(frozen=True, slots=True)
class DubClip:
    """一段待混入的配音音频。"""

    path: Path
    start_seconds: float


def build_dub_mix_args(
    video_path: Path,
    clips: Sequence[DubClip],
    *,
    original_volume: float = DEFAULT_ORIGINAL_VOLUME,
    video_has_audio: bool,
) -> list[str]:
    """构造 ffmpeg 参数（不含输出路径）。至少需要一段配音。"""

    if not clips:
        raise ValueError("至少需要一段配音音频")
    if not 0.0 <= original_volume <= 1.0:
        raise ValueError("original_volume 必须在 0 到 1 之间")

    args = ["ffmpeg", "-nostdin", "-y", "-i", str(video_path)]
    for clip in clips:
        args += ["-i", str(clip.path)]

    filters: list[str] = []
    labels: list[str] = []
    if video_has_audio:
        filters.append(f"[0:a]volume={original_volume:g}[orig]")
        labels.append("[orig]")
    for position, clip in enumerate(clips, start=1):
        delay_ms = max(0, round(clip.start_seconds * 1000))
        label = f"[dub{position}]"
        filters.append(f"[{position}:a]adelay={delay_ms}|{delay_ms}{label}")
        labels.append(label)
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[aout]")

    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
    ]
    return args


async def probe_has_audio_stream(video_path: Path, *, spawn: Spawner | None = None) -> bool:
    """原视频是否带音轨；ffprobe 不可用或失败时按「没有音轨」处理，混流只放配音。"""

    try:
        result = await run_with_deadline(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            deadline_seconds=_PROBE_DEADLINE_SECONDS,
            capture_stdout=True,
            spawn=spawn,
        )
    except (OSError, TimeoutError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


async def render_dubbed_video(
    video_path: Path,
    clips: Sequence[DubClip],
    output_path: Path,
    *,
    original_volume: float = DEFAULT_ORIGINAL_VOLUME,
    video_has_audio: bool,
    spawn: Spawner | None = None,
) -> Path:
    """混流并原子写入 ``output_path``；失败不动已有产物。"""

    args = build_dub_mix_args(video_path, clips, original_volume=original_volume, video_has_audio=video_has_audio)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.stem}.{uuid4().hex}.tmp{output_path.suffix}")
    result = await run_with_deadline(
        [*args, str(temp_path)],
        deadline_seconds=_RENDER_DEADLINE_SECONDS,
        grace=_RENDER_GRACE_SECONDS,
        cleanup_paths=[temp_path],
        spawn=spawn,
    )
    try:
        if result.returncode != 0 or not temp_path.exists() or temp_path.stat().st_size < 1:
            raise DubRenderError(f"ffmpeg 混流失败 returncode={result.returncode}")
        temp_path.replace(output_path)
        return output_path
    finally:
        temp_path.unlink(missing_ok=True)


__all__ = [
    "DEFAULT_ORIGINAL_VOLUME",
    "DubClip",
    "DubRenderError",
    "build_dub_mix_args",
    "probe_has_audio_stream",
    "render_dubbed_video",
]
