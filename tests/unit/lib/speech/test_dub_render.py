from __future__ import annotations

from pathlib import Path

import pytest

from lib.speech.dub_render import DubClip, build_dub_mix_args


def _filter(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


def test_original_volume_applied_and_clips_delayed():
    args = build_dub_mix_args(
        Path("v.mp4"),
        [DubClip(Path("a.wav"), 0.0), DubClip(Path("b.wav"), 2.5)],
        video_has_audio=True,
    )
    graph = _filter(args)
    # 缺省把原声压到 30%，配音不加任何音量滤镜
    assert "[0:a]volume=0.3[orig]" in graph
    assert "[1:a]volume" not in graph
    assert "[2:a]volume" not in graph
    assert "[1:a]adelay=0|0[dub1]" in graph
    assert "[2:a]adelay=2500|2500[dub2]" in graph
    assert "amix=inputs=3" in graph
    assert "normalize=0" in graph
    # 原声叠上满音量的配音可能超过满刻度，混音后过一道限幅器
    assert "[mix]alimiter=limit=0.97[aout]" in graph
    assert args[args.index("-c:v") + 1] == "copy"


def test_silent_video_mixes_only_dub_tracks():
    args = build_dub_mix_args(Path("v.mp4"), [DubClip(Path("a.wav"), 1.0)], video_has_audio=False)
    graph = _filter(args)
    assert "[0:a]" not in graph
    assert "amix=inputs=1" in graph


def test_rejects_empty_clips_and_bad_volume():
    with pytest.raises(ValueError, match="至少需要一段配音"):
        build_dub_mix_args(Path("v.mp4"), [], video_has_audio=True)
    with pytest.raises(ValueError, match="original_volume"):
        build_dub_mix_args(Path("v.mp4"), [DubClip(Path("a.wav"), 0)], original_volume=1.5, video_has_audio=True)


def test_original_volume_can_still_be_overridden():
    args = build_dub_mix_args(Path("v.mp4"), [DubClip(Path("a.wav"), 0)], original_volume=0.4, video_has_audio=True)

    assert "[0:a]volume=0.4[orig]" in _filter(args)
