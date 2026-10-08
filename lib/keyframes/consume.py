"""视频请求侧如何取用起始帧：查出单元已审核的格子图，以及渲染期补写的那一句首帧说明。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from lib.keyframes.models import KeyframeCell
from lib.keyframes.store import load_board

#: 起始帧在参考图列表里的保留名。不是资产名：它不进 ``project.json``，也不会被正文 ``@`` 引用，
#: 只在 ``<Picture @[…]>`` 占位里作为序位的查找键。
KEYFRAME_REFERENCE_NAME = "__keyframe__"

#: 渲染期补写的两句。首帧用 H3 指南里的写法（standalone ``<Picture N>``：the shot begins from …）。
_DEFINITION_LINE = "<Picture @[{name}]> is the opening frame of this unit."
_SHOT_ONE_PREFIX = "The shot begins from <Picture @[{name}]>, which fixes the opening framing, seating and wardrobe. "

_UNIT_EPISODE = re.compile(r"^E(\d+)U")


def episode_of_unit(unit_id: str) -> int | None:
    """视频单元编号 ``E{集}U{序}`` 里的集号；不是这个形式时返回 None。"""
    match = _UNIT_EPISODE.match(unit_id)
    return int(match.group(1)) if match else None


def approved_keyframe(project_path: Path, unit: Mapping[str, object]) -> tuple[KeyframeCell, Path] | None:
    """单元的已审核起始帧：``(格子, 图片绝对路径)``。

    看板不存在、单元不在任何格子里、格子未审核或图片不在盘上，都视为没有起始帧——起始帧是可选的
    增强，缺失时单元按没有它的路径生成，不阻断。看板无法解析同样当作没有，由看板自己的读取入口报错。
    """
    unit_id = unit.get("unit_id")
    if not isinstance(unit_id, str):
        return None
    episode = episode_of_unit(unit_id)
    if episode is None:
        return None
    try:
        board = load_board(project_path, episode)
    except ValueError:
        return None
    if board is None:
        return None
    cell = board.cell_for_unit(unit_id)
    if cell is None or cell.status != "approved" or not cell.image:
        return None
    path = project_path / cell.image
    if not path.is_file():
        return None
    return cell, path


def with_opening_frame(sections: dict[str, str], *, name: str = KEYFRAME_REFERENCE_NAME) -> dict[str, str]:
    """在六段里补写首帧说明：``subject_definitions`` 末尾一行，``[Shot 1]`` 开头一句。

    模型写的内容原样保留，补写是确定性的，不靠提示词编写的自觉。找不到 ``[Shot 1]`` 时只补定义行
    （这样的载荷本来就过不了结构校验）。
    """
    patched = dict(sections)
    definition = _DEFINITION_LINE.format(name=name)
    patched["subject_definitions"] = f"{sections['subject_definitions'].rstrip()}\n{definition}"
    detailed = sections["detailed_description"]
    marker = "[Shot 1]"
    index = detailed.find(marker)
    if index != -1:
        cut = index + len(marker)
        patched["detailed_description"] = (
            f"{detailed[:cut]} {_SHOT_ONE_PREFIX.format(name=name)}{detailed[cut:].lstrip()}"
        )
    return patched
