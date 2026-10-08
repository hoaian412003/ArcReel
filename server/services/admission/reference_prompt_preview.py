"""参考生视频草稿的最终提示词，使用当前请求投影的实发参考图与能力。"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib.generation.batch_admission import DURATION_CONFIRMATION_CODE
from lib.script.reference_video.h3_prompt import H3PromptUnavailable
from lib.script.reference_video.prompt_render import render_video_unit_prompt, resolve_reference_audio_paths
from lib.script.reference_video.request_projection import ReferenceUnitRequestProjection
from lib.script.reference_video.text_parser import derive_references_from_text
from lib.script.reference_video.voice_settings import VoiceRenderSettings

REFERENCE_ASSET_MISSING_CODE = "reference_asset_missing"
#: 缺图让单元从声明的 r2v 降到 i2v：同一个缺图问题的另一面，同样不妨碍读提示词。
REFERENCE_CAPABILITY_CHANGED_CODE = "reference_capability_changed"
#: 一张图都没有时单元落到 i2v 的无图档，模型不支持文生视频——仍是缺图的后果。
TEXT_TO_VIDEO_UNSUPPORTED_CODE = "video_capability_missing_t2v"
#: H3 载荷引用的资产不在本次请求里——缺图时请求里本来就没有它们。
H3_UNRESOLVED_REFERENCE_CODE = "h3_prompt_unresolved_reference"


def render_reference_prompt_preview(
    *,
    project: dict,
    unit: dict,
    project_path: Path,
    projection: ReferenceUnitRequestProjection,
    translate: Callable[..., str],
) -> dict[str, Any]:
    """只读渲染；时长待确认、参考图尚未生成都不妨碍查看文本，其他投影阻断项显式显示为不可用。

    参考图缺失时按正文声明的引用顺序给 ``<Picture N>`` 编号（假定它们都会随请求发出），并把缺图写进警告：
    预览回答的是「提示词长什么样」，缺图是生成前要补的事，不该让人先去生成资产图才能读到提示词。

    与分镜预览同一口径：``text`` 与 ``unavailable`` 恰有一个非 ``None``，``warnings`` 只随文本出现。
    """
    result: dict[str, Any] = {
        "text": None,
        "unavailable": None,
        "is_text_form": True,
        "references": [],
        "warnings": [],
    }
    missing_codes = {
        REFERENCE_ASSET_MISSING_CODE,
        REFERENCE_CAPABILITY_CHANGED_CODE,
        TEXT_TO_VIDEO_UNSUPPORTED_CODE,
        H3_UNRESOLVED_REFERENCE_CODE,
    }
    has_missing_assets = any(problem.code == REFERENCE_ASSET_MISSING_CODE for problem in projection.blocking_problems)
    deferrable = {DURATION_CONFIRMATION_CODE, *(missing_codes if has_missing_assets else ())}
    blockers = [problem for problem in projection.blocking_problems if problem.code not in deferrable]
    missing_references = [
        problem
        for problem in projection.blocking_problems
        if has_missing_assets and problem.code == REFERENCE_ASSET_MISSING_CODE
    ]
    if blockers:
        result["unavailable"] = translate(blockers[0].code, **blockers[0].parameters())
        return result
    request_facts = projection.request_facts
    if request_facts is None:
        result["unavailable"] = translate(
            "reference_capability_unavailable", capability=projection.hydrated_generation_type
        )
        return result
    if not str(unit.get("text") or "").strip():
        result["unavailable"] = translate("reference_prompt_preview_missing")
        return result

    try:
        rendered = render_video_unit_prompt(
            unit,
            project,
            VoiceRenderSettings.from_request_facts(
                request_facts, audio_ready=resolve_reference_audio_paths(project, project_path)
            ),
            request_references=(
                derive_references_from_text(str(unit.get("text") or ""), project)[0]
                if missing_references
                else [asset.reference for asset in projection.request_assets]
            ),
        )
    except H3PromptUnavailable as exc:
        result["unavailable"] = translate(exc.code, **exc.params)
        return result
    result["text"] = rendered.prompt
    result["references"] = [
        {
            "type": asset.reference.type,
            "name": asset.reference.name,
            "path": asset.path.relative_to(project_path).as_posix(),
        }
        for asset in projection.request_assets
    ]
    result["warnings"] = [
        *(translate(problem.code, **problem.parameters()) for problem in missing_references),
        *(
            translate(problem.code, **problem.parameters())
            for problem in projection.problems
            if not (has_missing_assets and problem.code in missing_codes)
        ),
        *(translate(warning["key"], **warning["params"]) for warning in rendered.warnings),
    ]
    return result
