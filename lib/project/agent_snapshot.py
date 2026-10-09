"""Agent 每一轮开始前的项目文本快照：对比本轮改了什么，并能整体回退。

只收项目里的文本文件（``project.json``、画布、剧本、草稿等，单文件不超过 ``MAX_FILE_BYTES``），
媒体文件不进快照。快照是 ``<项目>/.agent_snapshots/<id>.zip``，只保留最近 ``KEEP_SNAPSHOTS`` 份。
回退把快照里的文件写回、并删掉快照之后新增的文本文件；同一时段内队列 worker 写入的文本文件也会被一并回退。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = ".agent_snapshots"
KEEP_SNAPSHOTS = 10
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_SUFFIXES = frozenset({".json", ".md", ".txt", ".yaml", ".yml"})
_ID_RE = re.compile(r"^[0-9]{14}-[0-9a-f]{8}$")
_META = "__meta__.json"


class SnapshotNotFoundError(LookupError):
    pass


def _is_tracked(rel: Path) -> bool:
    if any(part.startswith(".") for part in rel.parts):
        return False
    return rel.suffix.lower() in TEXT_SUFFIXES


def _scan(project_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            path = Path(root) / name
            rel = path.relative_to(project_dir)
            if not _is_tracked(rel) or path.is_symlink():
                continue
            with suppress(OSError):
                if path.stat().st_size <= MAX_FILE_BYTES:
                    found[rel.as_posix()] = path
    return found


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_path(project_dir: Path, snapshot_id: str) -> Path:
    if not _ID_RE.match(snapshot_id):
        raise SnapshotNotFoundError(snapshot_id)
    path = project_dir / SNAPSHOT_DIR / f"{snapshot_id}.zip"
    if not path.is_file():
        raise SnapshotNotFoundError(snapshot_id)
    return path


def take_snapshot(project_dir: Path, *, label: str = "") -> str:
    """给项目文本文件拍一份快照，返回快照 id。"""
    directory = project_dir / SNAPSHOT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    snapshot_id = f"{time.strftime('%Y%m%d%H%M%S')}-{hashlib.sha256(os.urandom(8)).hexdigest()[:8]}"
    target = directory / f"{snapshot_id}.zip"
    staging = directory / f"{snapshot_id}.zip.part"
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel, path in _scan(project_dir).items():
            try:
                data = path.read_bytes()
            except OSError:
                continue
            archive.writestr(rel, data)
            hashes[rel] = _digest(data)
        archive.writestr(_META, json.dumps({"label": label, "created_at": time.time(), "files": hashes}))
    staging.replace(target)
    _prune(directory)
    return snapshot_id


def _prune(directory: Path) -> None:
    snapshots = sorted(directory.glob("*.zip"))
    for old in snapshots[:-KEEP_SNAPSHOTS]:
        with suppress(OSError):
            old.unlink()


def _meta(project_dir: Path, snapshot_id: str) -> dict[str, Any]:
    with zipfile.ZipFile(_snapshot_path(project_dir, snapshot_id)) as archive:
        meta: dict[str, Any] = json.loads(archive.read(_META))
    return meta


def diff_snapshot(project_dir: Path, snapshot_id: str) -> dict[str, list[str]]:
    """快照之后新增 / 修改 / 删除的文本文件（相对项目目录的路径）。"""
    before: dict[str, str] = _meta(project_dir, snapshot_id)["files"]
    now: dict[str, str] = {}
    for rel, path in _scan(project_dir).items():
        try:
            now[rel] = _digest(path.read_bytes())
        except OSError:
            continue
    return {
        "added": sorted(set(now) - set(before)),
        "modified": sorted(rel for rel in set(now) & set(before) if now[rel] != before[rel]),
        "removed": sorted(set(before) - set(now)),
    }


def discard_snapshot(project_dir: Path, snapshot_id: str) -> None:
    with suppress(SnapshotNotFoundError, OSError):
        _snapshot_path(project_dir, snapshot_id).unlink()


def list_snapshots(project_dir: Path) -> list[dict[str, Any]]:
    directory = project_dir / SNAPSHOT_DIR
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.zip"), reverse=True):
        with suppress(Exception):
            meta = _meta(project_dir, path.stem)
            items.append({"id": path.stem, "label": meta.get("label", ""), "created_at": meta["created_at"]})
    return items


def restore_snapshot(project_dir: Path, snapshot_id: str) -> dict[str, list[str]]:
    """把项目文本文件回退到快照时的状态，返回被改动的文件清单。"""
    changes = diff_snapshot(project_dir, snapshot_id)
    root = project_dir.resolve()
    with zipfile.ZipFile(_snapshot_path(project_dir, snapshot_id)) as archive:
        for rel in [*changes["modified"], *changes["removed"]]:
            destination = (project_dir / rel).resolve()
            if not destination.is_relative_to(root):
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(rel))
    for rel in changes["added"]:
        destination = (project_dir / rel).resolve()
        if destination.is_relative_to(root):
            with suppress(OSError):
                destination.unlink()
    return changes
