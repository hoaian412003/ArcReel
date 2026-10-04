"""越南语配音：合成入队、逐 unit 的配音时间线与预览数据、混流成片。

配音台词本身随 video unit 存于剧本的 ``dub_lines``（见 ``reference_videos`` 路由的读写与回填入口）；
本路由只负责「把台词合成为音频」「按时间线排布」「混入原视频」三件事。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from lib.backends.providers import PROVIDER_VIENEU
from lib.generation.generation_queue import get_generation_queue
from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.path_safety import safe_join
from lib.project.project_manager import get_project_manager, is_reference_video_project
from lib.speech.asr_runner import AsrError
from lib.speech.dub_render import (
    DEFAULT_ORIGINAL_VOLUME,
    DubClip,
    DubRenderError,
    probe_has_audio_stream,
    render_dubbed_video,
)
from lib.speech.dub_settings import project_dub_target_language
from lib.speech.vieneu_service import load_config_from_db as load_vieneu_config
from server.auth import CurrentUser
from server.i18n import Translator
from server.services.dub_pipeline import detect_start_times
from server.services.dub_service import build_unit_dub, enqueue_auto_dub
from server.services.tasks.dub_tasks import DUB_AUDIO_PAYLOAD, DUB_TASK_TYPE

router = APIRouter(prefix="/projects/{project_name}/dub")

DUB_RENDER_DIR = "dub/renders"


class SynthesizeDubRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 要合成的 unit；None = 本集所有带配音台词的 unit。
    unit_ids: list[str] | None = None
    #: 只合成这些序号的配音行；None = 每个 unit 内缺失或过期的行。
    line_indexes: list[int] | None = Field(default=None)


def _load(project_name: str, episode: int, _t: Translator) -> tuple[dict, dict, str]:
    pm = get_project_manager()
    try:
        project = pm.load_project(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    meta = next((e for e in project.get("episodes") or [] if e.get("episode") == episode), None)
    if meta is None or not meta.get("script_file"):
        raise HTTPException(status_code=404, detail=_t("ref_episode_not_found", episode=episode))
    if not is_reference_video_project(project):
        raise HTTPException(status_code=409, detail=_t("ref_not_reference_video_mode"))
    if project_dub_target_language(project) is None:
        raise HTTPException(status_code=409, detail=_t("ref_dub_not_enabled"))
    script_file = meta["script_file"]
    try:
        script = pm.load_script(project_name, script_file)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    return project, script, script_file


def _find_unit(script: dict, unit_id: str, _t: Translator) -> dict:
    for unit in script.get("video_units") or []:
        if unit.get("unit_id") == unit_id:
            return unit
    raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id))


class DetectTimingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 要检测的 unit；None = 本集所有既有成片又有配音台词的 unit。
    unit_ids: list[str] | None = None


@router.post("/episodes/{episode}/detect-timing")
async def detect_timing(project_name: str, episode: int, req: DetectTimingRequest, _t: Translator) -> dict[str, Any]:
    """用语音识别从成片读出每条原台词的实际开始时间，写入配音台词的 ``detected_start_seconds``。

    整批 unit 共用一次模型加载；识别出的句子按文本相似度对回剧本台词，对不上的台词不写，
    开始时间继续按估算排布。
    """
    _project, script, script_file = await asyncio.to_thread(_load, project_name, episode, _t)
    candidates = [u for u in script.get("video_units") or [] if u.get("dub_lines")]
    if req.unit_ids is not None:
        _check_unit_selection(script, req.unit_ids, _t)
        candidates = [u for u in candidates if u.get("unit_id") in set(req.unit_ids)]
    if not candidates:
        raise HTTPException(status_code=400, detail=_t("dub_no_lines"))
    try:
        results = await detect_start_times(
            get_project_manager(), project_name, script_file, [str(u["unit_id"]) for u in candidates], language=None
        )
    except AsrError as exc:
        raise HTTPException(status_code=503, detail=_t("dub_asr_failed", err_msg=str(exc))) from exc
    return {"results": results}


def _check_unit_selection(script: dict, unit_ids: list[str], _t: Translator) -> None:
    if not unit_ids:
        raise BadRequestError("ref_batch_empty_selection")
    known = {u.get("unit_id") for u in script.get("video_units") or []}
    for unit_id in unit_ids:
        if unit_id not in known:
            raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id))


class AutoDubRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 要配音的视频；None = 本集所有已有成片的 unit。
    unit_ids: list[str] | None = None
    #: 跳过从成片检测开始时间，直接按估算排布。
    skip_timing: bool = False


@router.post("/episodes/{episode}/auto")
async def auto_dub(
    project_name: str, episode: int, req: AutoDubRequest, user: CurrentUser, _t: Translator
) -> dict[str, Any]:
    """自动配音：每个已有成片的视频各一个任务，依次写台词、检测开始时间、合成音频，同时最多 4 个在跑。"""
    _project, script, script_file = await asyncio.to_thread(_load, project_name, episode, _t)
    if req.unit_ids is not None:
        _check_unit_selection(script, req.unit_ids, _t)
    # 配音固定走本地 VieNeu，不依赖通用音频供应商的配置；只有被明确关闭时才拒绝
    if not (await load_vieneu_config()).enabled:
        raise HTTPException(status_code=409, detail=_t("dub_vieneu_disabled"))
    result = await enqueue_auto_dub(
        project_name,
        episode,
        script,
        script_file,
        req.unit_ids,
        skip_timing=req.skip_timing,
        user_id=user.id,
        source="webui",
    )
    if not result["task_ids"]:
        raise HTTPException(status_code=400, detail=_t("dub_nothing_to_voice"))
    return result


@router.post("/episodes/{episode}/synthesize")
async def synthesize_dub(
    project_name: str, episode: int, req: SynthesizeDubRequest, user: CurrentUser, _t: Translator
) -> dict[str, Any]:
    _project, script, script_file = await asyncio.to_thread(_load, project_name, episode, _t)
    units = [u for u in script.get("video_units") or [] if u.get("dub_lines")]
    if req.unit_ids is not None:
        if not req.unit_ids:
            raise BadRequestError("ref_batch_empty_selection")
        wanted = set(req.unit_ids)
        known = {u.get("unit_id") for u in script.get("video_units") or []}
        for unit_id in req.unit_ids:
            if unit_id not in known:
                raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id))
        units = [u for u in units if u.get("unit_id") in wanted]
    if not units:
        raise HTTPException(status_code=400, detail=_t("dub_no_lines"))

    # 配音固定走本地 VieNeu，不依赖通用音频供应商的配置；只有被明确关闭时才拒绝
    if not (await load_vieneu_config()).enabled:
        raise HTTPException(status_code=409, detail=_t("dub_vieneu_disabled"))

    queue = get_generation_queue()
    task_ids: list[str] = []
    for unit in units:
        payload: dict[str, Any] = {"script_file": script_file, "episode": episode, **DUB_AUDIO_PAYLOAD}
        if req.line_indexes is not None:
            payload["line_indexes"] = list(req.line_indexes)
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type=DUB_TASK_TYPE,
            media_type="audio",
            resource_id=str(unit["unit_id"]),
            payload=payload,
            script_file=script_file,
            source="webui",
            user_id=user.id,
            provider_id=PROVIDER_VIENEU,
        )
        task_ids.append(result["task_id"])
    return {"success": True, "task_ids": task_ids, "unit_ids": [str(u["unit_id"]) for u in units]}


@router.get("/episodes/{episode}/units/{unit_id}")
async def get_unit_dub(project_name: str, episode: int, unit_id: str, _t: Translator) -> dict[str, Any]:
    _project, script, _script_file = await asyncio.to_thread(_load, project_name, episode, _t)
    unit = _find_unit(script, unit_id, _t)
    assets = unit.get("generated_assets") or {}
    return {
        **build_unit_dub(unit),
        "video_path": assets.get("video_clip"),
        "original_volume": DEFAULT_ORIGINAL_VOLUME,
    }


@router.post("/episodes/{episode}/units/{unit_id}/render")
async def render_unit_dub(project_name: str, episode: int, unit_id: str, _t: Translator) -> dict[str, Any]:
    """把已合成的配音混入该 unit 的成片（原声压到 30%），产物写入 ``dub/renders/``。"""
    _project, script, _script_file = await asyncio.to_thread(_load, project_name, episode, _t)
    unit = _find_unit(script, unit_id, _t)
    video_rel = (unit.get("generated_assets") or {}).get("video_clip")
    if not video_rel:
        raise HTTPException(status_code=400, detail=_t("dub_video_missing"))
    timeline = build_unit_dub(unit)
    ready = [line for line in timeline["lines"] if line["audio_path"]]
    if not ready:
        raise HTTPException(status_code=400, detail=_t("dub_nothing_to_render"))

    project_dir = get_project_manager().get_project_path(project_name)
    video_abs = safe_join(project_dir, video_rel)
    clips = [
        DubClip(path=safe_join(project_dir, line["audio_path"]), start_seconds=line["start_seconds"]) for line in ready
    ]
    output_rel = f"{DUB_RENDER_DIR}/{unit_id}.mp4"
    try:
        await render_dubbed_video(
            video_abs,
            clips,
            safe_join(project_dir, output_rel),
            original_volume=DEFAULT_ORIGINAL_VOLUME,
            video_has_audio=await probe_has_audio_stream(video_abs),
        )
    except DubRenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True, "file_path": output_rel, "clip_count": len(clips)}
