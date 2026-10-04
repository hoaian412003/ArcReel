"""真实 ffmpeg：配音混入时间线成片后，落在转场之后的真实时刻，原声被压低。"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.edit_timeline.catalog import UnitInfo
from lib.edit_timeline.models import Clip, Revision, Timeline
from lib.edit_timeline.render import DubSource, render_timeline

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="需要 ffmpeg / ffprobe"
)

ORIGINAL_HZ = 220
DUB_HZ = 800


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def _make_clip(path: Path, seconds: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s=160x90:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={ORIGINAL_HZ}:duration={seconds}:sample_rate=44100",
        "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path),
    )  # fmt: skip


def _make_dub(path: Path, seconds: float) -> None:
    _run(
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={DUB_HZ}:duration={seconds}:sample_rate=48000", str(path),
    )  # fmt: skip


def _level(video: Path, start: float, length: float, *, band: str) -> float:
    """窗口内某个频段的最大音量（dB）；``high`` 只留配音的 800 Hz，``low`` 只留原声的 220 Hz。"""
    cut = "highpass=f=600" if band == "high" else "lowpass=f=400"
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(length), "-i", str(video),
         "-vn", "-af", f"{cut},volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    match = re.search(r"max_volume: (-?[0-9.]+|-inf) dB", result.stderr)
    assert match, result.stderr
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))


def _timeline(*clips: Clip) -> Timeline:
    return Timeline(
        timeline_id="t",
        episode=1,
        name="A",
        revisions=[Revision(revision=1, author="me", summary="s", created_at="x", clips=list(clips))],
    )


async def test_the_dub_lands_where_the_transition_puts_its_clip_and_the_original_is_lowered_to_thirty_percent(
    tmp_path: Path,
):
    _make_clip(tmp_path / "videos/A.mp4", 6)
    _make_clip(tmp_path / "videos/B.mp4", 6)
    dub = tmp_path / "audio/dub.wav"
    dub.parent.mkdir()
    _make_dub(dub, 1.0)
    catalog = [UnitInfo("A", "videos/A.mp4", 1, 6.0), UnitInfo("B", "videos/B.mp4", 1, 6.0)]
    timeline = _timeline(Clip(clip_id="a", unit_id="A", transition_to_next="fade"), Clip(clip_id="b", unit_id="B"))

    plain = await render_timeline(timeline, catalog, tmp_path, tmp_path / "out/plain.mp4")
    dubbed = await render_timeline(
        timeline, catalog, tmp_path, tmp_path / "out/dubbed.mp4", dub_sources={"B": [DubSource(dub, 2.0, 1.0)]}
    )

    # 第二个片段在成片 5.5 秒开始（淡入转场重叠 0.5 秒），单元内 2.0 秒的配音落在 7.5–8.5 秒。
    # 高通滤波挡不干净 220 Hz 原声，所以拿「同一条成片里没有配音的窗口」当底噪来比。
    in_window = _level(dubbed, 7.5, 1.0, band="high")
    before = _level(dubbed, 6.0, 1.2, band="high")
    after = _level(dubbed, 9.0, 2.0, band="high")
    assert in_window > before + 8
    assert in_window > after + 8
    # 没有配音的成片里同一个窗口不比底噪响
    assert _level(plain, 7.5, 1.0, band="high") < in_window - 8
    # 原声压到 30%（约 -10.5 dB）：取不受转场影响的窗口比较 220 Hz 成分
    drop = _level(plain, 1.0, 3.0, band="low") - _level(dubbed, 1.0, 3.0, band="low")
    assert 9.0 < drop < 12.0


async def test_a_dub_cut_by_the_trim_is_not_heard_outside_the_clip(tmp_path: Path):
    _make_clip(tmp_path / "videos/A.mp4", 6)
    dub = tmp_path / "audio/dub.wav"
    dub.parent.mkdir()
    _make_dub(dub, 2.0)
    catalog = [UnitInfo("A", "videos/A.mp4", 1, 6.0)]
    # 只用单元的 0–3 秒；配音在单元 2.0–4.0 秒，只有前 1 秒在片段里
    timeline = _timeline(Clip(clip_id="a", unit_id="A", in_s=0.0, out_s=3.0, trimmed_against_version=1))

    out = await render_timeline(
        timeline, catalog, tmp_path, tmp_path / "out/trimmed.mp4", dub_sources={"A": [DubSource(dub, 2.0, 2.0)]}
    )

    assert _level(out, 2.0, 0.9, band="high") > _level(out, 0.2, 1.0, band="high") + 8
    duration = float(
        _run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)).stdout
    )
    assert duration == pytest.approx(3.0, abs=0.2)


async def test_with_no_dub_sources_the_original_audio_is_left_alone(tmp_path: Path):
    _make_clip(tmp_path / "videos/A.mp4", 4)
    catalog = [UnitInfo("A", "videos/A.mp4", 1, 4.0)]
    timeline = _timeline(Clip(clip_id="a", unit_id="A"))

    out = await render_timeline(timeline, catalog, tmp_path, tmp_path / "out/plain.mp4", dub_sources={})
    reference = await render_timeline(timeline, catalog, tmp_path, tmp_path / "out/ref.mp4")

    assert abs(_level(out, 0.5, 2.0, band="low") - _level(reference, 0.5, 2.0, band="low")) < 0.5
