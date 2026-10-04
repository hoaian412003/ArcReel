"""剪辑时间线路由：具名时间线的增删改（修订追加）与成片渲染（ADR 0090）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import store
from lib.edit_timeline.catalog import UnitInfo, build_catalog, diagnose, mechanical_clips
from lib.edit_timeline.durations import with_measured_durations
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.models import TRANSITIONS, Clip, Timeline
from lib.edit_timeline.render import DubSource, render_timeline
from lib.episode.episode_paths import episode_script_relpath
from lib.infra.api_errors import NotFoundError
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.infra.path_safety import safe_join
from lib.project.project_manager import get_project_manager, is_reference_video_project
from lib.script.script_skeleton import resolve_script_kind
from lib.speech.dub_render import DEFAULT_ORIGINAL_VOLUME
from lib.speech.dub_settings import project_dub_target_language
from server.services.dub_service import timeline_dub_lines

logger = logging.getLogger(__name__)

router = APIRouter()

_AUTHOR = "user"
_BASE = "/projects/{project_name}/episodes/{episode}/edit-timelines"


class CreateTimelineRequest(BaseModel):
    name: str = Field(min_length=1)
    source: Literal["mechanical", "copy"] = "mechanical"
    copy_from: str | None = None


class UpdateTimelineRequest(BaseModel):
    parent_revision: int
    clips: list[Clip]
    summary: str = "edit"
    #: 成片是否混入越南语配音；缺省沿用上一修订。
    voice_over: bool | None = None


class RollbackRequest(BaseModel):
    to_revision: int
    parent_revision: int


class RenameRequest(BaseModel):
    name: str


class _Context:
    """一次请求用到的项目现状：目录、单元目录，以及（项目启用配音时）各单元可用的配音。"""

    __slots__ = ("catalog", "dub_lines", "project_dir")

    def __init__(self, project_dir: Path, catalog: list[UnitInfo], dub_lines: dict[str, list[dict[str, Any]]]) -> None:
        self.project_dir = project_dir
        self.catalog = catalog
        self.dub_lines = dub_lines


async def _context(project_name: str, episode: int) -> _Context:
    pm = get_project_manager()
    project_dir = pm.get_project_path(project_name)
    try:
        script = pm.load_script_readonly(project_name, episode_script_relpath(episode))
    except FileNotFoundError as exc:
        raise NotFoundError("edit_timeline_episode_not_found", episode=episode) from exc
    resource_type = "reference_videos" if resolve_script_kind(script) == "video_units" else "videos"
    vm = VersionManager(project_dir)
    unit_ids = [u.unit_id for u in build_catalog(script)]
    versions = {uid: vm.get_current_version(resource_type, uid) for uid in unit_ids}
    catalog = await with_measured_durations(build_catalog(script, versions), project_dir)
    return _Context(project_dir, catalog, _available_dub_lines(pm, project_name, script, project_dir))


def _available_dub_lines(pm, project_name: str, script: dict, project_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """项目没启用越南语配音（或不是参考生视频）时没有可用配音，时间线也就没有配音开关。"""
    project = pm.load_project(project_name)
    if project_dub_target_language(project) is None or not is_reference_video_project(project):
        return {}
    return timeline_dub_lines(script, project_dir)


def _dub_sources(timeline: Timeline, ctx: _Context) -> dict[str, list[DubSource]]:
    """要混进成片的配音：只在本修订开了配音开关、且片段用到的单元才有。"""
    if not timeline.latest.voice_over:
        return {}
    used = {clip.unit_id for clip in timeline.latest.clips}
    return {
        unit_id: [
            DubSource(safe_join(ctx.project_dir, line["audio_path"]), line["start_s"], line["duration_s"])
            for line in lines
        ]
        for unit_id, lines in ctx.dub_lines.items()
        if unit_id in used
    }


def _dub_fingerprint(sources: dict[str, list[DubSource]]) -> str | None:
    """配音状态的指纹：哪条配音、放在单元内什么时刻、音频文件何时改过。无配音为 None。"""
    if not sources:
        return None
    entries = [
        [
            unit_id,
            source.path.name,
            round(source.start_s, 3),
            round(source.duration_s, 3),
            source.path.stat().st_mtime_ns,
        ]
        for unit_id, unit_sources in sorted(sources.items())
        for source in unit_sources
    ]
    return hashlib.sha1(json.dumps(entries).encode()).hexdigest()


def _final_cut_paths(project_dir: Path, episode: int, timeline_id: str) -> tuple[Path, Path]:
    stem = f"ep{episode}_{timeline_id}"
    return project_dir / "final_cuts" / f"{stem}.mp4", project_dir / "final_cuts" / f"{stem}.json"


def _basis(timeline: Timeline, ctx: _Context) -> dict[str, Any]:
    used = {c.unit_id for c in timeline.latest.clips}
    basis: dict[str, Any] = {
        "revision": timeline.latest.revision,
        "unit_versions": {u.unit_id: u.version for u in ctx.catalog if u.unit_id in used},
    }
    # 只在成片真的混了配音时记；没开配音的成片与旧的旁路文件保持原样，不会被误判过期
    fingerprint = _dub_fingerprint(_dub_sources(timeline, ctx))
    if fingerprint is not None:
        basis["voice_over"] = fingerprint
    return basis


def _final_cut(project_name: str, ctx: _Context, timeline: Timeline) -> dict[str, Any] | None:
    project_dir = ctx.project_dir
    video, sidecar = _final_cut_paths(project_dir, timeline.episode, timeline.timeline_id)
    if not video.is_file():
        return None
    rendered = load_json_or_none(sidecar)
    return {
        "url": f"/api/v1/files/{project_name}/final_cuts/{video.name}?v={int(video.stat().st_mtime)}",
        "stale": rendered != _basis(timeline, ctx),
        "revision": (rendered or {}).get("revision"),
    }


# 进行中 / 最近一次渲染的状态（进程内）；成片文件与旁路依据落盘。
_render_jobs: dict[tuple[str, str], dict[str, Any]] = {}
_render_tasks: set[asyncio.Task[None]] = set()


def _summary(t: Timeline) -> dict[str, Any]:
    return {
        "timeline_id": t.timeline_id,
        "name": t.name,
        "latest_revision": t.latest.revision,
        "clip_count": len(t.latest.clips),
        "updated_at": t.latest.created_at,
    }


def _detail(project_name: str, ctx: _Context, timeline: Timeline) -> dict[str, Any]:
    catalog = ctx.catalog
    diag = diagnose(timeline, catalog)
    return {
        "timeline": timeline.model_dump(mode="json"),
        "diagnostics": {
            "deleted_unit_clips": diag.deleted_unit_clips,
            "unused_unit_ids": diag.unused_unit_ids,
            "stale_trim_clips": diag.stale_trim_clips,
            "missing_video_clips": diag.missing_video_clips,
        },
        "units": [
            {
                "unit_id": u.unit_id,
                "video_url": f"/api/v1/files/{project_name}/{u.video_path}?v={u.version}" if u.video_path else None,
                "version": u.version,
                "duration_s": u.duration_s,
                "dub_lines": [
                    {
                        "index": line["index"],
                        "text_vi": line["text_vi"],
                        "start_s": line["start_s"],
                        "duration_s": line["duration_s"],
                        "audio_url": f"/api/v1/files/{project_name}/{line['audio_path']}",
                    }
                    for line in ctx.dub_lines.get(u.unit_id, [])
                ],
            }
            for u in catalog
        ],
        "transitions": list(TRANSITIONS),
        # 有已合成配音的单元才有配音开关；原声在混入配音时的音量
        "voice_over_available": bool(ctx.dub_lines),
        "original_volume": DEFAULT_ORIGINAL_VOLUME,
        "final_cut": _final_cut(project_name, ctx, timeline),
        "render": _render_jobs.get((project_name, timeline.timeline_id)),
    }


@router.get(_BASE)
async def list_edit_timelines(project_name: str, episode: int):
    ctx = await _context(project_name, episode)
    return {"timelines": [_summary(t) for t in store.list_timelines(ctx.project_dir, episode)]}


@router.post(_BASE)
async def create_edit_timeline(project_name: str, episode: int, req: CreateTimelineRequest):
    ctx = await _context(project_name, episode)
    if req.source == "copy":
        if not req.copy_from:
            raise EditTimelineError("copy_from is required", status_code=400)
        timeline = store.copy_timeline(ctx.project_dir, episode, req.copy_from, name=req.name, author=_AUTHOR)
    else:
        timeline = store.create_timeline(
            ctx.project_dir,
            episode,
            name=req.name,
            clips=mechanical_clips(ctx.catalog),
            author=_AUTHOR,
            summary="mechanical",
            # 项目已有配音音频时，新时间线默认带上配音；不想要可以在编辑器里关掉
            voice_over=bool(ctx.dub_lines),
        )
    return _detail(project_name, ctx, timeline)


@router.get(_BASE + "/{timeline_id}")
async def get_edit_timeline(project_name: str, episode: int, timeline_id: str):
    ctx = await _context(project_name, episode)
    return _detail(project_name, ctx, store.get_timeline(ctx.project_dir, episode, timeline_id))


@router.put(_BASE + "/{timeline_id}")
async def update_edit_timeline(project_name: str, episode: int, timeline_id: str, req: UpdateTimelineRequest):
    ctx = await _context(project_name, episode)
    timeline = store.append_revision(
        ctx.project_dir,
        episode,
        timeline_id,
        parent_revision=req.parent_revision,
        clips=req.clips,
        author=_AUTHOR,
        summary=req.summary,
        voice_over=req.voice_over,
    )
    return _detail(project_name, ctx, timeline)


@router.post(_BASE + "/{timeline_id}/rollback")
async def rollback_edit_timeline(project_name: str, episode: int, timeline_id: str, req: RollbackRequest):
    ctx = await _context(project_name, episode)
    timeline = store.rollback(
        ctx.project_dir,
        episode,
        timeline_id,
        to_revision=req.to_revision,
        parent_revision=req.parent_revision,
        author=_AUTHOR,
    )
    return _detail(project_name, ctx, timeline)


@router.patch(_BASE + "/{timeline_id}")
async def rename_edit_timeline(project_name: str, episode: int, timeline_id: str, req: RenameRequest):
    ctx = await _context(project_name, episode)
    timeline = store.rename_timeline(ctx.project_dir, episode, timeline_id, name=req.name)
    return _detail(project_name, ctx, timeline)


@router.delete(_BASE + "/{timeline_id}")
async def delete_edit_timeline(project_name: str, episode: int, timeline_id: str):
    ctx = await _context(project_name, episode)
    store.delete_timeline(ctx.project_dir, episode, timeline_id)
    _render_jobs.pop((project_name, timeline_id), None)
    return {"success": True}


async def _run_render(project_name: str, ctx: _Context, timeline: Timeline) -> None:
    key = (project_name, timeline.timeline_id)
    video, sidecar = _final_cut_paths(ctx.project_dir, timeline.episode, timeline.timeline_id)
    try:
        await render_timeline(timeline, ctx.catalog, ctx.project_dir, video, dub_sources=_dub_sources(timeline, ctx))
        atomic_write_json(sidecar, _basis(timeline, ctx))
        _render_jobs[key] = {"status": "done", "revision": timeline.latest.revision}
    except EditTimelineError as exc:
        _render_jobs[key] = {"status": "failed", "error_key": exc.key, "error_params": exc.params}
    except Exception:
        logger.exception("成片渲染失败 %s/%s", project_name, timeline.timeline_id)
        _render_jobs[key] = {"status": "failed", "error_key": "edit_timeline_render_failed", "error_params": {}}


@router.post(_BASE + "/{timeline_id}/render")
async def render_edit_timeline(project_name: str, episode: int, timeline_id: str):
    ctx = await _context(project_name, episode)
    timeline = store.get_timeline(ctx.project_dir, episode, timeline_id)
    key = (project_name, timeline_id)
    if (_render_jobs.get(key) or {}).get("status") == "running":
        raise EditTimelineError("render already running", key="edit_timeline_render_running", status_code=409)
    _render_jobs[key] = {"status": "running", "revision": timeline.latest.revision}
    task = asyncio.create_task(_run_render(project_name, ctx, timeline))
    _render_tasks.add(task)
    task.add_done_callback(_render_tasks.discard)
    return _render_jobs[key]


@router.get(_BASE + "/{timeline_id}/render")
async def get_render_status(project_name: str, episode: int, timeline_id: str):
    ctx = await _context(project_name, episode)
    timeline = store.get_timeline(ctx.project_dir, episode, timeline_id)
    return {
        "render": _render_jobs.get((project_name, timeline_id)),
        "final_cut": _final_cut(project_name, ctx, timeline),
    }
