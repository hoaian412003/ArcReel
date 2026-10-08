"""联合图生成后的落格：切分、写格子图、更新看板。

先切分、再写盘：被取的格子里有灰色占位（模型没画出内容）时整张作废、看板原样不动，不会把一张灰图
登记成起始帧。落格把格子重置为「待审」，已有的审核不沿用——图变了，审核对象就变了。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from lib.keyframes.models import KeyframeBoard, digest
from lib.keyframes.sheet import KeyframeSheetError, SheetPrompt, split_sheet
from lib.keyframes.store import cell_image_relpath, load_board, save_board, sheet_image_relpath


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def sheet_basis(built: SheetPrompt) -> str:
    """一张联合图的生成依据：提示词与每张参考图的内容摘要。依据变化即该图的格子过期。"""
    return digest(built.prompt, [_file_digest(path) for path in built.reference_images])


#: 联合图实际比例与要求比例允许的相对偏差；超出即视为模型没按比例出图。
_RATIO_TOLERANCE = 0.05


def require_requested_ratio(sheet_image: Path, aspect_ratio: str) -> None:
    """联合图的比例须与要求一致。

    模型偶尔会忽略比例参数、出一张方图；每格再居中裁成目标比例会丢掉上下的内容（头顶、手），
    因此宁可让这次任务失败、由用户重新生成，也不把裁坏的格子登记成起始帧。
    """
    width_text, _, height_text = aspect_ratio.partition(":")
    expected = float(width_text) / float(height_text)
    with Image.open(sheet_image) as source:
        actual = source.width / source.height
    if abs(actual - expected) / expected > _RATIO_TOLERANCE:
        raise KeyframeSheetError("keyframe_sheet_wrong_ratio", f"{actual:.2f} != {expected:.2f}")


def land_sheet(
    project_path: Path,
    episode: int,
    sheet_id: str,
    sheet_image: Path,
    *,
    aspect_ratio: str,
    basis: str,
) -> list[str]:
    """把生成好的联合图切成格子图并写回看板，返回落下的格子编号。

    看板或联合图在生成期间被改走（重新规划后编号对不上）时抛 :class:`KeyframeSheetError`，
    联合图文件保留在盘上、看板不动。
    """
    board = load_board(project_path, episode)
    if board is None:
        raise KeyframeSheetError("keyframe_board_missing", str(episode))
    sheet = next((item for item in board.sheets if item.id == sheet_id), None)
    if sheet is None:
        raise KeyframeSheetError("keyframe_sheet_missing", sheet_id)

    require_requested_ratio(sheet_image, aspect_ratio)
    images = split_sheet(sheet_image, len(sheet.cells), aspect_ratio)

    landed: list[str] = []
    for cell, image in zip(sheet.cells, images, strict=True):
        relative = cell_image_relpath(episode, cell.id)
        target = project_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        cell.status = "draft"
        cell.image = relative
        cell.basis = basis
        landed.append(cell.id)
    sheet.image = sheet_image_relpath(episode, sheet_id)
    save_board(project_path, board)
    return landed


def board_cells_missing_images(board: KeyframeBoard) -> Sequence[str]:
    """还没有图的格子编号，供界面与工具提示「这张联合图还没生成」。"""
    return [cell.id for cell in board.cells() if not cell.image]
