"""画布图片节点的任务执行层。

画布节点任务不进产物登记（Artifact Manifest）：它是自由创作，输入就是画布上连进来的
提示词与参考图，产物落在 ``canvas/images/{节点 id}.png``，结果写回画布图里对应的图片节点。
计费、供应商解析与参考图压缩复用既有生成链路。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from lib.canvas.graph import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    CanvasGraphError,
    find_node,
    reference_legend,
)
from lib.canvas.store import mutate_canvas
from lib.db.base import DEFAULT_USER_ID
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.project_manager import get_project_manager
from lib.project.resource_paths import CANVAS_IMAGE_RESOURCE_TYPE, resource_relative_path
from server.services.tasks.generation_context import ImageLaneRequest, resolve_generation_context

logger = logging.getLogger(__name__)

CANVAS_IMAGE_TASK_TYPE = "canvas_image"
_ERROR_MAX_CHARS = 500


async def _patch_nodes(project_name: str, patches: dict[str, dict[str, Any]]) -> None:
    """尽力把服务端字段写回节点；节点已被用户删除时静默跳过。"""
    pm = get_project_manager()
    project_path = await asyncio.to_thread(pm.get_project_path, project_name)

    def _apply(graph: dict[str, Any]) -> None:
        for node_id, data in patches.items():
            try:
                find_node(graph, node_id)["data"].update(data)
            except CanvasGraphError:
                logger.info("画布节点 %s 已不存在，跳过写回", node_id)

    await asyncio.to_thread(mutate_canvas, pm, project_path, _apply)


def _absolute_references(project_path: Path, relative_paths: list[str], labels: list[str]) -> list[tuple[Path, str]]:
    """可用的参考图及其名称；越界或已不存在的路径被丢弃，名称与图保持同序。"""
    resolved: list[tuple[Path, str]] = []
    for index, rel in enumerate(relative_paths):
        try:
            path = safe_join(project_path, rel)
        except PathTraversalError:
            continue
        if path.is_file():
            resolved.append((path, labels[index] if index < len(labels) else ""))
    return resolved


async def execute_canvas_image_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """``resource_id`` 是待填充的图片节点 id；payload 带运行时冻结的提示词、参考图、画幅与供应商节点 id。"""
    generator_id = str(payload.get("generator_id") or "")
    prompt = str(payload.get("prompt") or "")
    aspect_ratio = str(payload.get("aspect_ratio") or "9:16")
    pm = get_project_manager()
    project, project_path = await asyncio.gather(
        asyncio.to_thread(pm.load_project, project_name), asyncio.to_thread(pm.get_project_path, project_name)
    )
    found = await asyncio.to_thread(
        _absolute_references,
        project_path,
        [str(p) for p in payload.get("references") or []],
        [str(label) for label in payload.get("reference_labels") or []],
    )

    def _both(**data: Any) -> dict[str, dict[str, Any]]:
        patches = {resource_id: dict(data)}
        if generator_id:
            patches[generator_id] = {"status": data["status"], "error": data.get("error")}
        return patches

    await _patch_nodes(project_name, _both(status=STATUS_RUNNING, error=None))
    try:
        context = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            project_path=project_path,
            user_id=user_id,
            image=ImageLaneRequest(generation_type="i2i" if found else "t2i"),
        )
        limit = context.image.max_reference_images
        if limit > 0:
            found = found[:limit]
        references = [path for path, _ in found]
        # 「图N」编号即参考图在请求里的位置；说明只覆盖实际发出的图（裁剪之后）。
        legend = reference_legend([label for _, label in found])
        _, version = await context.generator.generate_image_async(
            f"{legend}\n\n{prompt}" if legend else prompt,
            CANVAS_IMAGE_RESOURCE_TYPE,
            resource_id,
            reference_images=references,
            aspect_ratio=aspect_ratio,
            image_size=context.image.resolution,
            task_id=task_id,
        )
    except BaseException as exc:
        failed = _both(status=STATUS_FAILED, error=str(exc)[:_ERROR_MAX_CHARS])
        await asyncio.shield(_patch_nodes(project_name, failed))
        raise

    rel_path = resource_relative_path(CANVAS_IMAGE_RESOURCE_TYPE, resource_id)
    patches = _both(status=STATUS_DONE, error=None)
    patches[resource_id].update(path=rel_path, version=version)
    await _patch_nodes(project_name, patches)
    return {"file_path": rel_path, "version": version, "resource_type": CANVAS_IMAGE_RESOURCE_TYPE}
