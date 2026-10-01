from __future__ import annotations

import pytest

from lib.edit_timeline import store
from lib.edit_timeline.catalog import build_catalog, diagnose, mechanical_clips
from lib.edit_timeline.errors import InvalidTimelineError, RevisionConflictError, TimelineNotFoundError
from lib.edit_timeline.models import Clip


def _script() -> dict:
    return {
        "video_units": [
            {"unit_id": "U1", "duration_seconds": 5, "generated_assets": {"video_clip": "reference_videos/U1.mp4"}},
            {"unit_id": "U2", "duration_seconds": 4, "generated_assets": {"video_clip": None}},
            {"unit_id": "U3", "duration_seconds": 6, "generated_assets": {"video_clip": "reference_videos/U3.mp4"}},
        ]
    }


def test_mechanical_timeline_skips_units_without_video(tmp_path):
    clips = mechanical_clips(build_catalog(_script()))
    assert [c.unit_id for c in clips] == ["U1", "U3"]
    assert all(c.transition_to_next == "cut" and c.out_s is None for c in clips)


def test_append_revision_requires_latest_parent(tmp_path):
    tl = store.create_timeline(tmp_path, 1, name="A", clips=[Clip(clip_id="a", unit_id="U1")], author="me")
    updated = store.append_revision(
        tmp_path,
        1,
        tl.timeline_id,
        parent_revision=1,
        clips=[Clip(clip_id="a", unit_id="U1", in_s=1, out_s=3)],
        author="me",
        summary="trim",
    )
    assert updated.latest.revision == 2
    assert updated.latest.parent_revision == 1
    with pytest.raises(RevisionConflictError) as info:
        store.append_revision(tmp_path, 1, tl.timeline_id, parent_revision=1, clips=[], author="me", summary="stale")
    assert info.value.params == {"expected": 1, "latest": 2}


def test_rollback_appends_new_revision(tmp_path):
    tl = store.create_timeline(tmp_path, 1, name="A", clips=[Clip(clip_id="a", unit_id="U1")], author="me")
    store.append_revision(tmp_path, 1, tl.timeline_id, parent_revision=1, clips=[], author="me", summary="clear")
    rolled = store.rollback(tmp_path, 1, tl.timeline_id, to_revision=1, parent_revision=2, author="me")
    assert rolled.latest.revision == 3
    assert [c.unit_id for c in rolled.latest.clips] == ["U1"]
    assert len(rolled.revisions) == 3


def test_copy_rename_delete_and_list(tmp_path):
    tl = store.create_timeline(tmp_path, 2, name="A", clips=[Clip(clip_id="a", unit_id="U1")], author="me")
    copy = store.copy_timeline(tmp_path, 2, tl.timeline_id, name="B", author="me")
    assert copy.timeline_id != tl.timeline_id
    assert store.rename_timeline(tmp_path, 2, copy.timeline_id, name="C").name == "C"
    assert [t.name for t in store.list_timelines(tmp_path, 2)] == ["A", "C"]
    store.delete_timeline(tmp_path, 2, tl.timeline_id)
    with pytest.raises(TimelineNotFoundError):
        store.get_timeline(tmp_path, 2, tl.timeline_id)
    assert store.list_timelines(tmp_path, 9) == []


def test_invalid_clips_and_ids_are_rejected(tmp_path):
    with pytest.raises(InvalidTimelineError):
        store.create_timeline(
            tmp_path, 1, name="A", clips=[{"clip_id": "a", "unit_id": "U1", "in_s": 3, "out_s": 2}], author="me"
        )
    with pytest.raises(TimelineNotFoundError):
        store.get_timeline(tmp_path, 1, "../evil")


def test_diagnostics_report_deleted_unused_and_stale_trim():
    catalog = build_catalog(_script(), {"U1": 2, "U3": 1})
    tl = store.Timeline(
        timeline_id="t",
        episode=1,
        name="A",
        revisions=[
            store.Revision(
                revision=1,
                author="me",
                summary="s",
                created_at="x",
                clips=[
                    Clip(clip_id="a", unit_id="U1", in_s=0, out_s=2, trimmed_against_version=1),
                    Clip(clip_id="b", unit_id="GONE"),
                    Clip(clip_id="c", unit_id="U2"),
                ],
            )
        ],
    )
    d = diagnose(tl, catalog)
    assert d.stale_trim_clips == ["a"]
    assert d.deleted_unit_clips == ["b"]
    assert d.missing_video_clips == ["c"]
    assert d.unused_unit_ids == ["U3"]
