"""单元拆解产出与已通过剧本（screenplay）的对齐判定。

单元拆解（``generate_script_plan``）以通过的剧本为依据划分单元。模型给每个单元标 ``scene_no``（所属剧本场次，
从 1 起），本模块据此机械判定：

- 每个单元都标了合法的 ``scene_no``，序号不倒退；
- 剧本的每一场至少有一个单元（不漏场）；
- 台词与画外音（drama 的 ``utterances``、参考生视频 ``text`` 里的 ``{台词}``）逐字取自剧本（或源文）——
  这些话是用户审阅过的，不得在拆解时改写；
- 单场内各单元时长合计与剧本的预计时长偏差过大时给出提示（软判定，不阻断）。

``scene_no`` 只用于本次判定，不落盘：它在计划条目模型里 ``exclude=True``，持久化的 script_plan 与正式脚本形状不变。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from lib.infra.text_utils import strip_json_code_fences
from lib.script.draft_violation import DraftViolation
from lib.script.reference_video.text_parser import line_speech_marks

ScreenplayKind = Literal["narration", "drama", "reference_video"]

#: 单场内单元时长合计相对剧本预计时长的容忍偏差；超出只提示。
BUDGET_DRIFT_RATIO = 0.4

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """逐字比对前的归一：NFKC 后去掉全部空白与 Markdown 强调符，标点与文字本身不动。"""
    return _WHITESPACE.sub("", unicodedata.normalize("NFKC", text)).replace("*", "")


def scene_numbers_from_response(response_text: str, items_key: str) -> list[object]:
    """从模型原始响应里按条目顺序取 ``scene_no``。

    计划条目模型把 ``scene_no`` 排除在 ``model_dump`` 之外，所以在落到 dict 之前从原始 JSON 里读；
    解析不出或形状不符时返回空列表，由调用侧的对齐判定报「缺 scene_no」。
    """
    try:
        data = json.loads(strip_json_code_fences(response_text))
    except (json.JSONDecodeError, TypeError):
        return []
    items = data.get(items_key) if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [item.get("scene_no") if isinstance(item, dict) else None for item in items]


def _dialogue_lines(kind: ScreenplayKind, entry: Mapping[str, Any]) -> list[str]:
    """条目里必须逐字取自剧本的台词 / 画外音文本。narration 的口播来自源文，不在此判定。"""
    if kind == "drama":
        raw = entry.get("utterances")
        return [
            str(utterance["text"])
            for utterance in (raw if isinstance(raw, list) else [])
            if isinstance(utterance, Mapping) and isinstance(utterance.get("text"), str)
        ]
    if kind == "reference_video":
        text = entry.get("text")
        if not isinstance(text, str):
            return []
        return [mark.text for line in text.splitlines() for mark in line_speech_marks(line)]
    return []


def collect_screenplay_violations(
    entries: Sequence[dict[str, Any]],
    scene_numbers: Sequence[object],
    screenplay: Mapping[str, Any],
    *,
    kind: ScreenplayKind,
    label_of: Callable[[dict[str, Any], int], str],
    source_text: str | None = None,
) -> tuple[list[DraftViolation], list[str]]:
    """判定单元拆解产出与剧本的对齐。返回 ``(违约, 提示)``：违约要落待修复草稿，提示只随结果回报。

    ``scene_numbers`` 与 ``entries`` 同序（见 :func:`scene_numbers_from_response`）。``source_text`` 给出时，
    台词也可取自它（源文本身就是成品剧本的项目：台词与剧本天然一致，不强求只出现在剧本里）。
    """
    scenes = screenplay["scenes"]
    scene_count = len(scenes)
    violations: list[DraftViolation] = []
    warnings: list[str] = []

    if len(scene_numbers) != len(entries):
        violations.append(
            DraftViolation(
                f"模型没有为每个单元标注 scene_no（{len(entries)} 个单元，读到 {len(scene_numbers)} 个）；"
                "每个单元都要写明它属于剧本的第几场",
                code="scene_no_missing",
            )
        )
        return violations, warnings

    previous = 0
    valid_numbers: list[int | None] = []
    for index, (entry, number) in enumerate(zip(entries, scene_numbers, strict=True)):
        label = label_of(entry, index)
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= scene_count:
            violations.append(
                DraftViolation(
                    f"{label} 的 scene_no={number!r} 无效：须为 1 到 {scene_count} 的整数（剧本共 {scene_count} 场）",
                    code="scene_no_invalid",
                    label=label,
                )
            )
            valid_numbers.append(None)
            continue
        valid_numbers.append(number)
        if number < previous:
            violations.append(
                DraftViolation(
                    f"{label} 的 scene_no={number} 小于前一个单元的 {previous}：单元须按剧本场次的先后排列，不能倒退",
                    code="scene_no_regress",
                    label=label,
                )
            )
        previous = max(previous, number)

    covered = {number for number in valid_numbers if number is not None}
    missing = [number for number in range(1, scene_count + 1) if number not in covered]
    if missing:
        violations.append(
            DraftViolation(
                f"剧本第 {missing} 场没有对应的单元：每一场至少要有一个单元，不得漏场",
                code="scene_not_covered",
            )
        )

    screenplay_text = _normalize("\n".join(str(scene["text"]) for scene in scenes))
    source = _normalize(source_text) if source_text else ""
    for index, entry in enumerate(entries):
        for line in _dialogue_lines(kind, entry):
            needle = _normalize(line)
            if needle and needle not in screenplay_text and needle not in source:
                label = label_of(entry, index)
                violations.append(
                    DraftViolation(
                        f"{label} 的台词「{line}」不在已通过的剧本里：台词与画外音须逐字取自剧本，不得改写、润色或新增",
                        code="utterance_not_in_screenplay",
                        label=label,
                    )
                )

    totals: dict[int, int] = {}
    for entry, number in zip(entries, valid_numbers, strict=True):
        duration = entry.get("duration_seconds")
        if number is not None and isinstance(duration, int) and not isinstance(duration, bool):
            totals[number] = totals.get(number, 0) + duration
    for number, total in sorted(totals.items()):
        estimated = int(scenes[number - 1]["estimated_seconds"])
        if abs(total - estimated) > estimated * BUDGET_DRIFT_RATIO:
            warnings.append(f"剧本第 {number} 场预计 {estimated} 秒，拆出的单元合计 {total} 秒，偏差较大")
    return violations, warnings
