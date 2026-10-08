"""分集源文上传：集文件、账本条目与替换已消费集时的 stale 标记（真实 ProjectManager + tmp_path）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.episode.episode_upload import (
    EpisodeSourceItem,
    EpisodeUploadError,
    upload_episode_source,
    upload_episode_sources,
)
from lib.project.project_manager import ProjectManager


def _write_project(
    tmp_path: Path, *, content_mode: str = "drama", episodes: list | None = None
) -> tuple[ProjectManager, str]:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": content_mode,
        "generation_mode": "storyboard",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": episodes or [],
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return ProjectManager.for_project_dir(project_dir), "demo"


def _load(pm: ProjectManager, name: str) -> dict:
    return json.loads((pm.get_project_path(name) / "project.json").read_text(encoding="utf-8"))


def test_first_upload_creates_the_episode_file_and_a_thin_planned_entry(tmp_path: Path) -> None:
    pm, name = _write_project(tmp_path)

    result = upload_episode_source(pm, name, 1, "第一集正文。\r\n第二行。", title="开端")

    assert (result.created, result.stale, result.title) == (True, False, "开端")
    assert (pm.get_project_path(name) / "source" / "episode_1.txt").read_text(
        encoding="utf-8"
    ) == "第一集正文。\n第二行。"
    assert _load(pm, name)["episodes"] == [
        {"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json", "ledger_status": "planned"}
    ]


def test_episodes_stay_ordered_when_uploaded_out_of_order(tmp_path: Path) -> None:
    pm, name = _write_project(tmp_path)

    upload_episode_source(pm, name, 3, "三")
    upload_episode_source(pm, name, 1, "一")

    assert [entry["episode"] for entry in _load(pm, name)["episodes"]] == [1, 3]


def test_replacing_a_planner_made_episode_drops_its_stale_source_range(tmp_path: Path) -> None:
    pm, name = _write_project(
        tmp_path,
        episodes=[
            {
                "episode": 1,
                "title": "旧标题",
                "script_file": "scripts/episode_1.json",
                "source_range": {"source_file": "source/novel.txt", "start": 0, "end": 10},
                "ledger_status": "planned",
            }
        ],
    )

    result = upload_episode_source(pm, name, 1, "新的第一集")

    entry = _load(pm, name)["episodes"][0]
    assert result.created is False
    assert "source_range" not in entry
    assert entry["title"] == "旧标题"


def test_replacing_a_consumed_episode_marks_it_stale_without_deleting_products(tmp_path: Path) -> None:
    pm, name = _write_project(
        tmp_path,
        episodes=[{"episode": 1, "title": "一", "script_file": "scripts/episode_1.json", "ledger_status": "consumed"}],
    )
    script = pm.get_project_path(name) / "scripts" / "episode_1.json"
    script.parent.mkdir()
    script.write_text("{}", encoding="utf-8")

    result = upload_episode_source(pm, name, 1, "改过的第一集")

    assert result.stale is True
    assert _load(pm, name)["episodes"][0]["ledger_status"] == "stale"
    assert script.exists()


def test_padded_alias_file_is_replaced_by_the_canonical_episode_file(tmp_path: Path) -> None:
    pm, name = _write_project(tmp_path)
    source_dir = pm.get_project_path(name) / "source"
    source_dir.mkdir()
    (source_dir / "episode_01.txt").write_text("旧", encoding="utf-8")

    upload_episode_source(pm, name, 1, "新")

    assert sorted(path.name for path in source_dir.iterdir()) == ["episode_1.txt"]


@pytest.mark.parametrize(
    ("episode", "text", "code"),
    [(0, "正文", "invalid_episode"), (1, "  \n ", "empty_source")],
)
def test_invalid_requests_are_rejected_before_anything_is_written(
    tmp_path: Path, episode: int, text: str, code: str
) -> None:
    pm, name = _write_project(tmp_path)

    with pytest.raises(EpisodeUploadError) as exc_info:
        upload_episode_source(pm, name, episode, text)

    assert exc_info.value.code == code
    assert _load(pm, name)["episodes"] == []
    assert not (pm.get_project_path(name) / "source").exists()


def test_non_drama_projects_are_refused_and_leave_no_file_behind(tmp_path: Path) -> None:
    pm, name = _write_project(tmp_path, content_mode="narration")

    with pytest.raises(EpisodeUploadError) as exc_info:
        upload_episode_source(pm, name, 1, "正文")

    assert exc_info.value.code == "unsupported_mode"
    assert not (pm.get_project_path(name) / "source" / "episode_1.txt").exists()


def test_batch_upload_registers_every_episode_in_one_commit(tmp_path: Path) -> None:
    pm, name = _write_project(tmp_path)

    results = upload_episode_sources(
        pm, name, [EpisodeSourceItem(2, "二", "乙"), EpisodeSourceItem(1, "一", "甲"), EpisodeSourceItem(3, "三")]
    )

    assert [(r.episode, r.title, r.created) for r in results] == [(2, "乙", True), (1, "甲", True), (3, "", True)]
    assert [(e["episode"], e["title"]) for e in _load(pm, name)["episodes"]] == [(1, "甲"), (2, "乙"), (3, "")]
    source = pm.get_project_path(name) / "source"
    assert sorted(path.name for path in source.iterdir()) == ["episode_1.txt", "episode_2.txt", "episode_3.txt"]


@pytest.mark.parametrize(
    "items",
    [
        [EpisodeSourceItem(1, "一"), EpisodeSourceItem(2, "  ")],
        [EpisodeSourceItem(1, "一"), EpisodeSourceItem(1, "又一")],
    ],
    ids=["blank-text", "duplicate-number"],
)
def test_one_bad_item_rejects_the_whole_batch_without_writing(tmp_path: Path, items: list[EpisodeSourceItem]) -> None:
    pm, name = _write_project(tmp_path)

    with pytest.raises(EpisodeUploadError):
        upload_episode_sources(pm, name, items)

    assert _load(pm, name)["episodes"] == []
    assert not (pm.get_project_path(name) / "source").exists()
