"""Host-neutral tools for Vietnamese voice-over (dub): queue it, read its status.

配音任务按视频单元（``dub_auto`` / ``dub_tts``）入队，不是以产物为键的生成批次，因此不进
``generation_batch``：``generate_dub_voiceover`` 入队后立即返回任务 id，Agent 用 ``get_dub_status`` 轮询。
工具返回文本是 agent-facing（免 i18n）；显示名在 ``ARCREEL_MCP_TOOL_IDS`` 注册、补三语。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_activation import resolve_artifact_episode
from lib.project.project_manager import is_reference_video_project
from lib.script.reference_video.dub_lines import align_dub_lines, unit_utterances
from lib.script.script_models import get_generated_assets
from lib.speech.dub_settings import project_dub_target_language
from lib.speech.vieneu_service import ConfigLoader, load_config_from_db
from server.media_tools.context import RequestedIds, ScriptFilename, tool_error, tool_problem
from server.services.dub_service import build_unit_dub, dub_lines_of, enqueue_auto_dub
from server.services.tasks.dub_tasks import DUB_AUTO_TASK_TYPE, DUB_TASK_TYPE
from server.tool_runtime import (
    CallerContext,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
)

_GENERATE = "generate_dub_voiceover"
_STATUS = "get_dub_status"
_DUB_LINES_TASK_TYPE = "text_reference_dub_lines"
_ACTIVE_DUB_TASK_TYPES = (DUB_AUTO_TASK_TYPE, DUB_TASK_TYPE, _DUB_LINES_TASK_TYPE)
#: 一行配音被顺延不少于该秒数才计入 ``delayed_lines``（与前端预览的提示阈值一致）。
DELAYED_LINE_SECONDS = 0.3


class GenerateDubVoiceoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="剧本纯文件名（不含目录），如 episode_1.json")
    unit_ids: RequestedIds | SkipJsonSchema[None] = Field(
        default=None,
        description="要配音的视频单元 ID 列表；省略则为本集所有已有成片的单元",
    )
    skip_timing: bool = Field(
        default=False,
        description="跳过从成片检测原台词开始时间，直接按估算排布配音",
    )


class GetDubStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="剧本纯文件名（不含目录），如 episode_1.json")


def _applicability_problem(project: dict[str, Any]) -> ToolProblem | None:
    if not is_reference_video_project(project):
        return ToolProblem(
            "dub_not_applicable",
            "越南语配音只用于参考生视频项目；当前项目不是参考生视频生成模式",
        )
    if project_dub_target_language(project) is None:
        return ToolProblem(
            "dub_not_enabled",
            '项目未启用配音。先用 patch_project 的 settings 设置 dub_target_language="vi"'
            "（可选 narrator_gender、dub_voice_male、dub_voice_female），再重试",
        )
    return None


async def generate_dub_voiceover(
    request: ToolRequest[GenerateDubVoiceoverRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
    *,
    load_vieneu_config: ConfigLoader = load_config_from_db,
) -> ToolOutcome[dict[str, Any]]:
    try:
        script_filename = request.value.script
        unit_ids = request.value.unit_ids
        project = services.projects.load_project(scope.project_name)
        if (problem := _applicability_problem(project)) is not None:
            return ToolOutcome(problem=problem)
        # 配音固定走本地 VieNeu，不依赖通用音频供应商；只有被明确关闭时才拒绝
        if not (await load_vieneu_config()).enabled:
            return tool_problem("本地越南语配音（VieNeu）已在系统设置中关闭，请先启用", code="dub_vieneu_disabled")
        script = services.projects.load_script(scope.project_name, script_filename)
        known = {str(unit.get("unit_id")) for unit in script.get("video_units") or []}
        if unit_ids is not None:
            unknown = [unit_id for unit_id in unit_ids if unit_id not in known]
            if unknown:
                return tool_problem(f"剧本中没有这些视频单元：{unknown}", code="unit_not_found")
        episode = resolve_artifact_episode(project=project, script=script, script_filename=script_filename)
        result = await enqueue_auto_dub(
            scope.project_name,
            episode,
            script,
            script_filename,
            list(unit_ids) if unit_ids is not None else None,
            skip_timing=request.value.skip_timing,
            user_id=caller.user_id,
            source=caller.source,
            queue=services.queue,
        )
        if not result["task_ids"]:
            return tool_problem(
                "没有可配音的视频：所选单元都还没有成片（先用 generate_videos 生成视频），或视频里没有台词和画外音",
                code="dub_video_missing",
            )
        return ToolOutcome(value=result)
    except Exception as exc:
        return tool_error(_GENERATE, exc)


def _unit_status(unit: dict[str, Any]) -> dict[str, Any]:
    text = str(unit.get("text") or "")
    expected = len(unit_utterances(text))
    lines = dub_lines_of(unit)
    current = sum(1 for item in align_dub_lines(text, lines) if item.status == "current")
    timeline = build_unit_dub(unit)
    live = timeline["lines"]
    audio_current = sum(1 for line in live if line["audio_path"] and not line["stale"])
    detected = sum(1 for line in lines if line.detected_start_seconds is not None and line.index < expected)
    timed = sum(1 for line in live if line["start_source"] != "estimated")
    has_video = bool(get_generated_assets(unit).get("video_clip"))
    if not has_video:
        state = "no_video"
    elif current < expected:
        state = "no_lines"
    elif audio_current >= expected:
        state = "ready"
    elif timed < expected:
        state = "needs_timing"
    else:
        state = "needs_audio"
    return {
        "unit_id": str(unit.get("unit_id")),
        "has_video": has_video,
        "state": state,
        "lines_expected": expected,
        "lines": len(live),
        "detected_timing": detected,
        "audio_current": audio_current,
        "delayed_lines": sum(1 for line in live if line["delayed_seconds"] >= DELAYED_LINE_SECONDS),
        "overflow_seconds": timeline["overflow_seconds"],
    }


async def _active_dub_tasks(project_name: str, services: Services) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task_type in _ACTIVE_DUB_TASK_TYPES:
        for status in ("queued", "running"):
            page = await services.queue.list_tasks(
                project_name=project_name, status=status, task_type=task_type, page_size=500
            )
            tasks.extend(
                {
                    "task_id": row["task_id"],
                    "task_type": row["task_type"],
                    "unit_id": row["resource_id"],
                    "status": row["status"],
                }
                for row in page["items"]
            )
    return tasks


async def get_dub_status(
    request: ToolRequest[GetDubStatusRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    try:
        script_filename = request.value.script
        project = services.projects.load_project(scope.project_name)
        if (problem := _applicability_problem(project)) is not None:
            return ToolOutcome(problem=problem)
        script = services.projects.load_script(scope.project_name, script_filename)
        units = [_unit_status(unit) for unit in script.get("video_units") or [] if isinstance(unit, dict)]
        counts = {
            state: sum(1 for unit in units if unit["state"] == state)
            for state in ("no_video", "no_lines", "needs_timing", "needs_audio", "ready")
        }
        tasks = await _active_dub_tasks(scope.project_name, services)
        with_video = len(units) - counts["no_video"]
        return ToolOutcome(
            value={
                "script": script_filename,
                "units": units,
                "summary": {
                    "units_total": len(units),
                    "with_video": with_video,
                    **counts,
                    "complete": with_video > 0 and counts["ready"] == with_video,
                },
                "active_tasks": {"running": bool(tasks), "tasks": tasks},
            }
        )
    except Exception as exc:
        return tool_error(_STATUS, exc)


__all__ = [
    "DELAYED_LINE_SECONDS",
    "GenerateDubVoiceoverRequest",
    "GetDubStatusRequest",
    "generate_dub_voiceover",
    "get_dub_status",
]
