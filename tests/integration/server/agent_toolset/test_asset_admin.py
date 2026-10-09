"""delete_asset：与网页删除同一写入口，名称不存在时给 problem。"""

from __future__ import annotations

from pathlib import Path

from lib.project.project_manager import ProjectManager
from server.agent_toolset.asset_admin import DeleteAssetRequest, delete_asset
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolRequest
from tests.fakes import FakeConfigResolver

_CALLER = CallerContext(user_id="default", source="mcp")


def _services(tmp_path: Path) -> Services:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.update_project("demo", lambda p: p.setdefault("characters", {}).update({"阿离": {"description": "x"}}))
    return Services(projects=pm, workflow_planner=WorkflowPlanner(pm), capabilities=FakeConfigResolver())


async def _delete(services: Services, table: str, name: str):
    scope = ProjectScope(project_name="demo", data_root=services.projects.data_root)
    return await delete_asset(ToolRequest(DeleteAssetRequest(table=table, name=name)), scope, _CALLER, services)


async def test_delete_asset_removes_entry(tmp_path: Path) -> None:
    services = _services(tmp_path)

    outcome = await _delete(services, "characters", "阿离")

    assert outcome.problem is None
    assert "阿离" not in services.projects.load_project("demo").get("characters", {})


async def test_delete_unknown_asset_is_a_not_found_problem(tmp_path: Path) -> None:
    outcome = await _delete(_services(tmp_path), "characters", "不存在")

    assert outcome.problem is not None
    assert outcome.problem.code == "not_found"
