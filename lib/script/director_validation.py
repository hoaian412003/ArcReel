"""分镜路线（旁白 / 剧情）导演步骤产出的机械校验。

导演步骤产出集级 ``DirectorPlan`` 与逐条目 ``EntryDirection``（模型见 ``lib.script.script_models``）。
本模块只判看得出来的缺口：条目覆盖不全或错配、集级规划空泛、镜头意图缺失或过短、出场角色没写表演、
衔接字段为空。镜头好不好、意图是否贴合内容属于语义判断，交给导演审阅时的人工决定。

镜头的角度 / 景别 / 运动是自由文本，不设词表；只要求非空。违约走 :class:`DraftViolation`，一次判定
收齐全部条目的问题，由调用方渲染成报告。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lib.script.draft_violation import DraftViolation, collect_violations
from lib.script.reference_video.director_validation import DIRECTOR_PLAN_LABEL, validate_director_plan

#: 镜头意图去首尾空白后的最短字数：短于此多半是「好看」「氛围」这类空话。
DIRECTOR_INTENT_MIN_CHARS = 6

#: 不构成意图的占位文本（比较时忽略大小写与首尾空白）。
_GENERIC_INTENTS = frozenset({"n/a", "na", "none", "tbd", "todo", "无", "暂无", "没有", "...", "…", "-", "—"})


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def entry_label(entry_id: str) -> str:
    return f"entry {entry_id}"


def _entry_problems(
    entry_id: str,
    direction: Mapping[str, Any],
    characters: Sequence[str],
) -> list[str]:
    problems: list[str] = []
    raw_camera = direction.get("camera")
    camera: Mapping[str, Any] = raw_camera if isinstance(raw_camera, Mapping) else {}
    problems.extend(
        f"camera.{field} 为空" for field in ("angle", "shot_size", "movement") if not _text(camera.get(field))
    )
    intent = _text(camera.get("intent"))
    if not intent:
        problems.append("camera.intent 为空（写清为什么这样拍）")
    elif len(intent) < DIRECTOR_INTENT_MIN_CHARS or intent.casefold() in _GENERIC_INTENTS:
        problems.append(
            f"camera.intent 过于空泛（{len(intent)} 字，至少 {DIRECTOR_INTENT_MIN_CHARS} 字）：写清选这个镜头要让观众感受到什么"
        )
    raw_performance = direction.get("performance")
    performance: list[Any] = raw_performance if isinstance(raw_performance, list) else []
    covered: set[str] = set()
    for index, item in enumerate(performance, start=1):
        if not isinstance(item, Mapping) or not _text(item.get("character")) or not _text(item.get("note")):
            problems.append(f"performance 第 {index} 项的 character 或 note 为空")
            continue
        covered.add(_text(item["character"]))
    missing = [name for name in characters if name not in covered]
    if missing:
        problems.append(f"出场角色没有表演说明：{'、'.join(missing)}")
    if not _text(direction.get("continuity_in")):
        problems.append("continuity_in 为空")
    if not _text(direction.get("continuity_out")):
        problems.append("continuity_out 为空")
    return problems


def _check_entry(entry_id: str, direction: Mapping[str, Any], characters: Sequence[str]) -> None:
    problems = _entry_problems(entry_id, direction, characters)
    if problems:
        raise DraftViolation(
            f"{entry_label(entry_id)} 的导演说明不完整：" + "；".join(problems),
            code="director_entry_invalid",
            label=entry_label(entry_id),
        )


def _check_coverage(expected_ids: Sequence[str], produced_ids: Sequence[str]) -> None:
    seen: set[str] = set()
    duplicated: list[str] = []
    for entry_id in produced_ids:
        if entry_id in seen and entry_id not in duplicated:
            duplicated.append(entry_id)
        seen.add(entry_id)
    missing = [entry_id for entry_id in expected_ids if entry_id not in seen]
    extra = [entry_id for entry_id in produced_ids if entry_id not in set(expected_ids)]
    problems: list[str] = []
    if missing:
        problems.append(f"缺少条目 {missing}")
    if extra:
        problems.append(f"含本次范围之外的条目 {sorted(set(extra))}")
    if duplicated:
        problems.append(f"条目重复 {duplicated}")
    if problems:
        raise DraftViolation(
            "导演产出的条目与本次范围不一致：" + "；".join(problems) + "；entries 须逐字对应输入条目 id，不增不减",
            code="director_entries_mismatch",
            label=DIRECTOR_PLAN_LABEL,
        )


def collect_director_violations(
    output: Mapping[str, Any],
    *,
    expected_ids: Sequence[str],
    characters_by_id: Mapping[str, Sequence[str]],
    total_entries: int,
    validate_plan: bool = True,
) -> list[DraftViolation]:
    """校验一份导演产出（``DirectorEpisodeOutput`` 的 dict 形态），返回收齐的违约列表。

    ``expected_ids`` 是本次要导演的条目 id（按剧本顺序）；``characters_by_id`` 给出每个条目登记的出场角色，
    用于检查表演覆盖；``total_entries`` 是整集条目数，用于校验弧线引用的序号范围。整集重做以外
    （只修订部分条目）时 ``validate_plan`` 仍默认校验集级规划，因为产出里总带一份规划。
    """
    raw_entries = output.get("entries")
    entries: list[Any] = raw_entries if isinstance(raw_entries, list) else []
    produced_ids = [str(entry.get("id")) for entry in entries if isinstance(entry, Mapping)]
    checks: list[Any] = []
    if validate_plan:
        checks.append(lambda: validate_director_plan(output.get("director_plan"), total_entries))
    checks.append(lambda: _check_coverage(expected_ids, produced_ids))
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        entry_id = str(entry.get("id"))
        direction = entry.get("direction")
        if not isinstance(direction, Mapping):
            checks.append(
                lambda entry_id=entry_id: _raise_missing_direction(entry_id),
            )
            continue
        characters = characters_by_id.get(entry_id, ())
        checks.append(
            lambda entry_id=entry_id, direction=direction, characters=characters: _check_entry(
                entry_id, direction, characters
            )
        )
    return collect_violations(checks)


def _raise_missing_direction(entry_id: str) -> None:
    raise DraftViolation(
        f"{entry_label(entry_id)} 缺少 direction",
        code="director_entry_invalid",
        label=entry_label(entry_id),
    )


__all__ = [
    "DIRECTOR_INTENT_MIN_CHARS",
    "collect_director_violations",
    "entry_label",
]
