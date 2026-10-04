from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.speech.dub_render import DubClip, render_dubbed_video

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="需要 ffmpeg / ffprobe"
)


def _run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True)


def _streams(path: Path) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        check=True,
        capture_output=True,
    ).stdout
    return [s["codec_type"] for s in json.loads(out)["streams"]]


@pytest.mark.asyncio
async def test_mixes_dub_over_video_with_original_audio(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _run(
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=3", "-f", "lavfi",
        "-i", "sine=frequency=300:duration=3", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(video),
    )  # fmt: skip
    dub = tmp_path / "dub.wav"
    _run("ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=800:duration=1", str(dub))

    out = await render_dubbed_video(video, [DubClip(dub, 1.0)], tmp_path / "out" / "dubbed.mp4", video_has_audio=True)

    assert out.is_file()
    assert sorted(_streams(out)) == ["audio", "video"]


@pytest.mark.asyncio
async def test_silent_video_gets_dub_audio_track(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _run("ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-c:v", "libx264", str(video))
    dub = tmp_path / "dub.wav"
    _run("ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=800:duration=1", str(dub))

    out = await render_dubbed_video(video, [DubClip(dub, 0.0)], tmp_path / "dubbed.mp4", video_has_audio=False)

    assert sorted(_streams(out)) == ["audio", "video"]
