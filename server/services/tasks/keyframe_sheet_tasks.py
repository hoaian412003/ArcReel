"""起始帧联合图的生成任务执行层（见 ``docs/adr/0096``）。

一次任务生成看板里的一张联合图：提示词与参考图在执行时按当前看板与项目状态装配（入队不收 prompt），
图像模型以 i2i 出一张 2×2 联合图，经 ``MediaGenerator`` 记账与版本管理落盘，随后切成格子图并写回看板。

起始帧不是正式产物：不进产物清单、不认领输入，所以这里不走 ``run_formal_image_task``。格子图的取用由
视频请求侧按文件存在性判断（``lib.keyframes.consume``）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.canvas.keyframe_refs import canvas_sheet_references
from lib.db.base import DEFAULT_USER_ID
from lib.keyframes.landing import land_sheet, require_requested_ratio, sheet_basis
from lib.keyframes.models import KeyframeSheet
from lib.keyframes.sheet import KeyframeSheetError, SheetPrompt, build_sheet_prompt
from lib.keyframes.store import load_board
from lib.keyframes.task_types import KEYFRAME_SHEET_RESOURCE_TYPE, parse_sheet_resource_id
from lib.project.project_manager import get_project_manager
from server.services.tasks.generation_context import ImageLaneRequest, resolve_generation_context

#: 图像后端没有声明分辨率时的联合图档位：2K，每格约 1024×576，够作参考图。
SHEET_FALLBACK_RESOLUTION = "2K"

#: 联合图比例与要求不符（模型出了方图）时最多生成几次；每次都是一次付费调用。
MAX_RATIO_ATTEMPTS = 3


@dataclass(frozen=True)
class _Prepared:
    project: dict[str, Any]
    project_path: Path
    sheet: KeyframeSheet
    built: SheetPrompt
    basis: str


def _prepare(project_name: str, episode: int, sheet_id: str) -> _Prepared:
    pm = get_project_manager()
    project = pm.load_project(project_name)
    project_path = pm.get_project_path(project_name)
    board = load_board(project_path, episode)
    if board is None:
        raise ValueError(f"第 {episode} 集还没有起始帧看板，先提交规划")
    sheet = next((item for item in board.sheets if item.id == sheet_id), None)
    if sheet is None:
        raise ValueError(f"看板里没有联合图 {sheet_id}（规划可能已被重新提交）")
    built = build_sheet_prompt(
        sheet,
        project=project,
        project_path=project_path,
        extra_references=canvas_sheet_references(project_path, episode, sheet_id),
    )
    return _Prepared(project, project_path, sheet, built, sheet_basis(built))


async def execute_keyframe_sheet_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """生成一张联合图并落格。``resource_id`` 是 ``episode_N/S1``。

    素材缺失（场景图、角色图）或参考图数量超出图像后端上限时在付费之前失败，不提交任何请求。
    """
    episode, sheet_id = parse_sheet_resource_id(resource_id)
    prepared = await asyncio.to_thread(_prepare, project_name, episode, sheet_id)
    context = await resolve_generation_context(
        project_name,
        payload,
        project=prepared.project,
        project_path=prepared.project_path,
        user_id=user_id,
        image=ImageLaneRequest(generation_type="i2i"),
    )

    references = list(prepared.built.reference_images)
    limit = context.image.max_reference_images
    if limit and len(references) > limit:
        raise ValueError(
            f"联合图 {sheet_id} 需要 {len(references)} 张参考图，超出图像模型上限 {limit}；"
            "出镜角色太多，请把这张联合图的格子拆到不同场景或减少出镜角色后重新规划"
        )

    attempt = 0
    while True:
        attempt += 1
        output_path, version = await context.generator.generate_image_async(
            prompt=prepared.built.prompt,
            resource_type=KEYFRAME_SHEET_RESOURCE_TYPE,
            resource_id=resource_id,
            reference_images=references,
            aspect_ratio=prepared.built.aspect_ratio,
            image_size=context.image.resolution or SHEET_FALLBACK_RESOLUTION,
            task_id=task_id,
        )
        try:
            await asyncio.to_thread(require_requested_ratio, Path(output_path), prepared.built.aspect_ratio)
            break
        except KeyframeSheetError as exc:
            if exc.code != "keyframe_sheet_wrong_ratio" or attempt == MAX_RATIO_ATTEMPTS:
                raise

    landed = await asyncio.to_thread(
        land_sheet,
        prepared.project_path,
        episode,
        sheet_id,
        Path(output_path),
        aspect_ratio=prepared.built.aspect_ratio,
        basis=prepared.basis,
    )
    return {
        "file_path": f"keyframes/episode_{episode}/{sheet_id}.png",
        "resource_type": KEYFRAME_SHEET_RESOURCE_TYPE,
        "resource_id": resource_id,
        "version": version,
        "attempts": attempt,
        "cells": landed,
    }


__all__ = ["SHEET_FALLBACK_RESOLUTION", "execute_keyframe_sheet_task"]
