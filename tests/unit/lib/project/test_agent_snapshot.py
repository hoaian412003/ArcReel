import json

import pytest

from lib.project.agent_snapshot import (
    KEEP_SNAPSHOTS,
    SnapshotNotFoundError,
    diff_snapshot,
    list_snapshots,
    restore_snapshot,
    take_snapshot,
)


@pytest.fixture
def snapshot_project(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"characters": {"A": {}, "B": {}}}), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "ep1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.png").write_bytes(b"png")
    return tmp_path


def test_diff_reports_added_modified_removed_and_ignores_media(snapshot_project):
    project = snapshot_project
    snapshot = take_snapshot(project)
    (project / "project.json").write_text(json.dumps({"characters": {"A": {}}}), encoding="utf-8")
    (project / "scripts" / "ep1.json").unlink()
    (project / "notes.md").write_text("x", encoding="utf-8")
    (project / "images" / "b.png").write_bytes(b"new")

    assert diff_snapshot(project, snapshot) == {
        "added": ["notes.md"],
        "modified": ["project.json"],
        "removed": ["scripts/ep1.json"],
    }


def test_restore_rolls_back_text_files(snapshot_project):
    project = snapshot_project
    original = (project / "project.json").read_text(encoding="utf-8")
    snapshot = take_snapshot(project)
    (project / "project.json").write_text("{}", encoding="utf-8")
    (project / "scripts" / "ep1.json").unlink()
    (project / "notes.md").write_text("x", encoding="utf-8")

    restore_snapshot(project, snapshot)

    assert (project / "project.json").read_text(encoding="utf-8") == original
    assert (project / "scripts" / "ep1.json").exists()
    assert not (project / "notes.md").exists()
    assert diff_snapshot(project, snapshot) == {"added": [], "modified": [], "removed": []}


def test_snapshots_do_not_include_themselves_and_are_pruned(snapshot_project):
    project = snapshot_project
    ids = [take_snapshot(project, label=str(i)) for i in range(KEEP_SNAPSHOTS + 3)]

    kept = [item["id"] for item in list_snapshots(project)]
    assert len(kept) == KEEP_SNAPSHOTS
    assert set(kept) <= set(ids)
    assert diff_snapshot(snapshot_project, kept[0]) == {"added": [], "modified": [], "removed": []}


def test_unknown_or_malformed_id_is_rejected(snapshot_project):
    project = snapshot_project
    for bad in ("../../etc/passwd", "20260101000000-deadbeef"):
        with pytest.raises(SnapshotNotFoundError):
            restore_snapshot(project, bad)
