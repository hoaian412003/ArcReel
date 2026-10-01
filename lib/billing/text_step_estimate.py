"""按提示词规模估算文本类制作步骤的 token 用量。

只做参考估算：输入 token 由源文本、资产清单与模板开销推得，输出 token 优先取已有产物的规模，
没有产物时按固定比例推。不构造真实提示词，也不调用任何供应商。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: 各步骤提示词模板（含规则与输出格式说明）的固定开销，单位 token。
_TEMPLATE_OVERHEAD_TOKENS = {
    "episode_plan": 2_000,
    "script_plan_content": 2_500,
    "final_script": 3_000,
}

#: 没有既有产物时，输出 token 相对该集源文本 token 的比例。
_OUTPUT_RATIO = {
    "script_plan_content": 0.5,
    "final_script": 1.2,
}
_EPISODE_PLAN_OUTPUT_TOKENS_PER_EPISODE = 250
_EPISODE_PLAN_MIN_OUTPUT_TOKENS = 1_500

_ASSET_KEYS = ("characters", "scenes", "props", "products")


def estimate_tokens(text: str) -> int:
    """CJK 字符约 1.2 token/字，其余字符约 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "　" <= ch <= "鿿" or "가" <= ch <= "힯")
    return round(cjk * 1.2 + (len(text) - cjk) / 4)


@dataclass(frozen=True, slots=True)
class TextStepEstimate:
    id: str
    calls: int
    input_tokens: int
    output_tokens: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def _assets_tokens(project: Mapping[str, Any]) -> int:
    slim = {key: project.get(key) for key in _ASSET_KEYS if project.get(key)}
    return estimate_tokens(json.dumps(slim, ensure_ascii=False)) if slim else 0


def _script_tokens(script: Mapping[str, Any] | None) -> int:
    if not script:
        return 0
    return estimate_tokens(json.dumps(script, ensure_ascii=False))


def estimate_text_steps(
    project: Mapping[str, Any],
    *,
    source_text: str,
    scripts_by_episode: Mapping[int, Mapping[str, Any]],
    episode_numbers: Sequence[int],
) -> list[TextStepEstimate]:
    """估算整个项目文本步骤的调用次数与 token 数。

    ``episode_numbers`` 为空（尚未规划分集）时只估分集规划一步。
    """
    source_tokens = estimate_tokens(source_text)
    if source_tokens == 0:
        return []
    assets_tokens = _assets_tokens(project)
    episodes = list(episode_numbers)

    steps = [
        TextStepEstimate(
            id="episode_plan",
            calls=1,
            input_tokens=_TEMPLATE_OVERHEAD_TOKENS["episode_plan"] + source_tokens + assets_tokens,
            output_tokens=max(
                _EPISODE_PLAN_MIN_OUTPUT_TOKENS,
                len(episodes) * _EPISODE_PLAN_OUTPUT_TOKENS_PER_EPISODE,
            ),
        )
    ]
    if not episodes:
        return steps

    share = source_tokens // len(episodes)
    plan_output = round(share * _OUTPUT_RATIO["script_plan_content"])
    for step_id in ("script_plan_content", "final_script"):
        script_output = sum(_script_tokens(scripts_by_episode.get(ep)) for ep in episodes)
        output_per_episode = round(share * _OUTPUT_RATIO[step_id])
        # 最终剧本已存在时，输出规模以现有剧本为准；分集规划内容没有独立产物，只能按比例推。
        output_total = (
            script_output if step_id == "final_script" and script_output else output_per_episode * len(episodes)
        )
        # 最终剧本的输入还要带上前一步的规划内容。
        carried = plan_output if step_id == "final_script" else 0
        steps.append(
            TextStepEstimate(
                id=step_id,
                calls=len(episodes),
                input_tokens=len(episodes) * (_TEMPLATE_OVERHEAD_TOKENS[step_id] + share + assets_tokens + carried),
                output_tokens=output_total,
            )
        )
    return steps
