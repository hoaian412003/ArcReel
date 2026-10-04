"""导演审阅（人工单元）：适用性、导演数据指纹与确认状态派生。

导演步骤把集级 ``director_plan`` 与逐条目 ``direction`` 写进正式脚本；导演审阅是随后的人工决定。
真值只存决定记录于 project.json ``episodes[i].direction_review``：
``{"fingerprint", "decision", "decided_at", "note", "entry_ids"}``。状态按读时派生——记录里的指纹与
脚本里现有导演数据的指纹比对，导演数据一变（重新导演、手改）指纹即漂移，自动回到待审阅，无需写入方各自上报。

适用范围：旁白 / 剧情项目（分镜图生视频与参考生视频两条路线），且该集仍有待编写条目（提示词尚未编写）。
条目已全部编写的存量集不走这一步（``not_applicable``），不被新步骤阻塞。广告不适用。

导演说明的存放：分镜路线写在条目的 ``direction`` 对象里；参考生视频沿用单元上扁平的导演字段
（``brief`` / ``shots`` / ``performance`` …），与解析预览里的「导演指南」编辑同一份数据。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from lib.infra.content_digest import canonical_json_digest
from lib.project.project_manager import find_episode, is_reference_video_project
from lib.script.script_models import (
    PENDING_AUTHORING_FIELD,
    REFERENCE_DIRECTION_OUTPUT_FIELDS,
    REFERENCE_UNIT_DIRECTION_FIELDS,
)
from lib.script.script_skeleton import SKELETONS

ReviewStatus = Literal["not_applicable", "no_direction", "pending_review", "approved", "changes_requested"]

#: 决定记录在 episode 条目上的字段名。
REVIEW_FIELD = "direction_review"

DECISION_APPROVE = "approve"
DECISION_CHANGES = "changes"
DECISION_REDO = "redo"
DECISIONS = (DECISION_APPROVE, DECISION_CHANGES, DECISION_REDO)

#: 分镜路线下导演步骤适用的创作类型 → 条目骨架种类；参考生视频路线一律是 ``video_units``。
_KIND_BY_CONTENT_MODE = {"narration": "segments", "drama": "scenes"}
REFERENCE_KIND = "video_units"

#: 参考生视频单元上属于导演说明的扁平字段（存量字段与导演步骤的新字段的并集）。
_REFERENCE_DIRECTION_FIELDS: tuple[str, ...] = tuple(
    dict.fromkeys((*REFERENCE_UNIT_DIRECTION_FIELDS, *REFERENCE_DIRECTION_OUTPUT_FIELDS))
)


def applies_to_project(project: Mapping[str, Any]) -> bool:
    """导演步骤是否适用于该项目：旁白 / 剧情项目，分镜与参考生视频两条路线都适用。"""
    return project.get("content_mode") in _KIND_BY_CONTENT_MODE


def entry_kind(project: Mapping[str, Any]) -> str | None:
    """适用项目的条目骨架种类（``segments`` / ``scenes`` / ``video_units``）；不适用时 ``None``。"""
    if not applies_to_project(project):
        return None
    if is_reference_video_project(project):
        return REFERENCE_KIND
    return _KIND_BY_CONTENT_MODE[str(project.get("content_mode"))]


def entry_direction(item: Mapping[str, Any], kind: str) -> dict[str, Any] | None:
    """条目的导演说明；没有时 ``None``。

    参考生视频单元把扁平导演字段收成一个对象：写了 ``brief`` 且有镜头表（``shots``，或存量的 ``beats``）
    才算导演过。
    """
    if kind != REFERENCE_KIND:
        direction = item.get("direction")
        return dict(direction) if isinstance(direction, Mapping) else None
    brief = item.get("brief")
    if not (isinstance(brief, str) and brief.strip()) or not (item.get("shots") or item.get("beats")):
        return None
    return {field: item[field] for field in _REFERENCE_DIRECTION_FIELDS if field in item}


def script_entries(script: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    raw = script.get(kind)
    return [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]


def has_pending_authoring(script: Mapping[str, Any], kind: str) -> bool:
    return any(item.get(PENDING_AUTHORING_FIELD) is True for item in script_entries(script, kind))


def direction_complete(script: Mapping[str, Any], kind: str) -> bool:
    """脚本有集级导演规划，且每个条目都带导演说明。"""
    entries = script_entries(script, kind)
    return (
        isinstance(script.get("director_plan"), Mapping)
        and bool(entries)
        and all(entry_direction(item, kind) is not None for item in entries)
    )


def undirected_entry_ids(script: Mapping[str, Any], kind: str) -> list[str]:
    """尚无导演说明的条目 id，按剧本顺序。"""
    id_field = SKELETONS[kind].id_field
    return [
        str(item[id_field])
        for item in script_entries(script, kind)
        if isinstance(item.get(id_field), str) and entry_direction(item, kind) is None
    ]


def direction_fingerprint(script: Mapping[str, Any], kind: str) -> str | None:
    """导演数据的规范化摘要（集级规划 + 逐条目说明）；脚本没有任何导演数据时 ``None``。"""
    plan = script.get("director_plan")
    id_field = SKELETONS[kind].id_field
    directions = [
        {"id": item.get(id_field), "direction": direction}
        for item in script_entries(script, kind)
        if (direction := entry_direction(item, kind)) is not None
    ]
    if not isinstance(plan, Mapping) and not directions:
        return None
    return canonical_json_digest({"director_plan": plan if isinstance(plan, Mapping) else None, "entries": directions})


def stored_review(project: Mapping[str, Any], episode: int) -> dict[str, Any]:
    """该集已存的决定记录，缺失或形状坏时返回空 dict。"""
    entry = find_episode(project, episode)
    review = entry.get(REVIEW_FIELD) if entry else None
    return review if isinstance(review, dict) else {}


def review_status(project: Mapping[str, Any], script: Mapping[str, Any] | None, episode: int) -> ReviewStatus:
    """派生该集导演审阅状态。

    - 项目不适用，或脚本没有完整导演数据且已无待编写条目（存量）→ ``not_applicable``；
    - 脚本缺失，或没有完整导演数据而仍有待编写条目 → ``no_direction``；
    - 有完整导演数据：记录指纹与现有数据一致时按决定给 ``approved`` / ``changes_requested``，
      不一致（含无记录）→ ``pending_review``。
    """
    kind = entry_kind(project)
    if kind is None:
        return "not_applicable"
    if script is None:
        return "no_direction"
    if not direction_complete(script, kind):
        return "no_direction" if has_pending_authoring(script, kind) else "not_applicable"
    stored = stored_review(project, episode)
    if stored.get("fingerprint") != direction_fingerprint(script, kind):
        return "pending_review"
    return "approved" if stored.get("decision") == DECISION_APPROVE else "changes_requested"


def apply_decision(
    project: dict[str, Any],
    episode: int,
    *,
    fingerprint: str,
    decision: str,
    decided_at: str,
    note: str | None,
    entry_ids: list[str] | None,
) -> bool:
    """就地把决定记录写入 project ``episodes[i].direction_review``；集条目不存在返回 ``False``。

    供 service 层在 ``ProjectManager.update_project`` 的回调内调用，决定记录的持久化形状单一真相源在此。
    """
    entry = find_episode(project, episode)
    if entry is None:
        return False
    entry[REVIEW_FIELD] = {
        "fingerprint": fingerprint,
        "decision": decision,
        "decided_at": decided_at,
        "note": note,
        "entry_ids": entry_ids,
    }
    return True


__all__ = [
    "DECISIONS",
    "DECISION_APPROVE",
    "DECISION_CHANGES",
    "DECISION_REDO",
    "REFERENCE_KIND",
    "REVIEW_FIELD",
    "ReviewStatus",
    "applies_to_project",
    "apply_decision",
    "direction_complete",
    "direction_fingerprint",
    "entry_direction",
    "entry_kind",
    "has_pending_authoring",
    "review_status",
    "script_entries",
    "stored_review",
    "undirected_entry_ids",
]
