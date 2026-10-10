"""按 Agent 工具集声明表驱动的远程 MCP 投影。

每条声明经远程 MCP 投影出的名字、schema、描述、迁移阻断与结果信封必须与声明一致；
远程 schema 在声明之上多一个 ``project``，长任务描述多一段附注；无 scope 声明（列出、创建项目）不带 ``project``。
领域行为在 handler 的 ``ToolOutcome`` 层测，这里的 fake handler 只用于观察 adapter 是否原样透传。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import types

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_result import GenerationBatchResult
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    RETRY_MIGRATION_ACTION,
    record_migration_failure,
)
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_toolset.declaration import (
    BLOCKED,
    MIGRATION_REFUSAL_NOTE,
    AgentToolDeclaration,
    Blocked,
    ToolDeclaration,
    UnscopedToolDeclaration,
    tool_description,
)
from server.agent_toolset.dub_voiceover import GENERATE_DUB_VOICEOVER, GET_DUB_STATUS
from server.agent_toolset.envelope import json_value
from server.agent_toolset.generation_batches import CANCEL_GENERATION_BATCH, GET_GENERATION_BATCH
from server.agent_toolset.grid_storyboards import GENERATE_GRID, SPLIT_GRIDS
from server.agent_toolset.keyframe_planning import GET_KEYFRAME_BRIEF, SUBMIT_KEYFRAME_PLAN
from server.agent_toolset.orientation import GET_PROMPT_PREVIEW, GET_VIDEO_CAPABILITIES
from server.agent_toolset.project_entry import CREATE_PROJECT
from server.agent_toolset.remote import LONG_TASK_NOTE, remote_tool
from server.agent_toolset.script_authoring import (
    APPROVE_SCREENPLAY,
    CONFIRM_SCRIPT_REVIEW,
    DECIDE_DIRECTION_REVIEW,
    GENERATE_DIRECTOR_PLAN,
    GENERATE_EPISODE_SCRIPT,
    GET_DIRECTION_REPORT,
    GET_SCREENPLAY,
    OPEN_DRAFT,
    PATCH_DRAFT,
    PATCH_SCREENPLAY,
    PROMOTE_DRAFT,
)
from server.agent_toolset.script_editing import PATCH_EPISODE_SCRIPT
from server.agent_toolset.toolset import AGENT_TOOLSET, ARCREEL_MCP_TOOL_IDS, MIGRATION_BLOCKED_TOOL_IDS
from server.agent_toolset.workflow_completion import COMPLETE_ASSET_INVENTORY, COMPLETE_SCRIPT_PLAN_REBUILD
from server.remote_mcp import build_remote_mcp_server
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import (
    CallerContext,
    GenerationBatchToolRequest,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    get_generation_batch,
)

_ABSENT_REVISION = "sha256-v1:" + "0" * 64

# 每条声明一份合法入参；新增声明须在此登记，否则参数化用例以 KeyError 失败。
SAMPLE_ARGUMENTS: dict[str, dict[str, Any]] = {
    "list_projects": {},
    "create_project": {"name": "fresh", "title": "Fresh"},
    "upload_source": {"filename": "novel.txt", "content": "第一章\n你好", "on_conflict": "replace"},
    "get_workflow_plan": {"episode": 1},
    "get_video_capabilities": {},
    "get_prompt_preview": {"script": "episode_1.json", "item_id": "E1S01"},
    "get_generation_batch": {"batch_id": "batch-absent"},
    "cancel_generation_batch": {"batch_id": "batch-absent"},
    "patch_project": {"overview": {"synopsis": "梗概"}},
    "patch_episode_meta": {"script": "episode_1.json", "field": "title", "value": "第一集"},
    "rename_asset": {"table": "characters", "old_name": "甲", "new_name": "乙"},
    "retry_project_migration": {},
    "get_project_content": {},
    "list_source_files": {},
    "get_source_text": {"path": "source/episode_1.txt"},
    "get_episode_script": {"script": "episode_1.json"},
    "get_script_plan_content": {"episode": 1},
    "list_project_files": {},
    "read_project_file": {"path": "project.json"},
    "list_pending_assets": {"type": "character"},
    "generate_assets": {"type": "character", "names": ["张三"]},
    "generate_storyboards": {"script": "episode_1.json"},
    "edit_images": {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]},
    "generate_narration_audio": {"script": "episode_1.json", "segment_ids": ["E1S01"]},
    "generate_dub_voiceover": {"script": "episode_1.json", "unit_ids": ["E1U1"], "skip_timing": True},
    "get_dub_status": {"script": "episode_1.json"},
    "generate_episode_script": {"episode": 1, "dry_run": True},
    "submit_screenplay": {
        "episode": 1,
        "title": "t",
        "logline": "l",
        "scenes": [
            {
                "heading": "内景 · 屋 · 日",
                "summary": "s",
                "beats": [{"kind": "dialogue", "speaker": "张三", "text": "你好"}],
            }
        ],
    },
    "get_screenplay": {"episode": 1},
    "patch_screenplay": {
        "episode": 1,
        "base_fingerprint": "x",
        "operations": [{"op": "set_meta", "fields": {"title": "t"}}],
    },
    "approve_screenplay": {"episode": 1, "fingerprint": "x"},
    "generate_script_plan": {"episode": 1, "dry_run": True},
    "get_script_plan_brief": {"episode": 1},
    "submit_script_plan": {"episode": 1, "units": [{"duration_seconds": 4, "source_text": "s", "text": "t"}]},
    "confirm_script_review": {"episode": 1},
    "generate_director_plan": {"episode": 1, "dry_run": True},
    "get_prompt_authoring_brief": {"episode": 1},
    "submit_h3_prompts": {
        "episode": 1,
        "units": [
            {
                "unit_id": "E1U01",
                "subject_definitions": "s",
                "summary": "s",
                "retention_analysis": "s",
                "detailed_description": "s",
                "overall_soundscape": "s",
                "non_diegetic_music": "N/A",
            }
        ],
    },
    "get_director_brief": {"episode": 1},
    "submit_director_plan": {"episode": 1, "units": [{"unit_id": "E1U01", "direction": "导演说明"}]},
    "get_direction_report": {"episode": 1},
    "get_keyframe_brief": {"episode": 1},
    "delete_asset": {"table": "characters", "name": "不存在"},
    "get_canvas": {},
    "canvas_add_node": {"type": "text", "position": {"x": 0, "y": 0}, "text": "t"},
    "canvas_patch_node": {"node_id": "n", "text": "t"},
    "canvas_delete_node": {"node_id": "n"},
    "canvas_connect": {"source": "a", "source_handle": "text", "target": "b", "target_handle": "prompt"},
    "canvas_disconnect": {"edge_id": "e"},
    "canvas_run_node": {"node_id": "n"},
    "canvas_save_template": {"name": "t", "node_ids": ["n"]},
    "canvas_use_template": {"template_id": "t", "position": {"x": 0, "y": 0}},
    "canvas_import_episode": {"episode": 1},
    "canvas_import_assets": {},
    "get_keyframe_board": {"episode": 1},
    "generate_keyframe_sheet": {"episode": 1, "sheet_id": "S1"},
    "canvas_group": {"node_ids": ["n"], "label": "g", "position": {"x": 0, "y": 0}},
    "canvas_ungroup": {"group_id": "g"},
    "submit_keyframe_plan": {
        "episode": 1,
        "cells": [
            {
                "label": "近景",
                "scene": "车内",
                "staging": "甲在左、乙在右",
                "framing": "近景",
                "characters": ["甲"],
                "unit_ids": ["E1U01"],
            }
        ],
    },
    "decide_direction_review": {"episode": 1, "decision": "approve"},
    "create_review_summary": {"episode": 1, "review_kind": "direction"},
    "open_draft": {"episode": 1, "doc_type": "drama_script_plan"},
    "patch_draft": {
        "episode": 1,
        "doc_type": "drama_script_plan",
        "content": {"title": "第一集", "scenes": []},
        "base_revision": _ABSENT_REVISION,
    },
    "promote_draft": {"episode": 1, "doc_type": "drama_script_plan", "base_revision": _ABSENT_REVISION},
    "discard_draft": {"episode": 1, "doc_type": "drama_script_plan", "base_revision": _ABSENT_REVISION},
    "patch_episode_script": {
        "script": "episode_9.json",
        "base_revision": "sha256-v1:" + "0" * 64,
        "operations": [{"op": "remove", "id": "E9S01"}],
    },
    "generate_grid": {"script": "episode_1.json", "list_only": True},
    "split_grids": {"grid_ids": ["grid_000000000000"]},
    "plan_episodes": {"instructions": "按章节对齐切分"},
    "reset_episode_planning": {"from_episode": 1},
    "complete_asset_inventory": {
        "scope": {"kind": "all", "files": []},
        "expected_source_revision": "sha256-v1:" + "0" * 64,
    },
    "complete_script_plan_rebuild": {"episode": 1, "expected_stale_script_plan_revision": None},
    "generate_videos": {
        "script": "episode_1.json",
        "target": {"scope": "all"},
        "narration_delivery": "post_production",
    },
}

_DECLARATIONS = pytest.mark.parametrize("declaration", AGENT_TOOLSET, ids=lambda declaration: declaration.name)
_SCOPED_DECLARATIONS = pytest.mark.parametrize(
    "declaration",
    [declaration for declaration in AGENT_TOOLSET if isinstance(declaration, ToolDeclaration)],
    ids=lambda declaration: declaration.name,
)


@pytest.fixture
def seeded_projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "data")
    root = manager.projects_dir
    manager.create_project("demo", content_mode="drama")
    manager.create_project_metadata("demo", "Demo", "", "drama")
    manager.upsert_assets("demo", "characters", {"甲": {"description": "主角"}})
    project_dir = root / "demo"
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "episode_1.txt").write_text("第一集原文", encoding="utf-8")
    (project_dir / "scripts").mkdir(exist_ok=True)
    (project_dir / "scripts" / "episode_1.json").write_text('{"episode":1,"scenes":[]}', encoding="utf-8")
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_normalized_script.json").write_text('{"title":"第一集","scenes":[]}', encoding="utf-8")
    (root / "empty").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "project.json").write_text("{}", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    return manager


@pytest.fixture
def services(seeded_projects: ProjectManager) -> Services:
    return Services(
        projects=seeded_projects,
        workflow_planner=WorkflowPlanner(seeded_projects),
        capabilities=ConfigResolver(async_session_factory),
    )


def _scope(seeded_projects: ProjectManager) -> ProjectScope:
    return ProjectScope(project_name="demo", data_root=seeded_projects.data_root)


def _answering[DeclarationT: AgentToolDeclaration](
    declaration: DeclarationT, outcome: ToolOutcome[Any], calls: list[object]
) -> DeclarationT:
    if isinstance(declaration, UnscopedToolDeclaration):

        async def unscoped_handler(request, _caller, _services) -> ToolOutcome[Any]:
            calls.append(request.value)
            return outcome

        return replace(declaration, handler=unscoped_handler)

    async def handler(request, _scope, _caller, _services) -> ToolOutcome[Any]:
        calls.append(request.value)
        return outcome

    return replace(declaration, handler=handler)


def _remote_arguments(declaration: AgentToolDeclaration, arguments: dict[str, Any]) -> dict[str, Any]:
    """外部 Agent 的等价入参：作用于项目的工具显式带上 ``project``。"""
    if isinstance(declaration, UnscopedToolDeclaration):
        return dict(arguments)
    return {"project": "demo", **arguments}


async def _call_remote(
    declaration: AgentToolDeclaration, arguments: dict[str, Any], services: Services
) -> types.CallToolResult:
    tool = remote_tool(
        declaration,
        projects=services.projects,
        services=services,
        caller=lambda: CallerContext(user_id="u1", source="mcp"),
    )
    result = await tool.run(arguments)
    assert isinstance(result, types.CallToolResult)
    return result


def _texts(result: types.CallToolResult) -> list[str]:
    return [block.text for block in result.content if isinstance(block, types.TextContent)]


def _without_project(schema: dict[str, Any]) -> dict[str, Any]:
    stripped = {**schema, "properties": {k: v for k, v in schema["properties"].items() if k != "project"}}
    required = [name for name in schema.get("required", []) if name != "project"]
    if required:
        stripped["required"] = required
    else:
        stripped.pop("required", None)
    return stripped


@_SCOPED_DECLARATIONS
async def test_remote_exposes_the_declared_name_schema_and_description(
    declaration: ToolDeclaration[Any, Any], services: Services
) -> None:
    remote = {tool.name: tool for tool in await build_remote_mcp_server(services=services).list_tools()}[
        declaration.name
    ]

    assert declaration.name in ARCREEL_MCP_TOOL_IDS
    assert remote.inputSchema["properties"]["project"]["type"] == "string"
    assert remote.inputSchema["required"][0] == "project"
    assert _without_project(remote.inputSchema) == declaration.input_schema
    assert all(spec.get("description") for spec in remote.inputSchema["properties"].values())
    assert remote.description == tool_description(declaration) + (LONG_TASK_NOTE if declaration.long_task else "")
    assert remote.description.removesuffix(LONG_TASK_NOTE if declaration.long_task else "").endswith(
        MIGRATION_REFUSAL_NOTE
    ) is isinstance(declaration.migration, Blocked)


@pytest.mark.parametrize(
    "declaration",
    [declaration for declaration in AGENT_TOOLSET if isinstance(declaration, UnscopedToolDeclaration)],
    ids=lambda declaration: declaration.name,
)
async def test_remote_exposes_an_unscoped_declaration_without_project(
    declaration: UnscopedToolDeclaration[Any, Any], services: Services
) -> None:
    remote = {tool.name: tool for tool in await build_remote_mcp_server(services=services).list_tools()}[
        declaration.name
    ]

    assert declaration.name in ARCREEL_MCP_TOOL_IDS
    assert "project" not in remote.inputSchema["properties"]
    assert remote.inputSchema == declaration.input_schema
    assert all(spec.get("description") for spec in remote.inputSchema["properties"].values())
    assert remote.description == declaration.description
    assert MIGRATION_REFUSAL_NOTE not in remote.description


async def test_every_tool_the_remote_exposes_is_defined_by_a_declaration(services: Services) -> None:
    """远程注册的工具集合与声明集合相同：没有声明之外的第二份工具定义。

    ``SAMPLE_ARGUMENTS`` 逐个列出了全部工具，它的键集合同时充当工具清单。
    """
    remote = {tool.name for tool in await build_remote_mcp_server(services=services).list_tools()}
    declared = {declaration.name for declaration in AGENT_TOOLSET}

    assert remote == declared == set(ARCREEL_MCP_TOOL_IDS) == set(SAMPLE_ARGUMENTS)


@_DECLARATIONS
async def test_problems_pass_through_the_remote_host_unchanged(
    declaration: AgentToolDeclaration, services: Services
) -> None:
    problem = ToolProblem("sentinel_problem", "原样透传", action="sentinel_action", params={"ids": ["E1S01"]})
    answering = _answering(declaration, ToolOutcome(problem=problem), [])

    remote = await _call_remote(answering, _remote_arguments(answering, SAMPLE_ARGUMENTS[declaration.name]), services)

    expected = {
        "problem": {
            "code": "sentinel_problem",
            "detail": "原样透传",
            "action": "sentinel_action",
            "params": {"ids": ["E1S01"]},
        }
    }
    assert remote.isError is True
    assert remote.structuredContent == expected


@_DECLARATIONS
async def test_invalid_arguments_are_rejected_as_an_invalid_request(
    declaration: AgentToolDeclaration, services: Services
) -> None:
    arguments = {**SAMPLE_ARGUMENTS[declaration.name], "unexpected": 1}

    remote = await _call_remote(declaration, _remote_arguments(declaration, arguments), services)

    assert remote.isError is True
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "invalid_request"
    assert remote.structuredContent["problem"]["params"]["errors"][0]["loc"] == ["unexpected"]


@_SCOPED_DECLARATIONS
async def test_a_blocked_declaration_refuses_a_migration_failed_project(
    declaration: ToolDeclaration[Any, Any], seeded_projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    calls: list[object] = []
    blocked = _answering(replace(declaration, migration=BLOCKED), ToolOutcome(value={}), calls)

    remote = await _call_remote(blocked, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

    assert calls == []
    assert remote.isError is True
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION
    assert problem["params"]["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert len(_texts(remote)) == 2


@pytest.mark.parametrize(
    "declaration",
    [
        declaration
        for declaration in AGENT_TOOLSET
        if not (isinstance(declaration, ToolDeclaration) and isinstance(declaration.migration, Blocked))
    ],
    ids=lambda declaration: declaration.name,
)
async def test_unblocked_declarations_reach_the_handler_on_a_migration_failed_project(
    declaration: AgentToolDeclaration, seeded_projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    answering = _answering(declaration, ToolOutcome(value={"answered": True}), [])

    remote = await _call_remote(answering, _remote_arguments(answering, SAMPLE_ARGUMENTS[declaration.name]), services)

    assert remote.structuredContent == {declaration.domain_key: {"answered": True}}


@pytest.mark.parametrize(
    "declaration",
    [
        declaration
        for declaration in AGENT_TOOLSET
        if isinstance(declaration, ToolDeclaration) and isinstance(declaration.migration, Blocked)
    ],
    ids=lambda declaration: declaration.name,
)
async def test_declared_blocked_entries_refuse_a_migration_failed_project_before_the_handler(
    declaration: ToolDeclaration[Any, Any], seeded_projects: ProjectManager, services: Services
) -> None:
    """声明为 ``BLOCKED`` 的入口按声明阻断，不依赖 handler 内层的迁移兜底。"""
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    calls: list[object] = []
    answering = _answering(declaration, ToolOutcome(value={}), calls)

    remote = await _call_remote(answering, {"project": "demo", **SAMPLE_ARGUMENTS[declaration.name]}, services)

    assert declaration.name in MIGRATION_BLOCKED_TOOL_IDS
    assert calls == []
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION


async def test_episode_script_reader_reports_the_migration_problem(
    seeded_projects: ProjectManager, services: Services
) -> None:
    record_migration_failure(
        seeded_projects.get_project_path("demo"),
        RuntimeError("清单预检失败"),
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
    )
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "get_episode_script")

    remote = await _call_remote(declaration, {"project": "demo", "script": "episode_1.json"}, services)

    assert remote.isError is True
    assert remote.structuredContent is not None
    problem = remote.structuredContent["problem"]
    assert problem["code"] == MIGRATION_FAILURE_CODE
    assert problem["action"] == RETRY_MIGRATION_ACTION
    assert problem["params"]["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION


async def test_patch_episode_script_reports_a_missing_script_as_script_not_found(
    services: Services,
) -> None:
    arguments = SAMPLE_ARGUMENTS[PATCH_EPISODE_SCRIPT.name]

    remote = await _call_remote(PATCH_EPISODE_SCRIPT, _remote_arguments(PATCH_EPISODE_SCRIPT, arguments), services)

    assert remote.isError is True
    assert remote.structuredContent is not None
    assert remote.structuredContent["problem"]["code"] == "script_not_found"


# 真实 handler 的结果随调用时刻变化，无法逐字比较；透传由其余用例的 fake handler 覆盖。
_TIME_DEPENDENT_RESULTS = frozenset({CREATE_PROJECT.name})

# 样例入参下合法地返回 problem 的声明：测试项目缺少它们要找的对象或能力配置。其余声明在样例入参下必须成功。
_PROBLEM_ON_SAMPLE = frozenset(
    {
        GET_VIDEO_CAPABILITIES.name,
        GET_PROMPT_PREVIEW.name,
        GET_GENERATION_BATCH.name,
        CANCEL_GENERATION_BATCH.name,
        # 测试项目的画布是空的：这些工具要找的节点与连线不存在（真实流程见 test_canvas_tools）。
        "delete_asset",
        "canvas_patch_node",
        "canvas_delete_node",
        "canvas_connect",
        "canvas_disconnect",
        "canvas_run_node",
        "canvas_save_template",
        "canvas_use_template",
        "canvas_import_episode",
        "canvas_group",
        "canvas_ungroup",
        # 测试项目不是参考生视频项目：起始帧看板不适用。
        "get_keyframe_board",
        "generate_keyframe_sheet",
        # 测试项目不是参考生视频项目：单元拆解的简报与提交只服务该路线。
        "get_script_plan_brief",
        "submit_script_plan",
        "get_director_brief",
        "submit_director_plan",
        "get_prompt_authoring_brief",
        "submit_h3_prompts",
        # 测试项目已有正式脚本：确认需要覆盖认可，取回编辑副本被拒；没有在场草稿可改、可晋升。
        CONFIRM_SCRIPT_REVIEW.name,
        OPEN_DRAFT.name,
        PATCH_DRAFT.name,
        PROMOTE_DRAFT.name,
        PATCH_EPISODE_SCRIPT.name,
        SPLIT_GRIDS.name,
        COMPLETE_ASSET_INVENTORY.name,
        COMPLETE_SCRIPT_PLAN_REBUILD.name,
        # 测试项目没有登记分集条目，也没有待编写条目：导演审阅找不到分集、导演步骤拒绝导演。
        GENERATE_DIRECTOR_PLAN.name,
        GET_DIRECTION_REPORT.name,
        DECIDE_DIRECTION_REVIEW.name,
        # 测试项目没有登记分集条目：剧本审阅找不到分集。
        GET_SCREENPLAY.name,
        PATCH_SCREENPLAY.name,
        APPROVE_SCREENPLAY.name,
        "create_review_summary",
        # 测试项目没有登记分集条目：起始帧规划找不到分集。
        GET_KEYFRAME_BRIEF.name,
        SUBMIT_KEYFRAME_PLAN.name,
        # 测试项目不是参考生视频，也未启用配音。
        GENERATE_DUB_VOICEOVER.name,
        GET_DUB_STATUS.name,
    }
)


@pytest.mark.parametrize(
    "declaration",
    [
        declaration
        for declaration in AGENT_TOOLSET
        if not (isinstance(declaration, ToolDeclaration) and declaration.long_task)
        and declaration.name not in _TIME_DEPENDENT_RESULTS
    ],
    ids=lambda declaration: declaration.name,
)
async def test_remote_content_carries_the_same_json_as_structured_content(
    declaration: AgentToolDeclaration, services: Services
) -> None:
    remote = await _call_remote(
        declaration, _remote_arguments(declaration, SAMPLE_ARGUMENTS[declaration.name]), services
    )

    assert remote.isError is (declaration.name in _PROBLEM_ON_SAMPLE)
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {"problem" if remote.isError else declaration.domain_key}
    assert json.loads(_texts(remote)[-1]) == remote.structuredContent


async def test_a_summary_precedes_the_structured_json(services: Services) -> None:
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "get_project_content")
    summarized = replace(
        _answering(declaration, ToolOutcome(value={"title": "Demo"}), []),
        summary=lambda value: f"已读取《{value['title']}》",
    )

    remote = await _call_remote(summarized, {"project": "demo"}, services)

    assert _texts(remote) == ["已读取《Demo》", '{"project_content": {"title": "Demo"}}']
    assert remote.structuredContent == {"project_content": {"title": "Demo"}}


@_SCOPED_DECLARATIONS
@pytest.mark.parametrize(
    "project",
    [pytest.param(None, id="missing"), 7, "", "   ", "../demo", "demo/..", "absent", "empty", "escape"],
)
async def test_remote_project_locating_failures_are_invalid_project(
    declaration: ToolDeclaration[Any, Any], project: object, services: Services
) -> None:
    calls: list[object] = []
    answering = _answering(declaration, ToolOutcome(value={}), calls)
    arguments = dict(SAMPLE_ARGUMENTS[declaration.name])
    if project is not None:
        arguments["project"] = project

    result = await _call_remote(answering, arguments, services)

    assert calls == []
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["problem"]["code"] == "invalid_project"


async def test_remote_long_task_returns_a_batch_handle_that_polls_to_the_terminal_generation_result(
    seeded_projects: ProjectManager, services: Services, db_factory
) -> None:
    """长任务立即返回批次句柄，按句柄轮询到终态后读取附带的生成结果。"""
    project = seeded_projects.load_project("demo")
    project["characters"] = {"李四": {"description": ""}}
    seeded_projects.save_project("demo", project)
    services = replace(services, queue=GenerationQueue(session_factory=db_factory, project_manager=seeded_projects))
    declaration = next(declaration for declaration in AGENT_TOOLSET if declaration.name == "generate_assets")
    arguments = {"type": "character", "names": ["李四"]}
    remote_caller = CallerContext(user_id="u1", source="mcp")

    remote = await remote_tool(
        declaration, projects=seeded_projects, services=services, caller=lambda: remote_caller
    ).run({"project": "demo", **arguments})
    assert isinstance(remote, types.CallToolResult)
    assert remote.structuredContent is not None
    handle = remote.structuredContent["generation_batch"]
    polled = await get_generation_batch(
        ToolRequest(GenerationBatchToolRequest(batch_id=handle["batch_id"])),
        _scope(seeded_projects),
        remote_caller,
        services,
    )

    assert remote.isError is False
    assert set(remote.structuredContent) == {"generation_batch"}
    assert polled.value is not None
    assert polled.value.done is True
    assert polled.value.generation_result is not None
    polled_result = json_value(polled.value.generation_result)
    assert GenerationBatchResult.model_validate(polled_result).blocked == ["character/李四"]


async def test_grid_list_only_preview_is_returned_immediately(
    seeded_projects: ProjectManager, services: Services
) -> None:
    """宫格预览不是生成结果：立即拿到规划文本，放在工具名下，没有批次可轮询。"""
    project = seeded_projects.load_project("demo")
    project.update(
        {
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
        }
    )
    seeded_projects.save_project("demo", project)
    scenes = [{"scene_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)]
    (seeded_projects.get_project_path("demo") / "scripts" / "episode_1.json").write_text(
        json.dumps({"episode": 1, "content_mode": "drama", "scenes": scenes}), encoding="utf-8"
    )
    arguments = SAMPLE_ARGUMENTS[GENERATE_GRID.name]

    remote = await _call_remote(GENERATE_GRID, _remote_arguments(GENERATE_GRID, arguments), services)

    assert remote.isError is False
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {GENERATE_GRID.name}
    assert "E1S01..E1S04" in remote.structuredContent[GENERATE_GRID.name]


async def test_a_text_generation_dry_run_returns_the_prompt(
    services: Services,
) -> None:
    """长任务的 dry_run 不提交批次：立即拿到 ``text_generation``，没有批次句柄。"""
    arguments = SAMPLE_ARGUMENTS[GENERATE_EPISODE_SCRIPT.name]

    remote = await _call_remote(GENERATE_EPISODE_SCRIPT, {"project": "demo", **arguments}, services)

    assert remote.isError is False
    assert remote.structuredContent is not None
    assert set(remote.structuredContent) == {"text_generation"}
    assert "DRY RUN" in remote.structuredContent["text_generation"]["message"]
