"""删除一集：账本、集源文、剧本 / 草稿 / 已登记产物，以及确认门槛（真实 ProjectManager + tmp_path）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.episode.episode_delete import EpisodeDeleteConfirmationRequired, EpisodeDeleteError, delete_episode
from lib.episode.episode_upload import upload_episode_source
from lib.project.project_manager import ProjectManager


def _project(tmp_path: Path) -> tuple[ProjectManager, str, Path]:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    project = {
        "schema_version": 3,
        "title": "T",
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "characters": {"李恒": {"description": "少年"}},
        "scenes": {},
        "props": {},
        "episodes": [],
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    pm = ProjectManager.for_project_dir(project_dir)
    upload_episode_source(pm, "demo", 1, "第一集")
    upload_episode_source(pm, "demo", 2, "第二集")
    return pm, "demo", project_dir


def _episodes(pm: ProjectManager) -> list[int]:
    return [e["episode"] for e in pm.load_project("demo")["episodes"]]


def test_deleting_a_fresh_episode_removes_its_entry_and_source_but_nothing_else(tmp_path: Path) -> None:
    pm, name, project_dir = _project(tmp_path)

    result = delete_episode(pm, name, 2)

    assert _episodes(pm) == [1]
    assert not (project_dir / "source" / "episode_2.txt").exists()
    assert (project_dir / "source" / "episode_1.txt").exists()
    assert result.removed_files == 1
    assert "李恒" in pm.load_project(name)["characters"]


def test_the_number_can_be_reused_after_deleting(tmp_path: Path) -> None:
    pm, name, _ = _project(tmp_path)
    delete_episode(pm, name, 2)

    upload_episode_source(pm, name, 2, "新的第二集")

    assert _episodes(pm) == [1, 2]


def test_an_episode_with_products_needs_confirmation_and_stays_intact_without_it(tmp_path: Path) -> None:
    pm, name, project_dir = _project(tmp_path)
    script = project_dir / "scripts" / "episode_1.json"
    script.parent.mkdir()
    script.write_text("{}", encoding="utf-8")

    with pytest.raises(EpisodeDeleteConfirmationRequired) as exc_info:
        delete_episode(pm, name, 1)

    assert exc_info.value.products.script is True
    assert _episodes(pm) == [1, 2]
    assert script.exists()
    assert (project_dir / "source" / "episode_1.txt").exists()


def test_confirmed_delete_removes_script_drafts_and_registered_artifacts_of_that_episode_only(tmp_path: Path) -> None:
    pm, name, project_dir = _project(tmp_path)
    for relative in ("scripts/episode_1.json", "drafts/episode_1/script_plan_normalized_script.json"):
        path = project_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    other = project_dir / "storyboards" / "scene_E2S01.png"
    mine = project_dir / "storyboards" / "scene_E1S01.png"
    mine.parent.mkdir()
    mine.write_bytes(b"x")
    other.write_bytes(b"y")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    basis = ArtifactBasis.build("test/registered", kind_version=1, inputs={}).digest
    adapter.put_entry(
        ArtifactKey.episode_storyboard(1, "E1S01"),
        ArtifactManifestEntry(artifact_path="storyboards/scene_E1S01.png", basis_digest=basis),
    )
    adapter.put_entry(
        ArtifactKey.episode_storyboard(2, "E2S01"),
        ArtifactManifestEntry(artifact_path="storyboards/scene_E2S01.png", basis_digest=basis),
    )

    result = delete_episode(pm, name, 1, confirm_products=True)

    assert _episodes(pm) == [2]
    assert not (project_dir / "scripts" / "episode_1.json").exists()
    assert not (project_dir / "drafts" / "episode_1").exists()
    assert not mine.exists()
    assert other.exists()
    assert result.removed_artifacts == 1
    assert list(adapter.snapshot_entries()) == [ArtifactKey.episode_storyboard(2, "E2S01")]


def test_unknown_episode_is_reported(tmp_path: Path) -> None:
    pm, name, _ = _project(tmp_path)

    with pytest.raises(EpisodeDeleteError) as exc_info:
        delete_episode(pm, name, 9)

    assert exc_info.value.code == "episode_not_found"
