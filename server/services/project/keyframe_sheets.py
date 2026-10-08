"""起始帧联合图的入队：校验看板与图像供应商，创建生成任务。

Web 与 Agent 共用这一个入口。供应商在入队前解析（联合图必然 i2i，入队即知），解析不出时直接拒绝、
不建任务；执行层仍保留素材缺失与参考图上限的兜底（见 ``server/services/tasks/keyframe_sheet_tasks.py``）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from lib.generation.generation_queue import get_generation_queue
from lib.generation.generation_queue_client import TaskSpec
from lib.keyframes.store import load_board
from lib.keyframes.task_types import KEYFRAME_SHEET_TASK_TYPE, sheet_resource_id
from lib.project.project_manager import ProjectManager
from server.services.project.keyframe_plan import KeyframePlanError


class _Queue(Protocol):
    async def enqueue_task(self, **kwargs: Any) -> dict[str, Any]: ...


async def _i2i_provider(project: dict[str, Any]) -> str:
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory

    try:
        resolved = await ConfigResolver(async_session_factory).resolve_image_backend(
            project, None, generation_type="i2i"
        )
    except ValueError as exc:
        raise KeyframePlanError(
            "image_provider_unavailable", "没有可用的图生图（i2i）图像模型，先在设置里配置图像供应商"
        ) from exc
    return resolved.provider_id


async def enqueue_keyframe_sheet(
    pm: ProjectManager,
    project_name: str,
    episode: int,
    sheet_id: str,
    *,
    user_id: str,
    source: str = "webui",
    queue: _Queue | None = None,
    resolve_provider: Callable[[dict[str, Any]], Awaitable[str]] = _i2i_provider,
) -> dict[str, Any]:
    """把一张联合图的生成放进队列，返回 ``{"task_id", "deduped", "resource_id"}``。"""

    def _load() -> dict[str, Any]:
        project = pm.load_project(project_name)
        board = load_board(pm.get_project_path(project_name), episode)
        if board is None:
            raise KeyframePlanError("board_missing", f"第 {episode} 集还没有起始帧看板，先提交规划")
        if not any(sheet.id == sheet_id for sheet in board.sheets):
            raise KeyframePlanError("sheet_missing", f"看板里没有联合图 {sheet_id}")
        return project

    project = await asyncio.to_thread(_load)
    provider_id = await resolve_provider(project)
    resource_id = sheet_resource_id(episode, sheet_id)
    spec = TaskSpec.from_request(task_type=KEYFRAME_SHEET_TASK_TYPE, media_type="image", resource_id=resource_id)
    result = await (queue or get_generation_queue()).enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        source=source,
        user_id=user_id,
        provider_id=provider_id,
    )
    return {"task_id": result["task_id"], "deduped": result.get("deduped", False), "resource_id": resource_id}


__all__ = ["enqueue_keyframe_sheet"]
