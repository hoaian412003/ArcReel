from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lib.edit_timeline import store
from lib.edit_timeline.catalog import UnitInfo
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.models import Clip
from lib.edit_timeline.render import (
    ClipPlan,
    MediaProbe,
    build_filter_graph,
    render_timeline,
    resolve_plans,
)


def _plan(start=0.0, end=4.0, audio=True, transition="cut") -> ClipPlan:
    return ClipPlan(Path("x.mp4"), start, end, audio, transition)


def test_filter_graph_uses_concat_for_cuts_and_xfade_for_transitions():
    graph, v, a = build_filter_graph(
        [_plan(transition="dissolve"), _plan(transition="cut"), _plan()], width=640, height=360
    )
    assert "xfade=transition=dissolve:duration=0.500:offset=3.500" in graph
    assert "acrossfade=d=0.500" in graph
    assert "concat=n=2:v=1:a=1" in graph
    assert (v, a) == ("cv2", "ca2")


def test_filter_graph_trims_and_synthesises_silence_without_audio_stream():
    graph, _, _ = build_filter_graph([_plan(1.0, 3.0, audio=False)], width=320, height=180)
    assert "trim=start=1.000:end=3.000" in graph
    assert "anullsrc" in graph


def test_filter_graph_rejects_empty():
    with pytest.raises(EditTimelineError):
        build_filter_graph([], width=320, height=180)


def _timeline(clips: list[Clip]):
    return store.Timeline(
        timeline_id="t",
        episode=1,
        name="A",
        revisions=[store.Revision(revision=1, author="me", summary="s", created_at="x", clips=clips)],
    )


async def test_resolve_plans_ignores_stale_trim_and_skips_missing(tmp_path):
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos/U1.mp4").write_bytes(b"x")
    catalog = [UnitInfo("U1", "videos/U1.mp4", 2, 8.0), UnitInfo("U2", None, 0, None)]
    timeline = _timeline(
        [
            Clip(clip_id="a", unit_id="U1", in_s=1, out_s=3, trimmed_against_version=1),
            Clip(clip_id="b", unit_id="U1", in_s=1, out_s=3, trimmed_against_version=2),
            Clip(clip_id="c", unit_id="U2"),
            Clip(clip_id="d", unit_id="GONE"),
        ]
    )

    async def probe(_path):
        return MediaProbe(8.0, 640, 360, True)

    plans, first = await resolve_plans(timeline, catalog, tmp_path, probe=probe)
    assert [(p.start_s, p.end_s) for p in plans] == [(0.0, 8.0), (1.0, 3.0)]
    assert first is not None


def _make_fixture_videos(tmp_path):
    (tmp_path / "videos").mkdir()
    for name, color in (("A", "red"), ("B", "blue")):
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=160x90:d=2:r=24",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(tmp_path / f"videos/{name}.mp4"),
            ],
            check=True,
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg required")
async def test_render_end_to_end(tmp_path):
    _make_fixture_videos(tmp_path)
    catalog = [UnitInfo("A", "videos/A.mp4", 1, 2.0), UnitInfo("B", "videos/B.mp4", 1, 2.0)]
    timeline = _timeline([Clip(clip_id="a", unit_id="A", transition_to_next="fade"), Clip(clip_id="b", unit_id="B")])
    out = await render_timeline(timeline, catalog, tmp_path, tmp_path / "output/final.mp4")
    assert out.is_file()
    assert out.stat().st_size > 0


async def test_measured_durations_replace_script_estimates(tmp_path):
    from lib.edit_timeline.durations import with_measured_durations

    (tmp_path / "videos").mkdir()
    (tmp_path / "videos/U1.mp4").write_bytes(b"x")
    catalog = [
        UnitInfo("U1", "videos/U1.mp4", 1, 5.0),
        UnitInfo("U2", None, 0, 4.0),
        UnitInfo("U3", "videos/gone.mp4", 1, 6.0),
    ]

    async def probe(_path):
        return MediaProbe(7.25, 640, 360, True)

    measured = await with_measured_durations(catalog, tmp_path, probe=probe)
    assert [u.duration_s for u in measured] == [7.25, 4.0, 6.0]
