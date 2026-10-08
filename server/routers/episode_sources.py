"""分集源文路由：逐集上传 / 替换源文，并入队资产分析。

drama 项目不经分集规划：用户在分集界面上传每一集的原文，上传成功即登记账本条目，
再入队 ``text_asset_analysis`` 任务从新增或变化的集提取角色 / 场景 / 道具。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from lib.episode.chapter_splitter import group_into_episodes, split_chapters
from lib.episode.episode_delete import EpisodeDeleteConfirmationRequired, EpisodeDeleteError, delete_episode
from lib.episode.episode_ledger import discover_sources, parse_episode_num
from lib.episode.episode_upload import (
    EpisodeSourceItem,
    EpisodeUploadError,
    upload_episode_source,
    upload_episode_sources,
)
from lib.generation.generation_queue_client import WorkerOfflineError, enqueue_task_only
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.project.project_manager import get_project_manager
from lib.script.source_loader import (
    CorruptFileError,
    FileSizeExceededError,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
from server.auth import CurrentUser
from server.i18n import Translator
from server.tool_runtime import TEXT_ASSET_ANALYSIS_TASK_TYPE

logger = logging.getLogger(__name__)

router = APIRouter()

#: 资产分析任务的资源标识：同一项目同一时刻只有一个活动分析任务，连续上传并入同一任务（它按指纹只补分析变化的集）。
_ASSET_ANALYSIS_RESOURCE_ID = "asset-analysis"


async def _enqueue_asset_analysis(project_name: str, user_id: str) -> dict[str, Any]:
    """入队资产分析；执行器离线不让上传失败，把状态交回调用方展示并允许稍后重试。"""
    try:
        result = await enqueue_task_only(
            project_name=project_name,
            task_type=TEXT_ASSET_ANALYSIS_TASK_TYPE,
            media_type="text",
            resource_id=_ASSET_ANALYSIS_RESOURCE_ID,
            payload={},
            source="webui",
            user_id=user_id,
        )
    except WorkerOfflineError:
        return {"status": "worker_offline", "task_id": None}
    return {"status": "queued", "task_id": result["task_id"], "deduped": bool(result.get("deduped", False))}


def _extract_text(filename: str, data: bytes) -> str:
    """经 SourceLoader 把上传文件（txt / md / docx / epub / pdf）规范化为 UTF-8 文本。"""
    suffix = Path(filename).suffix
    with tempfile.TemporaryDirectory() as workdir:
        src = Path(workdir) / f"upload{suffix}"
        src.write_bytes(data)
        dst = Path(workdir) / "out"
        dst.mkdir()
        result = SourceLoader.load(src, dst, original_filename=f"episode{suffix}", on_conflict="replace")
        return result.normalized_path.read_text(encoding="utf-8")


@router.put("/projects/{project_name}/episodes/{episode}/source")
async def upload_episode(
    project_name: str,
    episode: int,
    user: CurrentUser,
    _t: Translator,
    file: UploadFile | None = File(default=None),
    content: str | None = Form(default=None),
    title: str | None = Form(default=None),
) -> dict[str, Any]:
    """上传（或替换）第 N 集源文：``file``（txt / md / docx / epub / pdf）与 ``content``（直接粘贴的文本）二选一。"""
    if (file is None) == (content is None):
        raise BadRequestError("episode_source_input_required")

    manager = get_project_manager()
    try:
        manager.get_project_path(project_name)
        if file is not None:
            filename = file.filename or ""
            data = await file.read()
            text = await asyncio.to_thread(_extract_text, filename, data)
        else:
            text = content or ""
        outcome = await asyncio.to_thread(upload_episode_source, manager, project_name, episode, text, title=title)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except EpisodeUploadError as exc:
        raise BadRequestError(f"episode_source_{exc.code}") from exc
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=400, detail=_t("source_unsupported_format", ext=exc.ext)) from exc
    except FileSizeExceededError as exc:
        raise HTTPException(
            status_code=413,
            detail=_t(
                "source_too_large",
                filename=exc.filename,
                size_mb=round(exc.size_bytes / 1024 / 1024, 1),
                limit_mb=round(exc.limit_bytes / 1024 / 1024, 1),
            ),
        ) from exc
    except SourceDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=_t("source_decode_failed", filename=exc.filename, tried=", ".join(exc.tried_encodings)),
        ) from exc
    except CorruptFileError as exc:
        raise HTTPException(
            status_code=422, detail=_t("source_corrupt_file", filename=exc.filename, reason=exc.reason)
        ) from exc

    analysis = await _enqueue_asset_analysis(project_name, user.id)
    return {
        "episode": outcome.episode,
        "title": outcome.title,
        "created": outcome.created,
        "stale": outcome.stale,
        "characters": outcome.characters,
        "analysis": analysis,
    }


@router.delete("/projects/{project_name}/episodes/{episode}")
async def delete_episode_route(project_name: str, episode: int, confirm_products: bool = False) -> dict[str, Any]:
    """删除一集：账本条目、集源文；有剧本 / 草稿 / 已登记产物时须带 ``confirm_products=true``，一并删除且不可恢复。"""
    manager = get_project_manager()
    try:
        outcome = await asyncio.to_thread(
            delete_episode, manager, project_name, episode, confirm_products=confirm_products
        )
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except EpisodeDeleteConfirmationRequired as exc:
        raise ConflictError(
            "episode_delete_confirmation_required", episode=episode, artifacts=exc.products.artifacts
        ) from exc
    except EpisodeDeleteError as exc:
        raise NotFoundError("episode_delete_not_found", episode=episode) from exc
    return {
        "episode": outcome.episode,
        "removed_files": outcome.removed_files,
        "removed_artifacts": outcome.removed_artifacts,
        "failed_paths": outcome.failed_paths,
    }


class SplitNovelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = Field(default=None, description="source/ 下的小说文件名；仅有一个源文时可省略")
    chapters_per_episode: int = Field(default=1, ge=1, le=50)
    start_episode: int = Field(default=1, ge=1)
    preview: bool = Field(default=True, description="true 只返回切分预览，不写任何东西")
    replace: bool = Field(default=False, description="目标集号已存在时是否替换（有产物的集会被标 stale）")


@router.post("/projects/{project_name}/episodes/split-novel")
async def split_novel(project_name: str, req: SplitNovelRequest, user: CurrentUser) -> dict[str, Any]:
    """按章节标题（正则）把整本小说切成分集：``preview=true`` 先看切分结果，确认后 ``preview=false`` 落盘并入队资产分析。"""
    manager = get_project_manager()
    try:
        project_dir = manager.get_project_path(project_name)
        project = await asyncio.to_thread(manager.load_project, project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    if project.get("content_mode") != "drama":
        raise BadRequestError("episode_source_unsupported_mode")

    sources = await asyncio.to_thread(discover_sources, project_dir)
    if req.source is None:
        if len(sources) != 1:
            raise BadRequestError("episode_split_source_required", count=len(sources))
        doc = sources[0]
    else:
        wanted = req.source if req.source.startswith("source/") else f"source/{req.source}"
        doc = next((d for d in sources if d.rel_path == wanted), None)
        if doc is None:
            raise NotFoundError("episode_split_source_not_found", name=req.source)

    split = split_chapters(doc.text)
    if not split.chapters:
        raise BadRequestError("episode_split_no_chapters", name=doc.rel_path)
    drafts = group_into_episodes(
        split.chapters, chapters_per_episode=req.chapters_per_episode, start_episode=req.start_episode
    )
    existing = {
        num
        for entry in project.get("episodes") or []
        if isinstance(entry, dict) and (num := parse_episode_num(entry.get("episode"))) is not None
    }
    conflicts = sorted(existing & {draft.episode for draft in drafts})
    preview = {
        "source": doc.rel_path,
        "pattern": split.family,
        "chapter_count": len(split.chapters),
        "preface_chars": split.preface_chars,
        "warnings": split.warnings,
        "conflicts": conflicts,
        "episodes": [
            {"episode": d.episode, "title": d.title, "chars": d.chars, "chapters": list(d.chapters)} for d in drafts
        ],
    }
    if req.preview:
        return {**preview, "created": False, "analysis": None}
    if conflicts and not req.replace:
        raise ConflictError("episode_split_conflict", episodes=", ".join(str(n) for n in conflicts))

    try:
        await asyncio.to_thread(
            upload_episode_sources,
            manager,
            project_name,
            [EpisodeSourceItem(d.episode, d.text, d.title) for d in drafts],
        )
    except EpisodeUploadError as exc:
        raise BadRequestError(f"episode_source_{exc.code}") from exc
    analysis = await _enqueue_asset_analysis(project_name, user.id)
    return {**preview, "created": True, "analysis": analysis}


@router.post("/projects/{project_name}/assets/analyze")
async def analyze_assets(project_name: str, user: CurrentUser) -> dict[str, Any]:
    """重新入队资产分析（上一次失败、执行器离线或源文有变动后的手动重试）。"""
    try:
        get_project_manager().get_project_path(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return await _enqueue_asset_analysis(project_name, user.id)


__all__ = ["router"]
