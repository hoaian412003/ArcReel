from __future__ import annotations

import io
import zipfile

import pytest

from lib.infra.data_root_layout import DataRootLayout
from server.claude_runtime.workspace import AgentWorkspace


def test_project_memory_is_separate_stable_and_reset_keeps_backup_and_source(tmp_path) -> None:
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    source = project / "source.txt"
    source.write_text("original story", encoding="utf-8")
    workspace = AgentWorkspace(DataRootLayout(tmp_path), project, "demo", "default")
    workspace.write_memory("MEMORY.md", "Remember EP04")
    workspace.set_main_session("main-session", "default")
    restored = AgentWorkspace(DataRootLayout(tmp_path), project, "demo", "default")
    assert restored.agent_id == workspace.agent_id
    assert restored.manifest["main_session_id"] == "main-session"
    restored.clear_main_session("default")
    assert restored.main_session("default") is None
    assert restored.memory() == [{"name": "MEMORY.md", "content": "Remember EP04"}]
    assert not restored.memory_dir.is_relative_to(project)
    restored.promote("MEMORY.md")
    assert (project / ".claude" / "rules" / "memory-MEMORY.md").read_text() == "Remember EP04"
    with zipfile.ZipFile(io.BytesIO(restored.export({"session": "test"}, []))) as archive:
        assert archive.read("memory/MEMORY.md") == b"Remember EP04"
        assert "history.json" in archive.namelist()
    restored.reset()
    assert restored.memory() == []
    assert source.read_text() == "original story"
    assert next((restored.root / "memory_backups").glob("*/MEMORY.md")).read_text() == "Remember EP04"


@pytest.mark.parametrize("name", ["../source.txt", "/tmp/memory.md", "nested/memory.md"])
def test_memory_write_rejects_paths_outside_memory_directory(tmp_path, name) -> None:
    workspace = AgentWorkspace(DataRootLayout(tmp_path), tmp_path, "demo", "default")
    with pytest.raises(ValueError, match="Invalid memory"):
        workspace.write_memory(name, "note")


def test_agents_do_not_share_memory_between_projects(tmp_path) -> None:
    first = AgentWorkspace(DataRootLayout(tmp_path), tmp_path, "first", "default")
    second = AgentWorkspace(DataRootLayout(tmp_path), tmp_path, "second", "default")
    first.write_memory("MEMORY.md", "first only")
    assert first.agent_id != second.agent_id
    assert second.memory() == []
