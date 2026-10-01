"""越南语配音合成任务：把 unit 的配音台词逐条合成为音频并登记回剧本。

一个任务对应一个 video unit（``resource_id`` = ``unit_id``），按 payload 点名的配音行（缺省 = 全部缺失或
过期的行）逐条走现行 audio lane 合成；音频文件之外，真实时长与合成所用的台词、音色一并登记到
``dub_lines``，台词或音色之后再变即判过期。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lib.backends.audio_backends.vieneu import DEFAULT_MODEL as VIENEU_DEFAULT_MODEL
from lib.backends.providers import PROVIDER_VIENEU
from lib.db.base import DEFAULT_USER_ID
from lib.project.project_manager import get_project_manager
from lib.project.resource_paths import resource_relative_path
from lib.script.reference_video.dub_lines import unit_utterances
from lib.script.script_batch_edit import ScriptBatchEditCommand, ScriptBatchEditor, script_revision
from lib.script.script_models import DubLine
from lib.speech.audio_utils import probe_audio_duration_seconds
from lib.speech.dub_settings import project_dub_target_language, project_dub_voice, project_narrator_gender
from server.services.tasks.generation_context import AudioLaneRequest, resolve_generation_context

logger = logging.getLogger(__name__)

DUB_TASK_TYPE = "dub_tts"

#: 配音只走本地 VieNeu（越南语 TTS），不跟随项目 / 全局的通用音频供应商——后者常是中文 TTS，
#: 且未配置时根本没有可用供应商。经 payload 层固定供应商与模型（见 ConfigResolver 的 payload 层）。
DUB_AUDIO_PAYLOAD: dict[str, str] = {"audio_provider": PROVIDER_VIENEU, "audio_model": VIENEU_DEFAULT_MODEL}


def dub_audio_resource_id(unit_id: str, index: int) -> str:
    """一条配音行的音频资源 id；同一 unit 内以序号区分。"""
    return f"dub__{unit_id}__{index}"


#: 项目没指定音色时各性别优先选用的音色（VieNeu 预设里风格中性自然的一对）；目录里没有时才取该性别的第一个。
PREFERRED_DUB_VOICES: dict[str, str] = {"male": "Hải Đăng", "female": "Mai Anh"}


def resolve_dub_voice(project: dict, gender: str, voices: tuple[Any, ...], fallback: str) -> str:
    """某性别的配音音色：项目指定优先，其次默认的中性音色，再次音色目录里该性别的第一个，最后回落到旁白音色。"""
    chosen = project_dub_voice(project, gender)
    if chosen:
        return chosen
    preferred = PREFERRED_DUB_VOICES.get(gender)
    for voice in voices:
        if getattr(voice, "id", None) == preferred and getattr(voice, "gender", None) == gender:
            return str(voice.id)
    for voice in voices:
        if getattr(voice, "gender", None) == gender:
            return str(voice.id)
    return fallback


def dub_line_audio_is_current(line: DubLine, voice: str) -> bool:
    """配音行已登记的音频仍与现行台词、音色一致。"""
    return bool(line.audio_path) and line.audio_text == line.text_vi and line.audio_voice == voice


async def execute_dub_tts_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    script_file = payload.get("script_file")
    if not isinstance(script_file, str) or not script_file:
        raise ValueError("dub 任务需要 payload.script_file")
    wanted_raw = payload.get("line_indexes")
    wanted = {int(i) for i in wanted_raw} if isinstance(wanted_raw, list) else None

    pm = get_project_manager()
    project = await asyncio.to_thread(pm.load_project, project_name)
    if project_dub_target_language(project) is None:
        raise ValueError("项目未启用配音（dub_target_language）")
    script = await asyncio.to_thread(pm.load_script, project_name, script_file)
    unit = next((u for u in script.get("video_units") or [] if u.get("unit_id") == resource_id), None)
    if unit is None:
        raise ValueError(f"video unit 不存在: {resource_id}")
    lines = [DubLine.model_validate(raw) for raw in unit.get("dub_lines") or []]
    if not lines:
        raise ValueError(f"unit {resource_id} 没有配音台词")
    utterance_count = len(unit_utterances(str(unit.get("text") or "")))

    ctx = await resolve_generation_context(
        project_name, {**payload, **DUB_AUDIO_PAYLOAD}, project=project, user_id=user_id, audio=AudioLaneRequest()
    )
    audio = ctx.audio
    generator = ctx.generator

    synthesized: dict[int, dict[str, Any]] = {}
    for line in lines:
        if line.index >= utterance_count or (wanted is not None and line.index not in wanted):
            continue
        voice = resolve_dub_voice(project, line.gender, audio.voices, audio.narration_voice)
        if wanted is None and dub_line_audio_is_current(line, voice):
            continue
        rid = dub_audio_resource_id(resource_id, line.index)
        await generator.generate_audio_async(
            text=line.text_vi, resource_id=rid, voice=voice, speed=None, task_id=task_id
        )
        audio_rel = resource_relative_path("audio", rid)
        content = await asyncio.to_thread((pm.get_project_path(project_name) / audio_rel).read_bytes)
        duration = await probe_audio_duration_seconds(content, ".wav")
        synthesized[line.index] = {
            "audio_path": audio_rel,
            "audio_duration_seconds": duration,
            "audio_voice": voice,
            "audio_text": line.text_vi,
        }

    if synthesized:
        await _register_audio(project_name, script_file, resource_id, synthesized)
    return {
        "unit_id": resource_id,
        "synthesized": sorted(synthesized),
        "resource_type": "audio",
        "narrator_gender": project_narrator_gender(project),
    }


async def _register_audio(
    project_name: str, script_file: str, unit_id: str, synthesized: dict[int, dict[str, Any]]
) -> None:
    """把合成结果登记进剧本。登记前重读最新剧本：合成期间台词若又被改动，对应行的音频不登记（保持过期）。"""
    pm = get_project_manager()
    script = await asyncio.to_thread(pm.load_script, project_name, script_file)
    unit = next((u for u in script.get("video_units") or [] if u.get("unit_id") == unit_id), None)
    if unit is None:
        return
    merged: list[dict[str, Any]] = []
    changed = False
    for raw in unit.get("dub_lines") or []:
        line = dict(raw)
        index = line.get("index")
        update = synthesized.get(index) if isinstance(index, int) else None
        if update is not None and update["audio_text"] == line.get("text_vi"):
            line.update(update)
            changed = True
        merged.append(line)
    if not changed:
        return
    command = ScriptBatchEditCommand.model_validate(
        {
            "script": script_file,
            "expected_revision": script_revision(script),
            "operations": [{"op": "update", "id": unit_id, "fields": {"dub_lines": merged}}],
        }
    )
    edit = await asyncio.to_thread(ScriptBatchEditor(pm).execute, project_name, command)
    if not edit.success:
        raise RuntimeError(f"登记配音音频失败: {edit.model_dump_json()}")
