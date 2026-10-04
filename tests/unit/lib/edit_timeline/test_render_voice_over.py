"""时间线成片混入越南语配音：配音按片段入出点裁剪、按转场后的真实起点定位，再与压低的原声混音。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.edit_timeline.catalog import UnitInfo
from lib.edit_timeline.models import Clip, Revision, Timeline
from lib.edit_timeline.render import (
    ClipPlan,
    DubPlacement,
    DubSource,
    MediaProbe,
    build_dub_mix,
    build_ffmpeg_args,
    build_filter_graph,
    clip_start_times,
    dub_placements,
    place_dub,
    resolve_plans,
)


def _wav(tmp_path: Path, name: str = "d.wav") -> Path:
    path = tmp_path / name
    path.write_bytes(b"RIFF")
    return path


def _plan(start=0.0, end=6.0, transition="cut", dub=()) -> ClipPlan:
    return ClipPlan(Path("x.mp4"), start, end, True, transition, tuple(dub))


class TestPlaceDub:
    def test_a_line_fully_inside_the_clip_is_kept_whole(self, tmp_path: Path):
        (placed,) = place_dub([DubSource(_wav(tmp_path), 2.0, 1.5)], 0.0, 6.0)

        assert (placed.offset_in_clip_s, placed.trim_head_s, placed.length_s) == (2.0, 0.0, 1.5)
        assert not placed.fade_in
        assert not placed.fade_out

    def test_positions_are_relative_to_the_trimmed_in_point(self, tmp_path: Path):
        # 片段从单元的 1.0 秒开始：单元里 3.0 秒的台词出现在片段第 2.0 秒
        (placed,) = place_dub([DubSource(_wav(tmp_path), 3.0, 1.0)], 1.0, 6.0)

        assert placed.offset_in_clip_s == 2.0

    def test_a_line_cut_by_the_in_point_keeps_only_its_tail_with_a_fade_in(self, tmp_path: Path):
        (placed,) = place_dub([DubSource(_wav(tmp_path), 0.5, 2.0)], 1.0, 6.0)

        assert (placed.offset_in_clip_s, placed.trim_head_s, placed.length_s) == (0.0, 0.5, 1.5)
        assert placed.fade_in
        assert not placed.fade_out

    def test_a_line_cut_by_the_out_point_keeps_only_its_head_with_a_fade_out(self, tmp_path: Path):
        (placed,) = place_dub([DubSource(_wav(tmp_path), 4.5, 2.0)], 0.0, 5.0)

        assert (placed.offset_in_clip_s, placed.trim_head_s, placed.length_s) == (4.5, 0.0, 0.5)
        assert placed.fade_out
        assert not placed.fade_in

    def test_lines_outside_the_clip_or_reduced_to_a_sliver_are_dropped(self, tmp_path: Path):
        wav = _wav(tmp_path)

        placed = place_dub([DubSource(wav, 7.0, 1.0), DubSource(wav, 0.0, 1.0), DubSource(wav, 4.98, 1.0)], 1.5, 5.0)

        assert placed == ()

    def test_a_missing_audio_file_is_skipped(self, tmp_path: Path):
        assert place_dub([DubSource(tmp_path / "gone.wav", 1.0, 1.0)], 0.0, 6.0) == ()


class TestClipStartTimes:
    def test_hard_cuts_simply_add_up(self):
        plans = [_plan(0, 4), _plan(0, 3), _plan(0, 5)]

        assert clip_start_times(plans) == [0.0, 4.0, 7.0]

    def test_a_transition_pulls_the_next_clip_forward_by_the_overlap(self):
        plans = [_plan(0, 6, "fade"), _plan(0, 6, "cut"), _plan(0, 4)]

        # 第一个转场重叠 0.5 秒；之后是硬切
        assert clip_start_times(plans) == [0.0, 5.5, 11.5]

    def test_a_short_clip_limits_the_overlap(self):
        # 重叠不超过任一侧片段长度的一半
        plans = [_plan(0, 0.6, "dissolve"), _plan(0, 6)]

        assert clip_start_times(plans) == [0.0, pytest.approx(0.6 - 0.3)]

    def test_starts_agree_with_the_offsets_the_filter_graph_really_uses(self):
        plans = [_plan(0, 6, "fade"), _plan(0, 5, "wipe"), _plan(0, 4)]

        graph, _, _ = build_filter_graph(plans, width=320, height=180)

        offsets = [float(m) for m in re.findall(r"xfade=[^;]*:offset=([0-9.]+)", graph)]
        durations = [float(m) for m in re.findall(r"xfade=[^;]*:duration=([0-9.]+)", graph)]
        starts = clip_start_times(plans)
        # 第 i+1 个片段在 offset 处开始淡入
        assert offsets[0] == pytest.approx(starts[1])
        assert offsets[1] + durations[1] == pytest.approx(starts[2] + durations[1])


class TestMix:
    def test_dubs_are_placed_at_clip_start_plus_their_offset_even_after_a_transition(self, tmp_path: Path):
        wav = _wav(tmp_path)
        dub = DubPlacement(wav, offset_in_clip_s=2.0, trim_head_s=0.0, length_s=1.0)
        plans = [_plan(0, 6, "fade"), _plan(0, 6, dub=[dub])]

        ((_, start),) = dub_placements(plans)

        assert start == pytest.approx(5.5 + 2.0)

    def test_original_is_lowered_to_thirty_percent_and_the_dub_is_not_touched(self, tmp_path: Path):
        placed = [(DubPlacement(_wav(tmp_path), 0.0, 0.0, 1.0), 3.0)]

        parts, label = build_dub_mix(placed, "ca1", first_input_index=2)
        graph = ";".join(parts)

        assert label == "afinal"
        assert "[ca1]volume=0.3[orig]" in graph
        assert "[2:a]atrim=start=0.000:end=1.000" in graph
        assert "adelay=3000|3000[dub0]" in graph
        assert "volume" not in graph.split("[2:a]")[1].split("[dub0]")[0]
        assert "amix=inputs=2:duration=first:normalize=0" in graph
        assert "alimiter=limit=0.97" in graph

    def test_cut_edges_get_short_fades(self, tmp_path: Path):
        placed = [(DubPlacement(_wav(tmp_path), 0.0, 0.5, 1.5, fade_in=True, fade_out=True), 1.0)]

        graph = ";".join(build_dub_mix(placed, "a", 1)[0])

        assert "afade=t=in:d=0.03" in graph
        assert "afade=t=out:st=1.470:d=0.03" in graph

    def test_ffmpeg_args_add_each_dub_as_an_extra_input_after_the_clips(self, tmp_path: Path):
        wav = _wav(tmp_path)
        plans = [_plan(0, 6, dub=[DubPlacement(wav, 1.0, 0.0, 1.0), DubPlacement(wav, 3.0, 0.0, 1.0)]), _plan(0, 4)]

        args = build_ffmpeg_args(plans, tmp_path / "out.mp4", width=320, height=180)

        inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
        assert inputs == ["x.mp4", "x.mp4", str(wav), str(wav)]
        assert args[args.index("-map", args.index("-map") + 1) + 1] == "[afinal]"

    def test_without_any_dub_the_command_is_unchanged(self, tmp_path: Path):
        plans = [_plan(0, 6), _plan(0, 4)]

        args = build_ffmpeg_args(plans, tmp_path / "out.mp4", width=320, height=180)

        graph = args[args.index("-filter_complex") + 1]
        assert "volume" not in graph
        assert "alimiter" not in graph
        assert "[ca1]" in args[args.index("-map", args.index("-map") + 1) + 1] or "ca1" in graph


def _timeline(clips: list[Clip]) -> Timeline:
    return Timeline(
        timeline_id="t",
        episode=1,
        name="A",
        revisions=[Revision(revision=1, author="me", summary="s", created_at="x", clips=clips)],
    )


async def _probe(path: Path) -> MediaProbe:
    return MediaProbe(duration_s=10.0, width=320, height=180, has_audio=True)


async def test_resolve_plans_hands_each_clip_the_dub_that_falls_inside_its_range(tmp_path: Path):
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos/U1.mp4").write_bytes(b"x")
    wav = _wav(tmp_path)
    catalog = [UnitInfo("U1", "videos/U1.mp4", 1, 10.0)]
    timeline = _timeline([Clip(clip_id="c1", unit_id="U1", in_s=2.0, out_s=6.0, trimmed_against_version=1)])
    sources = {"U1": [DubSource(wav, 1.0, 0.5), DubSource(wav, 3.0, 1.0), DubSource(wav, 8.0, 1.0)]}

    plans, _ = await resolve_plans(timeline, catalog, tmp_path, probe=_probe, dub_sources=sources)

    (plan,) = plans
    assert [(d.offset_in_clip_s, d.length_s) for d in plan.dub] == [(1.0, 1.0)]


async def test_resolve_plans_without_dub_sources_has_no_dub(tmp_path: Path):
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos/U1.mp4").write_bytes(b"x")
    catalog = [UnitInfo("U1", "videos/U1.mp4", 1, 10.0)]
    timeline = _timeline([Clip(clip_id="c1", unit_id="U1")])

    plans, _ = await resolve_plans(timeline, catalog, tmp_path, probe=_probe)

    assert plans[0].dub == ()
