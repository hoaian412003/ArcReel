"""参考生视频 script_plan 导演规划的机械校验。

导演规划（集级 ``director_plan`` + 逐 unit 的导演说明，模型见 ``lib.script.script_models``）是
prompt_authoring 编写镜头的依据，规划空泛时下游提示词随之空泛。本模块只判看得出来的「薄」：
字段缺失、过短、节点数与顺序不对、铺垫型单元没写按住什么、unit 之间没写衔接。规划好不好、
与正文是否一致属于语义判断，交给内容确认时的人工审阅。

违约走 :class:`DraftViolation`，与正文校验同一套收集与报告口径；不带任何导演内容的存量
script_plan 由调用方按 :func:`has_director_content` 跳过。
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from lib.project.asset_types import asset_name_comparison_key
from lib.script.draft_violation import DraftViolation, collect_violations
from lib.script.reference_video.script_preview import derive_utterances

#: 违约定位前缀：集级导演规划没有 unit 归属。
DIRECTOR_PLAN_LABEL = "director_plan"

#: ``brief`` 去首尾空白后的最短字数，短于此多半是「紧张的一幕」这类空话。
DIRECTOR_BRIEF_MIN_CHARS = 15

#: 情绪弧线至少要有的拍数：一拍构不成转折。
DIRECTOR_ARC_MIN_BEATS = 2

#: 单个 unit 的动作节点数区间（含两端）。
DIRECTOR_BEATS_RANGE = (2, 5)

#: 铺垫型单元：结果被按住、留给后面的单元，必须写明本 unit 不能出现什么。
UNIT_TYPES_REQUIRING_MUST_NOT_SHOW = frozenset({"tension_build", "cliffhanger"})

#: 判定「这个 unit 带了导演内容」时看的字段。
_UNIT_DIRECTION_FIELDS = (
    "unit_type",
    "brief",
    "beats",
    "performance",
    "must_not_show",
    "end_frame",
    "continuity_in",
    "continuity_out",
)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def has_director_content(director_plan: object, units: Sequence[Mapping[str, Any]]) -> bool:
    """集级规划非空，或任一 unit 带了导演字段的非空值。"""
    if director_plan is not None:
        return True
    return any(unit.get(field) for unit in units for field in _UNIT_DIRECTION_FIELDS)


def validate_director_plan(director_plan: object, unit_count: int) -> None:
    """集级导演规划：本集意图与基调非空，弧线至少两拍、每拍指向有效的 unit 序号。"""
    if not isinstance(director_plan, Mapping):
        raise DraftViolation(
            "缺少集级导演规划 director_plan；请先写本集意图、情绪弧线与基调，再逐 unit 拆分",
            code="director_plan_missing",
            label=DIRECTOR_PLAN_LABEL,
        )
    problems: list[str] = []
    if not _text(director_plan.get("episode_intent")):
        problems.append("episode_intent 为空")
    if not _text(director_plan.get("tone")):
        problems.append("tone 为空")
    raw_arc = director_plan.get("arc")
    arc: list[Any] = raw_arc if isinstance(raw_arc, list) else []
    if len(arc) < DIRECTOR_ARC_MIN_BEATS:
        problems.append(f"arc 只有 {len(arc)} 拍，至少需要 {DIRECTOR_ARC_MIN_BEATS} 拍（含一次情绪或局面的转折）")
    for index, beat in enumerate(arc, start=1):
        if not isinstance(beat, Mapping) or not _text(beat.get("beat")):
            problems.append(f"arc 第 {index} 拍的 beat 为空")
            continue
        units = beat.get("units")
        if not isinstance(units, list) or not units:
            problems.append(f"arc 第 {index} 拍没有指明承载它的 unit")
            continue
        invalid = sorted({u for u in units if not isinstance(u, int) or not 1 <= u <= unit_count})
        if invalid:
            problems.append(f"arc 第 {index} 拍引用了不存在的 unit 序号 {invalid}（共 {unit_count} 个 unit，从 1 起）")
    if problems:
        raise DraftViolation(
            "集级导演规划不完整：" + "；".join(problems),
            code="director_plan_invalid",
            label=DIRECTOR_PLAN_LABEL,
        )


def _beat_problems(beats: object) -> list[str]:
    items: list[Any] = beats if isinstance(beats, list) else []
    low, high = DIRECTOR_BEATS_RANGE
    if not low <= len(items) <= high:
        return [f"beats 有 {len(items)} 个节点，须为 {low}-{high} 个"]
    problems: list[str] = []
    previous: int | None = None
    for index, beat in enumerate(items, start=1):
        if not isinstance(beat, Mapping):
            problems.append(f"beats 第 {index} 个节点形状非法")
            continue
        if not _text(beat.get("action")):
            problems.append(f"beats 第 {index} 个节点的 action 为空")
        at_pct = beat.get("at_pct")
        if not isinstance(at_pct, int) or not 0 <= at_pct <= 100:
            problems.append(f"beats 第 {index} 个节点的 at_pct 须为 0-100 的整数")
            continue
        if previous is not None and at_pct <= previous:
            problems.append(f"beats 的 at_pct 须严格递增（第 {index} 个节点 {at_pct} 不大于前一个 {previous}）")
        previous = at_pct
    return problems


def validate_unit_direction(label: str, unit: Mapping[str, Any], *, is_last: bool) -> None:
    """unit 级导演说明：类型、说明、动作节点、按住项、末帧与衔接。

    ``is_last`` 为本集最后一个 unit 时不要求 ``end_frame``——它不向本集内的下一个 unit 交接。
    一个 unit 的全部问题合成一条违约，报告按 unit 一次列全。
    """
    problems: list[str] = []
    unit_type = unit.get("unit_type")
    if not unit_type:
        problems.append("unit_type 为空")
    brief = _text(unit.get("brief"))
    if len(brief) < DIRECTOR_BRIEF_MIN_CHARS:
        problems.append(
            f"brief 过短（{len(brief)} 字，至少 {DIRECTOR_BRIEF_MIN_CHARS} 字）：写清发生什么、为什么、观众该有什么感受"
        )
    problems.extend(_beat_problems(unit.get("beats")))
    if unit_type in UNIT_TYPES_REQUIRING_MUST_NOT_SHOW and not _text(unit.get("must_not_show")):
        problems.append(f"unit_type 为 {unit_type} 的铺垫型单元须写 must_not_show（本单元按住、不能出现的内容）")
    if not is_last and not _text(unit.get("end_frame")):
        problems.append("end_frame 为空（最后一帧画面，交接给下一个 unit）")
    if not _text(unit.get("continuity_in")):
        problems.append("continuity_in 为空")
    if not _text(unit.get("continuity_out")):
        problems.append("continuity_out 为空")
    if problems:
        raise DraftViolation(
            f"{label} 的导演说明不完整：" + "；".join(problems),
            code="director_unit_invalid",
            label=label,
        )


# ---------------------------------------------------------------------------
# 导演步骤（内容确认之后）的逐单元导演说明：镜头表、表演与声音、台词落位
# ---------------------------------------------------------------------------


def unit_label(unit_id: str) -> str:
    return f"unit {unit_id}"


def _line_key(speaker: str, text: str) -> tuple[str, str]:
    base = speaker.split("/", 1)[0]
    return asset_name_comparison_key(base), " ".join(unicodedata.normalize("NFC", text).split())


def _expected_lines(text: str) -> list[tuple[str, str]]:
    """单元正文里的角色台词（说话人 + 逐字台词）；画外音不进视频，不在台词落位之列。"""
    utterances, _warnings = derive_utterances(text)
    return [_line_key(item.speaker or "", item.text) for item in utterances if item.kind == "dialogue"]


def _shot_problems(shots: object, duration_seconds: int) -> list[str]:
    items: list[Any] = shots if isinstance(shots, list) else []
    if not items:
        return ["shots 为空：至少要有一个镜头"]
    problems: list[str] = []
    previous: float | None = None
    for index, shot in enumerate(items, start=1):
        if not isinstance(shot, Mapping):
            problems.append(f"shots 第 {index} 镜形状非法")
            continue
        at = shot.get("at_seconds")
        if isinstance(at, bool) or not isinstance(at, (int, float)):
            problems.append(f"shots 第 {index} 镜的 at_seconds 须为数字")
            continue
        if index == 1 and at != 0:
            problems.append("第一镜的 at_seconds 须为 0")
        if previous is not None and at <= previous:
            problems.append(f"shots 的 at_seconds 须严格递增（第 {index} 镜 {at} 不大于前一镜 {previous}）")
        if duration_seconds > 0 and at >= duration_seconds:
            problems.append(f"shots 第 {index} 镜的 at_seconds={at} 超出单元时长 {duration_seconds} 秒")
        if index > 1 and not _text(shot.get("why_cut")):
            problems.append(f"shots 第 {index} 镜没写 why_cut（切到这一镜带来了什么新的主体、空间、状态或视角）")
        previous = float(at)
    return problems


def _line_problems(direction: Mapping[str, Any], text: str, shot_count: int) -> list[str]:
    raw_lines = direction.get("lines")
    lines = [item for item in (raw_lines if isinstance(raw_lines, list) else []) if isinstance(item, Mapping)]
    problems: list[str] = []
    produced = [_line_key(_text(item.get("speaker")), _text(item.get("text"))) for item in lines]
    expected = _expected_lines(text)
    if Counter(produced) != Counter(expected):
        problems.append(
            f"lines 与正文的角色台词不一致（正文 {len(expected)} 句，lines {len(produced)} 句）："
            "每句 @[角色]{台词} 各写一条，说话人与台词逐字照抄，画外音不写"
        )
    for index, item in enumerate(lines, start=1):
        shot = item.get("shot")
        if isinstance(shot, bool) or not isinstance(shot, int) or not 1 <= shot <= shot_count:
            problems.append(f"lines 第 {index} 句的 shot={shot!r} 不是有效的镜头序号（共 {shot_count} 镜）")
    raw_performance = direction.get("performance")
    voiced = {
        asset_name_comparison_key(_text(item.get("character")).split("/", 1)[0])
        for item in (raw_performance if isinstance(raw_performance, list) else [])
        if isinstance(item, Mapping) and _text(item.get("voice"))
    }
    silent = sorted({speaker for speaker, _line in produced if speaker and speaker not in voiced})
    if silent:
        problems.append(f"说话的角色没有在 performance 里写 voice（音高、音色、语速）：{'、'.join(silent)}")
    return problems


def _reference_unit_problems(direction: Mapping[str, Any], unit: Mapping[str, Any], *, is_last: bool) -> list[str]:
    problems: list[str] = []
    unit_type = direction.get("unit_type")
    if not unit_type:
        problems.append("unit_type 为空")
    brief = _text(direction.get("brief"))
    if len(brief) < DIRECTOR_BRIEF_MIN_CHARS:
        problems.append(
            f"brief 过短（{len(brief)} 字，至少 {DIRECTOR_BRIEF_MIN_CHARS} 字）：写清发生什么、为什么、观众该有什么感受"
        )
    duration = unit.get("duration_seconds")
    shots = direction.get("shots")
    problems.extend(_shot_problems(shots, duration if isinstance(duration, int) else 0))
    shot_count = len(shots) if isinstance(shots, list) else 0
    problems.extend(_line_problems(direction, _text(unit.get("text")), shot_count))
    if unit_type in UNIT_TYPES_REQUIRING_MUST_NOT_SHOW and not _text(direction.get("must_not_show")):
        problems.append(f"unit_type 为 {unit_type} 的铺垫型单元须写 must_not_show（本单元按住、不能出现的内容）")
    if not is_last and not _text(direction.get("end_frame")):
        problems.append("end_frame 为空（最后一帧画面，交接给下一个单元）")
    if not _text(direction.get("continuity_in")):
        problems.append("continuity_in 为空")
    if not _text(direction.get("continuity_out")):
        problems.append("continuity_out 为空")
    return problems


def collect_reference_director_violations(
    output: Mapping[str, Any],
    *,
    units_by_id: Mapping[str, Mapping[str, Any]],
    expected_ids: Sequence[str],
    total_units: int,
    last_unit_id: str | None,
) -> list[DraftViolation]:
    """校验一份参考生视频导演产出（``ReferenceDirectorEpisodeOutput`` 的 dict 形态），返回收齐的违约列表。

    ``units_by_id`` 是正式脚本里的单元（取正文与时长）；``expected_ids`` 是本次要导演的单元 id（按剧本顺序）；
    ``total_units`` 用于校验弧线引用的序号范围；``last_unit_id`` 是本集最后一个单元，它不要求 ``end_frame``。
    """
    raw_units = output.get("units")
    items = [item for item in (raw_units if isinstance(raw_units, list) else []) if isinstance(item, Mapping)]
    produced_ids = [str(item.get("id")) for item in items]
    checks: list[Callable[[], Any]] = [
        lambda: validate_director_plan(output.get("director_plan"), total_units),
        lambda: _check_unit_coverage(expected_ids, produced_ids),
    ]
    for item in items:
        unit_id = str(item.get("id"))
        direction = item.get("direction")
        unit = units_by_id.get(unit_id)
        if not isinstance(direction, Mapping) or unit is None:
            continue
        checks.append(
            lambda unit_id=unit_id, direction=direction, unit=unit: _raise_unit_problems(
                unit_id, _reference_unit_problems(direction, unit, is_last=unit_id == last_unit_id)
            )
        )
    return collect_violations(checks)


def _raise_unit_problems(unit_id: str, problems: list[str]) -> None:
    if problems:
        raise DraftViolation(
            f"{unit_label(unit_id)} 的导演说明不完整：" + "；".join(problems),
            code="director_unit_invalid",
            label=unit_label(unit_id),
        )


def _check_unit_coverage(expected_ids: Sequence[str], produced_ids: Sequence[str]) -> None:
    counts = Counter(produced_ids)
    missing = [unit_id for unit_id in expected_ids if unit_id not in counts]
    extra = sorted({unit_id for unit_id in produced_ids if unit_id not in set(expected_ids)})
    duplicated = sorted(unit_id for unit_id, count in counts.items() if count > 1)
    problems: list[str] = []
    if missing:
        problems.append(f"缺少单元 {missing}")
    if extra:
        problems.append(f"含本次范围之外的单元 {extra}")
    if duplicated:
        problems.append(f"单元重复 {duplicated}")
    if problems:
        raise DraftViolation(
            "导演产出的单元与本次范围不一致：" + "；".join(problems) + "；units 须逐字对应输入单元 id，不增不减",
            code="director_entries_mismatch",
            label=DIRECTOR_PLAN_LABEL,
        )


__all__ = [
    "DIRECTOR_ARC_MIN_BEATS",
    "DIRECTOR_BEATS_RANGE",
    "DIRECTOR_BRIEF_MIN_CHARS",
    "DIRECTOR_PLAN_LABEL",
    "UNIT_TYPES_REQUIRING_MUST_NOT_SHOW",
    "collect_reference_director_violations",
    "has_director_content",
    "unit_label",
    "validate_director_plan",
    "validate_unit_direction",
]
