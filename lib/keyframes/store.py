"""起始帧看板与格子图的落盘位置与读写。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from lib.episode.episode_paths import episode_drafts_dir
from lib.keyframes.models import KeyframeBoard

BOARD_FILENAME = "keyframes.json"


def board_path(project_path: Path, episode: int) -> Path:
    """看板文件 ``{project}/drafts/episode_N/keyframes.json``。"""
    return episode_drafts_dir(project_path, episode) / BOARD_FILENAME


def keyframes_dir_relpath(episode: int) -> str:
    """该集格子图目录相对项目根的路径 ``keyframes/episode_N``。"""
    return f"keyframes/episode_{episode}"


def sheet_image_relpath(episode: int, sheet_id: str) -> str:
    return f"{keyframes_dir_relpath(episode)}/{sheet_id}.png"


def cell_image_relpath(episode: int, cell_id: str) -> str:
    return f"{keyframes_dir_relpath(episode)}/{cell_id}.png"


def load_board(project_path: Path, episode: int) -> KeyframeBoard | None:
    """读看板；文件不存在返回 None，内容无法解析时抛 ``ValueError``。"""
    path = board_path(project_path, episode)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return KeyframeBoard.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"起始帧看板无法解析（{path.name}）：{exc}") from exc


def save_board(project_path: Path, board: KeyframeBoard) -> Path:
    """原子写入看板（临时文件 + 替换），避免读到写了一半的文件。"""
    path = board_path(project_path, board.episode)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(board.model_dump(mode="json"), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".keyframes.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
