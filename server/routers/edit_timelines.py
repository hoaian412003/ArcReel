"""剪辑时间线路由：具名时间线的增删改（修订追加）与成片渲染（ADR 0090）。"""

from __future__ import annotations

import asyncio
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
from lib.edit_timeline.render import render_timeline
from lib.episode.episode_paths import episode_script_relpath
from lib.infra.api_errors import NotFoundError
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.project.project_manager import get_project_manager
from lib.script.script_skeleton import resolve_script_kind

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


class RollbackRequest(BaseModel):
    to_revision: int
    parent_revision: int


class RenameRequest(BaseModel):
    name: str


async def _context(project_name: str, episode: int) -> tuple[Path, list[UnitInfo]]:
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
    return project_dir, await with_measured_durations(build_catalog(script, versions), project_dir)


def _final_cut_paths(project_dir: Path, episode: int, timeline_id: str) -> tuple[Path, Path]:
    stem = f"ep{episode}_{timeline_id}"
    return project_dir / "final_cuts" / f"{stem}.mp4", project_dir / "final_cuts" / f"{stem}.json"


def _basis(timeline: Timeline, catalog: list[UnitInfo]) -> dict[str, Any]:
    used = {c.unit_id for c in timeline.latest.clips}
    return {
        "revision": timeline.latest.revision,
        "unit_versions": {u.unit_id: u.version for u in catalog if u.unit_id in used},
    }


def _final_cut(
    project_name: str, project_dir: Path, timeline: Timeline, catalog: list[UnitInfo]
) -> dict[str, Any] | None:
    video, sidecar = _final_cut_paths(project_dir, timeline.episode, timeline.timeline_id)
    if not video.is_file():
        return None
    rendered = load_json_or_none(sidecar)
    return {
        "url": f"/api/v1/files/{project_name}/final_cuts/{video.name}?v={int(video.stat().st_mtime)}",
        "stale": rendered != _basis(timeline, catalog),
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


def _detail(project_name: str, project_dir: Path, timeline: Timeline, catalog: list[UnitInfo]) -> dict[str, Any]:
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
            }
            for u in catalog
        ],
        "transitions": list(TRANSITIONS),
        "final_cut": _final_cut(project_name, project_dir, timeline, catalog),
        "render": _render_jobs.get((project_name, timeline.timeline_id)),
    }


@router.get(_BASE)
async def list_edit_timelines(project_name: str, episode: int):
    project_dir, _ = await _context(project_name, episode)
    return {"timelines": [_summary(t) for t in store.list_timelines(project_dir, episode)]}


@router.post(_BASE)
async def create_edit_timeline(project_name: str, episode: int, req: CreateTimelineRequest):
    project_dir, catalog = await _context(project_name, episode)
    if req.source == "copy":
        if not req.copy_from:
            raise EditTimelineError("copy_from is required", status_code=400)
        timeline = store.copy_timeline(project_dir, episode, req.copy_from, name=req.name, author=_AUTHOR)
    else:
        timeline = store.create_timeline(
            project_dir, episode, name=req.name, clips=mechanical_clips(catalog), author=_AUTHOR, summary="mechanical"
        )
    return _detail(project_name, project_dir, timeline, catalog)


@router.get(_BASE + "/{timeline_id}")
async def get_edit_timeline(project_name: str, episode: int, timeline_id: str):
    project_dir, catalog = await _context(project_name, episode)
    return _detail(project_name, project_dir, store.get_timeline(project_dir, episode, timeline_id), catalog)


@router.put(_BASE + "/{timeline_id}")
async def update_edit_timeline(project_name: str, episode: int, timeline_id: str, req: UpdateTimelineRequest):
    project_dir, catalog = await _context(project_name, episode)
    timeline = store.append_revision(
        project_dir,
        episode,
        timeline_id,
        parent_revision=req.parent_revision,
        clips=req.clips,
        author=_AUTHOR,
        summary=req.summary,
    )
    return _detail(project_name, project_dir, timeline, catalog)


@router.post(_BASE + "/{timeline_id}/rollback")
async def rollback_edit_timeline(project_name: str, episode: int, timeline_id: str, req: RollbackRequest):
    project_dir, catalog = await _context(project_name, episode)
    timeline = store.rollback(
        project_dir,
        episode,
        timeline_id,
        to_revision=req.to_revision,
        parent_revision=req.parent_revision,
        author=_AUTHOR,
    )
    return _detail(project_name, project_dir, timeline, catalog)


@router.patch(_BASE + "/{timeline_id}")
async def rename_edit_timeline(project_name: str, episode: int, timeline_id: str, req: RenameRequest):
    project_dir, catalog = await _context(project_name, episode)
    timeline = store.rename_timeline(project_dir, episode, timeline_id, name=req.name)
    return _detail(project_name, project_dir, timeline, catalog)


@router.delete(_BASE + "/{timeline_id}")
async def delete_edit_timeline(project_name: str, episode: int, timeline_id: str):
    project_dir, _ = await _context(project_name, episode)
    store.delete_timeline(project_dir, episode, timeline_id)
    _render_jobs.pop((project_name, timeline_id), None)
    return {"success": True}


async def _run_render(project_name: str, project_dir: Path, timeline: Timeline, catalog: list[UnitInfo]) -> None:
    key = (project_name, timeline.timeline_id)
    video, sidecar = _final_cut_paths(project_dir, timeline.episode, timeline.timeline_id)
    try:
        await render_timeline(timeline, catalog, project_dir, video)
        atomic_write_json(sidecar, _basis(timeline, catalog))
        _render_jobs[key] = {"status": "done", "revision": timeline.latest.revision}
    except EditTimelineError as exc:
        _render_jobs[key] = {"status": "failed", "error_key": exc.key, "error_params": exc.params}
    except Exception:
        logger.exception("成片渲染失败 %s/%s", project_name, timeline.timeline_id)
        _render_jobs[key] = {"status": "failed", "error_key": "edit_timeline_render_failed", "error_params": {}}


@router.post(_BASE + "/{timeline_id}/render")
async def render_edit_timeline(project_name: str, episode: int, timeline_id: str):
    project_dir, catalog = await _context(project_name, episode)
    timeline = store.get_timeline(project_dir, episode, timeline_id)
    key = (project_name, timeline_id)
    if (_render_jobs.get(key) or {}).get("status") == "running":
        raise EditTimelineError("render already running", key="edit_timeline_render_running", status_code=409)
    _render_jobs[key] = {"status": "running", "revision": timeline.latest.revision}
    task = asyncio.create_task(_run_render(project_name, project_dir, timeline, catalog))
    _render_tasks.add(task)
    task.add_done_callback(_render_tasks.discard)
    return _render_jobs[key]


@router.get(_BASE + "/{timeline_id}/render")
async def get_render_status(project_name: str, episode: int, timeline_id: str):
    project_dir, catalog = await _context(project_name, episode)
    timeline = store.get_timeline(project_dir, episode, timeline_id)
    return {
        "render": _render_jobs.get((project_name, timeline_id)),
        "final_cut": _final_cut(project_name, project_dir, timeline, catalog),
    }
