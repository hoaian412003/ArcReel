"""Data-driven workflow step rules for every content and generation mode pair."""

from __future__ import annotations

from dataclasses import dataclass

from lib.script.script_skeleton import resolve_declared_kind


@dataclass(frozen=True, slots=True)
class WorkflowStepRule:
    """One ordered workflow step and the status checkpoint that owns it."""

    id: str
    checkpoint: str | None
    applicable: bool


@dataclass(frozen=True, slots=True)
class WorkflowRule:
    """The complete workflow shape for one immutable project mode pair."""

    content_mode: str
    generation_mode: str
    skeleton_kind: str
    preprocessor: str | None
    steps: tuple[WorkflowStepRule, ...]


_STEP_CHECKPOINTS: tuple[tuple[str, str | None], ...] = (
    ("project_input", "PROJECT_INPUT"),
    ("selling_points", "SELLING_POINTS"),
    ("asset_inventory", "ASSET_INVENTORY"),
    ("episode_plan", "EPISODE_PLAN"),
    ("screenplay", "SCREENPLAY"),
    ("screenplay_review", "SCREENPLAY_REVIEW"),
    ("script_plan_content", "SCRIPT_PLAN_CONTENT"),
    ("script_plan_review", "SCRIPT_PLAN_REVIEW"),
    ("direct_episode", "DIRECTOR_PLAN"),
    ("direction_review", "DIRECTOR_REVIEW"),
    ("final_script", "FINAL_SCRIPT"),
    ("asset_sheets", "ASSET_SHEETS"),
    ("script_structure", None),
    ("storyboard", "STORYBOARD"),
    ("narration_delivery", None),
    ("video", "VIDEO"),
    ("export", "EXPORT_READY"),
)

_EPISODIC_STEPS = frozenset(
    {
        "project_input",
        "asset_inventory",
        "episode_plan",
        "screenplay",
        "screenplay_review",
        "script_plan_content",
        "script_plan_review",
        "final_script",
        "asset_sheets",
        "script_structure",
        "narration_delivery",
        "video",
        "export",
    }
)

#: 导演步骤在旁白 / 剧情的两条路线（分镜图生视频、参考生视频）上都适用；广告没有。
_DIRECTOR_STEPS = frozenset({"direct_episode", "direction_review"})

_CONTENT_STEPS: dict[str, frozenset[str]] = {
    "narration": _EPISODIC_STEPS,
    "drama": _EPISODIC_STEPS,
    "ad": frozenset(
        {
            "project_input",
            "selling_points",
            "final_script",
            "asset_sheets",
            "script_structure",
            "narration_delivery",
            "video",
            "export",
        }
    ),
}

_PREPROCESSORS: dict[tuple[str, str], str | None] = {
    ("narration", "storyboard"): "split-narration-segments",
    ("narration", "reference_video"): "split-reference-video-units",
    ("drama", "storyboard"): "normalize-drama-script",
    ("drama", "reference_video"): "split-reference-video-units",
    ("ad", "storyboard"): None,
    ("ad", "reference_video"): None,
}


#: 可选配音步骤：位于 video 与 export 之间，无状态检查点，不阻断 export。
VOICE_OVER_STEP_ID = "voice_over"


def _build_rule(content_mode: str, generation_mode: str, *, voice_over: bool = False) -> WorkflowRule:
    applicable = set(_CONTENT_STEPS[content_mode])
    if generation_mode == "storyboard":
        applicable.add("storyboard")
    if content_mode in {"narration", "drama"}:
        applicable |= _DIRECTOR_STEPS
    steps = [
        WorkflowStepRule(id=step_id, checkpoint=checkpoint, applicable=step_id in applicable)
        for step_id, checkpoint in _STEP_CHECKPOINTS
    ]
    if voice_over:
        export_index = next(index for index, step in enumerate(steps) if step.id == "export")
        steps.insert(export_index, WorkflowStepRule(id=VOICE_OVER_STEP_ID, checkpoint=None, applicable=True))
    return WorkflowRule(
        content_mode=content_mode,
        generation_mode=generation_mode,
        skeleton_kind=resolve_declared_kind(content_mode, generation_mode),
        preprocessor=_PREPROCESSORS[(content_mode, generation_mode)],
        steps=tuple(steps),
    )


WORKFLOW_RULES: dict[tuple[str, str], WorkflowRule] = {
    (content_mode, generation_mode): _build_rule(content_mode, generation_mode)
    for content_mode in ("narration", "drama", "ad")
    for generation_mode in ("storyboard", "reference_video")
}


#: 启用越南语配音的参考生视频项目：同一组规则外加可选的 ``voice_over`` 步骤。
VOICE_OVER_WORKFLOW_RULES: dict[tuple[str, str], WorkflowRule] = {
    (content_mode, "reference_video"): _build_rule(content_mode, "reference_video", voice_over=True)
    for content_mode in ("narration", "drama", "ad")
}


def workflow_rule(content_mode: str, generation_mode: str, *, voice_over: bool = False) -> WorkflowRule:
    """Return the exhaustive rule for a validated project mode pair.

    ``voice_over`` selects the variant that carries the optional voice-over step (reference-video only).
    """

    try:
        return (VOICE_OVER_WORKFLOW_RULES if voice_over else WORKFLOW_RULES)[(content_mode, generation_mode)]
    except KeyError as exc:
        raise ValueError(f"unsupported workflow mode pair: {content_mode!r}, {generation_mode!r}") from exc


__all__ = [
    "VOICE_OVER_STEP_ID",
    "VOICE_OVER_WORKFLOW_RULES",
    "WORKFLOW_RULES",
    "WorkflowRule",
    "WorkflowStepRule",
    "workflow_rule",
]
