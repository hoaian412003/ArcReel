"""用户在画布上额外接到起始帧联合图节点的参考图。

联合图节点（``step == "kf_gen"``）的 ``image1`` 口上接着的图片 / 资产节点，随联合图生成一起送给图像模型。
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.canvas.graph import resolve_generate_inputs, resolve_reference_labels
from lib.canvas.store import load_canvas
from lib.infra.path_safety import safe_join

logger = logging.getLogger(__name__)


def canvas_sheet_references(project_path: Path, episode: int, sheet_id: str) -> list[tuple[Path, str]]:
    """联合图 ``sheet_id`` 的画布参考图：``(绝对路径, 参考说明)``，按画布从上到下的摆放顺序。

    画布不可读、节点不在或文件缺失时返回空 / 跳过该图：画布是可选的附加输入，不应让联合图生成失败。
    """
    try:
        graph = load_canvas(project_path)
        node = next(
            (
                n
                for n in graph["nodes"]
                if n["data"].get("step") == "kf_gen"
                and n["data"].get("ep") == episode
                and n["data"].get("sheet") == sheet_id
            ),
            None,
        )
        if node is None:
            return []
        _prompt, paths = resolve_generate_inputs(graph, node["id"])
        labels = resolve_reference_labels(graph, node["id"])
    except Exception:
        logger.exception("读取联合图的画布参考图失败 sheet=%s", sheet_id)
        return []
    refs: list[tuple[Path, str]] = []
    for rel, label in zip(paths, labels, strict=True):
        try:
            path = safe_join(project_path, rel, must_exist=True, require_file=True)
        except (ValueError, FileNotFoundError, OSError):
            continue
        refs.append((path, label))
    return refs
