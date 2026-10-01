"""把剪辑时间线渲染为成片：解析片段 → 构造 ffmpeg 滤镜图 → 执行。

画面一律取视频单元的 current 视频；入出点针对的版本不是 current 时忽略裁切、整段使用；
引用已删除或尚无视频的单元时跳过该片段。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.edit_timeline.catalog import UnitInfo
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.models import TRANSITION_SECONDS, Timeline
from lib.infra.subprocess_deadline import run_with_deadline

#: 转场词表 → ffmpeg xfade 效果。
XFADE_EFFECTS: Mapping[str, str] = {"fade": "fade", "dissolve": "dissolve", "wipe": "wipeleft"}

_PROBE_DEADLINE_SECONDS = 30.0
_RENDER_DEADLINE_SECONDS = 1800.0
_SAMPLE_RATE = 44100
_FPS = 24


@dataclass(frozen=True)
class MediaProbe:
    duration_s: float
    width: int
    height: int
    has_audio: bool


@dataclass(frozen=True)
class ClipPlan:
    """一个即将渲染的片段：源文件、实际使用的区间与它后面的转场。"""

    path: Path
    start_s: float
    end_s: float
    has_audio: bool
    transition_to_next: str = "cut"

    @property
    def length_s(self) -> float:
        return self.end_s - self.start_s


Prober = Callable[[Path], Awaitable[MediaProbe]]


def ffmpeg_tools_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def probe_media(path: Path) -> MediaProbe:
    result = await run_with_deadline(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        deadline_seconds=_PROBE_DEADLINE_SECONDS,
        capture_stdout=True,
    )
    if result.returncode != 0:
        raise EditTimelineError(f"cannot probe {path.name}", key="edit_timeline_probe_failed", file=path.name)
    data = json.loads(result.stdout or b"{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise EditTimelineError(f"no video stream in {path.name}", key="edit_timeline_probe_failed", file=path.name)
    return MediaProbe(
        duration_s=float(data.get("format", {}).get("duration") or 0),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


async def resolve_plans(
    timeline: Timeline,
    catalog: Sequence[UnitInfo],
    project_dir: Path,
    *,
    probe: Prober = probe_media,
) -> tuple[list[ClipPlan], MediaProbe | None]:
    """把最新修订解析为可渲染的片段计划；返回计划与第一个片段的探测结果（用作输出画幅）。"""
    by_id = {unit.unit_id: unit for unit in catalog}
    plans: list[ClipPlan] = []
    first: MediaProbe | None = None
    for clip in timeline.latest.clips:
        unit = by_id.get(clip.unit_id)
        if unit is None or not unit.video_path:
            continue
        path = Path(project_dir) / unit.video_path
        if not path.is_file():
            continue
        info = await probe(path)
        if info.duration_s <= 0:
            continue
        first = first or info
        trim_valid = clip.trimmed_against_version is None or clip.trimmed_against_version == unit.version
        start = clip.in_s if trim_valid else 0.0
        end = clip.out_s if trim_valid and clip.out_s is not None else info.duration_s
        end = min(end, info.duration_s)
        if end - start <= 0.05:
            start, end = 0.0, info.duration_s
        plans.append(ClipPlan(path, start, end, info.has_audio, clip.transition_to_next))
    return plans, first


def build_filter_graph(plans: Sequence[ClipPlan], *, width: int, height: int) -> tuple[str, str, str]:
    """构造 ``-filter_complex`` 字符串，返回 ``(graph, video_label, audio_label)``。

    每个输入先归一化（裁切、缩放补边、统一帧率与采样率；无音轨补静音），再两两串接：
    硬切用 concat，其余转场用 xfade + acrossfade。
    """
    if not plans:
        raise EditTimelineError("timeline has no renderable clips", key="edit_timeline_empty")
    parts: list[str] = []
    for i, plan in enumerate(plans):
        parts.append(
            f"[{i}:v]trim=start={plan.start_s:.3f}:end={plan.end_s:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={_FPS},format=yuv420p[v{i}]"
        )
        if plan.has_audio:
            parts.append(
                f"[{i}:a]atrim=start={plan.start_s:.3f}:end={plan.end_s:.3f},asetpts=PTS-STARTPTS,"
                f"aresample={_SAMPLE_RATE},aformat=channel_layouts=stereo[a{i}]"
            )
        else:
            parts.append(
                f"anullsrc=r={_SAMPLE_RATE}:cl=stereo,atrim=duration={plan.length_s:.3f},asetpts=PTS-STARTPTS[a{i}]"
            )

    cur_v, cur_a, cur_len = "v0", "a0", plans[0].length_s
    for i in range(1, len(plans)):
        prev_transition = plans[i - 1].transition_to_next
        nxt = plans[i]
        out_v, out_a = f"cv{i}", f"ca{i}"
        effect = XFADE_EFFECTS.get(prev_transition)
        if effect is None:
            parts.append(f"[{cur_v}][{cur_a}][v{i}][a{i}]concat=n=2:v=1:a=1[{out_v}][{out_a}]")
            cur_len += nxt.length_s
        else:
            dur = min(TRANSITION_SECONDS, cur_len / 2, nxt.length_s / 2)
            parts.append(
                f"[{cur_v}][v{i}]xfade=transition={effect}:duration={dur:.3f}:offset={cur_len - dur:.3f}[{out_v}]"
            )
            parts.append(f"[{cur_a}][a{i}]acrossfade=d={dur:.3f}[{out_a}]")
            cur_len += nxt.length_s - dur
        cur_v, cur_a = out_v, out_a
    return ";".join(parts), cur_v, cur_a


def build_ffmpeg_args(plans: Sequence[ClipPlan], output: Path, *, width: int, height: int) -> list[str]:
    graph, v_label, a_label = build_filter_graph(plans, width=width, height=height)
    args = ["ffmpeg", "-y", "-v", "error"]
    for plan in plans:
        args += ["-i", str(plan.path)]
    args += [
        "-filter_complex",
        graph,
        "-map",
        f"[{v_label}]",
        "-map",
        f"[{a_label}]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return args


async def render_timeline(
    timeline: Timeline,
    catalog: Sequence[UnitInfo],
    project_dir: Path,
    output: Path,
    *,
    probe: Prober = probe_media,
) -> Path:
    """渲染时间线最新修订到 ``output``（先写临时文件再原子替换）。"""
    if not ffmpeg_tools_available():
        raise EditTimelineError("ffmpeg/ffprobe not available", key="edit_timeline_ffmpeg_missing")
    plans, first = await resolve_plans(timeline, catalog, project_dir, probe=probe)
    if not plans or first is None:
        raise EditTimelineError("timeline has no renderable clips", key="edit_timeline_empty")
    # libx264/yuv420p 要求偶数边长。
    width, height = first.width // 2 * 2, first.height // 2 * 2
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.stem}.tmp{output.suffix}")
    result = await run_with_deadline(
        build_ffmpeg_args(plans, tmp, width=width, height=height),
        deadline_seconds=_RENDER_DEADLINE_SECONDS,
        cleanup_paths=[tmp],
    )
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise EditTimelineError("ffmpeg render failed", key="edit_timeline_render_failed")
    tmp.replace(output)
    return output
