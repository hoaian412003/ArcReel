"""画布图的落盘：``<项目>/canvas/canvas.json``，读-改-写统一走项目文件锁。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib.canvas.graph import empty_graph, normalize_legacy
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.project.project_manager import ProjectManager

CANVAS_DIR = "canvas"
CANVAS_FILE = "canvas.json"
CANVAS_UPLOAD_DIR = f"{CANVAS_DIR}/uploads"


def canvas_path(project_path: Path) -> Path:
    return project_path / CANVAS_DIR / CANVAS_FILE


def _load(path: Path) -> dict[str, Any]:
    raw = load_json_or_none(path)
    if not isinstance(raw, dict):
        return empty_graph()
    graph = empty_graph()
    graph["revision"] = int(raw.get("revision") or 0)
    graph["nodes"] = [n for n in raw.get("nodes", []) if isinstance(n, dict)]
    graph["edges"] = [e for e in raw.get("edges", []) if isinstance(e, dict)]
    templates = raw.get("templates")
    graph["templates"] = [t for t in templates if isinstance(t, dict)] if isinstance(templates, list) else []
    overrides = raw.get("overrides")
    graph["overrides"] = dict(overrides) if isinstance(overrides, dict) else {}
    notes = raw.get("notes")
    graph["notes"] = {k: v for k, v in notes.items() if isinstance(v, str)} if isinstance(notes, dict) else {}
    meta = raw.get("meta")
    graph["meta"] = dict(meta) if isinstance(meta, dict) else {}
    normalize_legacy(graph)
    return graph


def load_canvas(project_path: Path) -> dict[str, Any]:
    return _load(canvas_path(project_path))


def mutate_canvas[T](
    pm: ProjectManager, project_path: Path, fn: Callable[[dict[str, Any]], T]
) -> tuple[T, dict[str, Any]]:
    """在文件锁内读图、交给 ``fn`` 改、递增 revision 后原子写回。

    ``fn`` 抛异常则不落盘。返回 ``(fn 的返回值, 写回后的图)``。同步函数，异步调用方经 ``asyncio.to_thread``。
    """
    path = canvas_path(project_path)
    with pm.file_lock(path):
        graph = _load(path)
        result = fn(graph)
        graph["revision"] += 1
        atomic_write_json(path, graph)
    return result, graph
