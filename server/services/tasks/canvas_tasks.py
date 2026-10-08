"""画布图片节点的任务执行层。

画布节点任务不进产物登记（Artifact Manifest）：它是自由创作，输入就是画布上连进来的
提示词与参考图，产物落在 ``canvas/images/{节点 id}.png``，结果写回画布图里对应的图片节点。
计费、供应商解析与参考图压缩复用既有生成链路。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
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
from lib.project.resource_paths import CANVAS_IMAGE_RESOURCE_TYPE, CANVAS_VIDEO_RESOURCE_TYPE, resource_relative_path
from server.services.tasks.generation_context import (
    AudioLaneRequest,
    ImageLaneRequest,
    VideoLaneRequest,
    resolve_generation_context,
)

logger = logging.getLogger(__name__)

CANVAS_IMAGE_TASK_TYPE = "canvas_image"
CANVAS_VIDEO_TASK_TYPE = "canvas_video"
CANVAS_AUDIO_TASK_TYPE = "canvas_audio"
#: 语音的资源类型就是 TTS 通道的 ``audio``，落盘 ``audio/segment_{id}.wav``。
CANVAS_AUDIO_RESOURCE_TYPE = "audio"
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


async def _run_node_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    resource_type: str,
    generate: Callable[[dict[str, Any], Path], Awaitable[int]],
) -> dict[str, Any]:
    """画布节点任务的共同骨架：置运行中 → 生成 → 把结果（路径与版本）或错误写回媒体节点与供应商节点。

    ``resource_id`` 是待填充的媒体节点 id；``generate`` 拿到项目与项目目录、产出文件并返回版本号。
    """
    generator_id = str(payload.get("generator_id") or "")
    pm = get_project_manager()
    project, project_path = await asyncio.gather(
        asyncio.to_thread(pm.load_project, project_name), asyncio.to_thread(pm.get_project_path, project_name)
    )

    def _both(**data: Any) -> dict[str, dict[str, Any]]:
        patches = {resource_id: dict(data)}
        if generator_id:
            patches[generator_id] = {"status": data["status"], "error": data.get("error")}
        return patches

    await _patch_nodes(project_name, _both(status=STATUS_RUNNING, error=None))
    try:
        version = await generate(project, project_path)
    except BaseException as exc:
        failed = _both(status=STATUS_FAILED, error=str(exc)[:_ERROR_MAX_CHARS])
        await asyncio.shield(_patch_nodes(project_name, failed))
        raise

    rel_path = resource_relative_path(resource_type, resource_id)
    patches = _both(status=STATUS_DONE, error=None)
    patches[resource_id].update(path=rel_path, version=version)
    await _patch_nodes(project_name, patches)
    return {"file_path": rel_path, "version": version, "resource_type": resource_type}


async def execute_canvas_image_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """``resource_id`` 是待填充的图片节点 id；payload 带运行时冻结的提示词、参考图、画幅与供应商节点 id。"""
    prompt = str(payload.get("prompt") or "")
    aspect_ratio = str(payload.get("aspect_ratio") or "9:16")

    async def _generate(project: dict[str, Any], project_path: Path) -> int:
        found = await asyncio.to_thread(
            _absolute_references,
            project_path,
            [str(p) for p in payload.get("references") or []],
            [str(label) for label in payload.get("reference_labels") or []],
        )
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
        # 「图N」编号即参考图在请求里的位置；说明只覆盖实际发出的图（裁剪之后）。
        legend = reference_legend([label for _, label in found])
        _, version = await context.generator.generate_image_async(
            f"{legend}\n\n{prompt}" if legend else prompt,
            CANVAS_IMAGE_RESOURCE_TYPE,
            resource_id,
            reference_images=[path for path, _ in found],
            aspect_ratio=aspect_ratio,
            image_size=context.image.resolution,
            task_id=task_id,
        )
        return version

    return await _run_node_task(
        project_name, resource_id, payload, resource_type=CANVAS_IMAGE_RESOURCE_TYPE, generate=_generate
    )


async def execute_canvas_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """``resource_id`` 是待填充的视频节点 id。有起始帧时走图生视频，没有就是文生视频。

    画布视频不进续跑检查点：进程在供应商任务进行中被重启时，该节点失败，需要重新运行。
    """
    prompt = str(payload.get("prompt") or "")

    async def _generate(project: dict[str, Any], project_path: Path) -> int:
        found = await asyncio.to_thread(
            _absolute_references, project_path, [str(p) for p in payload.get("references") or []], []
        )
        start_image = found[0][0] if found else None
        context = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            project_path=project_path,
            user_id=user_id,
            video=VideoLaneRequest(generation_type="i2v" if start_image else None),
        )
        _, version, _, _ = await context.generator.generate_video_async(
            prompt,
            CANVAS_VIDEO_RESOURCE_TYPE,
            resource_id,
            start_image=start_image,
            aspect_ratio=str(payload.get("aspect_ratio") or "9:16"),
            duration_seconds=int(payload.get("duration_seconds") or 5),
            resolution=context.video.resolution,
            task_id=task_id,
        )
        return version

    return await _run_node_task(
        project_name, resource_id, payload, resource_type=CANVAS_VIDEO_RESOURCE_TYPE, generate=_generate
    )


async def execute_canvas_audio_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """``resource_id`` 是待填充的语音节点 id。文本合成语音，音色缺省取项目的旁白音色。

    产物落在 ``audio/segment_{节点 id}.wav``（TTS 通道只有这一个落点）；节点 id 不会与剧本分镜 id 相撞。
    """
    text = str(payload.get("prompt") or "")

    async def _generate(project: dict[str, Any], project_path: Path) -> int:
        context = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            project_path=project_path,
            user_id=user_id,
            audio=AudioLaneRequest(),
        )
        voice = str(payload.get("voice") or "") or context.audio.narration_voice
        _, version = await context.generator.generate_audio_async(
            text,
            resource_id,
            voice,
            speed=context.audio.narration_speed,
            task_id=task_id,
        )
        return version

    return await _run_node_task(
        project_name, resource_id, payload, resource_type=CANVAS_AUDIO_RESOURCE_TYPE, generate=_generate
    )
