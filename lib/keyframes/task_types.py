"""起始帧联合图任务的常量；放在无依赖的小模块里，队列、执行层与服务层共用同一份字面量。"""

from __future__ import annotations

from lib.project.resource_paths import KEYFRAME_SHEET_RESOURCE_TYPE

#: 生成一张起始帧联合图的任务类型。必然 i2i（场景图与角色图是参考图）且入队即知；
#: 不收 prompt——提示词在执行时按当前看板与项目状态装配。
KEYFRAME_SHEET_TASK_TYPE = "keyframe_sheet"

__all__ = ["KEYFRAME_SHEET_RESOURCE_TYPE", "KEYFRAME_SHEET_TASK_TYPE"]


def sheet_resource_id(episode: int, sheet_id: str) -> str:
    """联合图任务与版本管理使用的资源 id：``episode_N/S1``。"""
    return f"episode_{episode}/{sheet_id}"


def parse_sheet_resource_id(resource_id: str) -> tuple[int, str]:
    """:func:`sheet_resource_id` 的逆运算；格式不对抛 ``ValueError``。"""
    folder, _, sheet_id = resource_id.partition("/")
    prefix = "episode_"
    if not folder.startswith(prefix) or not folder[len(prefix) :].isdigit() or not sheet_id:
        raise ValueError(f"无效的起始帧联合图资源 id: {resource_id}")
    return int(folder[len(prefix) :]), sheet_id
