"""剪辑时间线的存储与修订并发控制。

每条时间线一个 JSON：``<项目目录>/timelines/<集>/<timeline_id>.json``，修订只追加。
所有写入在项目元数据锁内完成读取—校验—原子写入。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from lib.artifacts.formal_write import project_metadata_lock
from lib.edit_timeline.errors import InvalidTimelineError, RevisionConflictError, TimelineNotFoundError
from lib.edit_timeline.models import Clip, Revision, Timeline
from lib.infra.json_io import atomic_write_json, load_json

TIMELINES_DIRNAME = "timelines"
_TIMELINE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _episode_dir(project_dir: Path, episode: int) -> Path:
    return Path(project_dir) / TIMELINES_DIRNAME / str(int(episode))


def _timeline_path(project_dir: Path, episode: int, timeline_id: str) -> Path:
    if not _TIMELINE_ID_PATTERN.fullmatch(timeline_id):
        raise TimelineNotFoundError(timeline_id)
    return _episode_dir(project_dir, episode) / f"{timeline_id}.json"


def _write(project_dir: Path, timeline: Timeline) -> None:
    path = _timeline_path(project_dir, timeline.episode, timeline.timeline_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, timeline.model_dump(mode="json"))


def _validated_clips(clips: Sequence[Clip | dict]) -> list[Clip]:
    try:
        return [clip if isinstance(clip, Clip) else Clip.model_validate(clip) for clip in clips]
    except ValidationError as exc:
        raise InvalidTimelineError(str(exc)) from exc


def new_clip_id() -> str:
    return uuid.uuid4().hex[:12]


def get_timeline(project_dir: Path, episode: int, timeline_id: str) -> Timeline:
    path = _timeline_path(project_dir, episode, timeline_id)
    try:
        return Timeline.model_validate(load_json(path))
    except FileNotFoundError as exc:
        raise TimelineNotFoundError(timeline_id) from exc


def list_timelines(project_dir: Path, episode: int) -> list[Timeline]:
    directory = _episode_dir(project_dir, episode)
    if not directory.is_dir():
        return []
    timelines = [Timeline.model_validate(load_json(path)) for path in sorted(directory.glob("*.json"))]
    return sorted(timelines, key=lambda t: t.revisions[0].created_at)


def create_timeline(
    project_dir: Path,
    episode: int,
    *,
    name: str,
    clips: Sequence[Clip | dict],
    author: str,
    summary: str = "create",
) -> Timeline:
    """以给定片段序列新建一条时间线（首个修订）。"""
    timeline = Timeline(
        timeline_id=uuid.uuid4().hex[:12],
        episode=episode,
        name=name,
        revisions=[
            Revision(
                revision=1,
                parent_revision=None,
                author=author,
                summary=summary,
                created_at=_now(),
                clips=_validated_clips(clips),
            )
        ],
    )
    with project_metadata_lock(project_dir):
        _write(project_dir, timeline)
    return timeline


def copy_timeline(project_dir: Path, episode: int, source_id: str, *, name: str, author: str) -> Timeline:
    """复制已有时间线的最新修订为一条新时间线。"""
    source = get_timeline(project_dir, episode, source_id)
    return create_timeline(
        project_dir,
        episode,
        name=name,
        clips=source.latest.clips,
        author=author,
        summary=f"copy of {source.name}",
    )


def append_revision(
    project_dir: Path,
    episode: int,
    timeline_id: str,
    *,
    parent_revision: int,
    clips: Sequence[Clip | dict],
    author: str,
    summary: str,
) -> Timeline:
    """追加新修订；``parent_revision`` 不是最新时拒绝写入。"""
    new_clips = _validated_clips(clips)
    with project_metadata_lock(project_dir):
        timeline = get_timeline(project_dir, episode, timeline_id)
        latest = timeline.latest.revision
        if parent_revision != latest:
            raise RevisionConflictError(expected=parent_revision, latest=latest)
        timeline.revisions.append(
            Revision(
                revision=latest + 1,
                parent_revision=latest,
                author=author,
                summary=summary,
                created_at=_now(),
                clips=new_clips,
            )
        )
        _write(project_dir, timeline)
    return timeline


def rollback(
    project_dir: Path,
    episode: int,
    timeline_id: str,
    *,
    to_revision: int,
    parent_revision: int,
    author: str,
) -> Timeline:
    """基于旧修订追加新修订。"""
    timeline = get_timeline(project_dir, episode, timeline_id)
    target = next((r for r in timeline.revisions if r.revision == to_revision), None)
    if target is None:
        raise InvalidTimelineError(f"unknown revision: {to_revision}", revision=to_revision)
    return append_revision(
        project_dir,
        episode,
        timeline_id,
        parent_revision=parent_revision,
        clips=target.clips,
        author=author,
        summary=f"rollback to revision {to_revision}",
    )


def rename_timeline(project_dir: Path, episode: int, timeline_id: str, *, name: str) -> Timeline:
    """改显示名（不产生修订）。"""
    if not name.strip():
        raise InvalidTimelineError("name must not be empty")
    with project_metadata_lock(project_dir):
        timeline = get_timeline(project_dir, episode, timeline_id)
        timeline.name = name.strip()
        _write(project_dir, timeline)
    return timeline


def delete_timeline(project_dir: Path, episode: int, timeline_id: str) -> None:
    path = _timeline_path(project_dir, episode, timeline_id)
    with project_metadata_lock(project_dir):
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise TimelineNotFoundError(timeline_id) from exc
