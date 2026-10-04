"""越南语配音任务。

- ``dub_tts``：把 unit 的配音台词逐条合成为音频并登记回剧本。一个任务对应一个 video unit
  （``resource_id`` = ``unit_id``），按 payload 点名的配音行（缺省 = 全部缺失或过期的行）逐条走 audio lane
  合成；音频文件之外，真实时长与合成所用的台词、音色一并登记到 ``dub_lines``，台词或音色之后再变即判过期。
- ``dub_auto``：一个视频的完整配音流水线——缺台词先写越南语台词，再从成片检测原台词开始时间，最后合成音频。
  同一时刻最多 4 个视频在跑（vieneu 的 audio lane 并发），写剧本与语音识别分别由 ``dub_pipeline`` 串行化。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from lib.backends.audio_backends.vieneu import DEFAULT_MODEL as VIENEU_DEFAULT_MODEL
from lib.backends.providers import PROVIDER_VIENEU
from lib.db.base import DEFAULT_USER_ID
from lib.infra.subprocess_deadline import SubprocessDeadlineExceeded, run_with_deadline
from lib.project.project_manager import get_project_manager
from lib.project.resource_paths import resource_relative_path
from lib.script.reference_video.dub_lines import unit_utterances
from lib.script.script_models import DubLine
from lib.speech.asr_runner import AsrError
from lib.speech.audio_utils import probe_audio_duration_seconds
from lib.speech.dub_settings import project_dub_target_language, project_dub_voice, project_narrator_gender
from server.services.dub_pipeline import detect_start_times, find_unit, update_unit_dub_lines
from server.services.tasks.generation_context import AudioLaneRequest, resolve_generation_context

logger = logging.getLogger(__name__)

DUB_TASK_TYPE = "dub_tts"
DUB_AUTO_TASK_TYPE = "dub_auto"

#: 配音只走本地 VieNeu（越南语 TTS），不跟随项目 / 全局的通用音频供应商——后者常是中文 TTS，
#: 且未配置时根本没有可用供应商。经 payload 层固定供应商与模型（见 ConfigResolver 的 payload 层）。
DUB_AUDIO_PAYLOAD: dict[str, str] = {"audio_provider": PROVIDER_VIENEU, "audio_model": VIENEU_DEFAULT_MODEL}

#: 配音后处理倍速（VieNeu 无 speed 参数，合成后用 ffmpeg atempo 变速，保持音高）。
DUB_PLAYBACK_SPEED = 1.2
_DUB_SPEEDUP_TIMEOUT_SECONDS = 60.0


async def _speed_up_wav(path: Path) -> None:
    """把已落盘的配音 wav 原地加速到 ``DUB_PLAYBACK_SPEED``；ffmpeg 不可用时保持原速。"""
    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg 不可用，配音保持原速: %s", path.name)
        return
    tmp = path.with_name(f".{path.stem}.speed{path.suffix}")
    try:
        result = await run_with_deadline(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-filter:a", f"atempo={DUB_PLAYBACK_SPEED}", str(tmp)],
            deadline_seconds=_DUB_SPEEDUP_TIMEOUT_SECONDS,
            cleanup_paths=[tmp],
        )
    except (FileNotFoundError, SubprocessDeadlineExceeded):
        logger.warning("ffmpeg 不可用或超时，配音保持原速: %s", path.name)
        return
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        logger.warning("ffmpeg 变速失败，配音保持原速: %s", path.name)
        return
    await asyncio.to_thread(tmp.replace, path)


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


def _requested_line_indexes(payload: dict[str, Any]) -> set[int] | None:
    raw = payload.get("line_indexes")
    return {int(i) for i in raw} if isinstance(raw, list) else None


async def _synthesize_unit(
    project_name: str,
    unit_id: str,
    script_file: str,
    payload: dict[str, Any],
    *,
    user_id: str,
    task_id: str | None,
) -> list[int]:
    """合成一个 unit 里缺失或过期（或 payload 点名）的配音行，并把结果登记回剧本；返回合成了哪些序号。"""
    wanted = _requested_line_indexes(payload)
    pm = get_project_manager()
    project = await asyncio.to_thread(pm.load_project, project_name)
    if project_dub_target_language(project) is None:
        raise ValueError("项目未启用配音（dub_target_language）")
    script = await asyncio.to_thread(pm.load_script, project_name, script_file)
    unit = find_unit(script, unit_id)
    if unit is None:
        raise ValueError(f"video unit 不存在: {unit_id}")
    lines = [DubLine.model_validate(raw) for raw in unit.get("dub_lines") or []]
    if not lines:
        raise ValueError(f"unit {unit_id} 没有配音台词")
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
        rid = dub_audio_resource_id(unit_id, line.index)
        await generator.generate_audio_async(
            text=line.text_vi, resource_id=rid, voice=voice, speed=None, task_id=task_id
        )
        audio_rel = resource_relative_path("audio", rid)
        await _speed_up_wav(pm.get_project_path(project_name) / audio_rel)
        content = await asyncio.to_thread((pm.get_project_path(project_name) / audio_rel).read_bytes)
        duration = await probe_audio_duration_seconds(content, ".wav")
        synthesized[line.index] = {
            "audio_path": audio_rel,
            "audio_duration_seconds": duration,
            "audio_voice": voice,
            "audio_text": line.text_vi,
        }

    if synthesized:
        await update_unit_dub_lines(pm, project_name, script_file, unit_id, _audio_registrar(synthesized))
    return sorted(synthesized)


def _audio_registrar(synthesized: dict[int, dict[str, Any]]):
    """登记合成结果的改写函数。合成期间台词若又被改动，对应行的音频不登记（保持过期）。"""

    def register(lines: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        changed = False
        for line in lines:
            index = line.get("index")
            update = synthesized.get(index) if isinstance(index, int) else None
            if update is not None and update["audio_text"] == line.get("text_vi"):
                line.update(update)
                changed = True
        return lines if changed else None

    return register


def _script_file_of(payload: dict[str, Any]) -> str:
    script_file = payload.get("script_file")
    if not isinstance(script_file, str) or not script_file:
        raise ValueError("dub 任务需要 payload.script_file")
    return script_file


async def execute_dub_tts_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    script_file = _script_file_of(payload)
    synthesized = await _synthesize_unit(
        project_name, resource_id, script_file, payload, user_id=user_id, task_id=task_id
    )
    project = await asyncio.to_thread(get_project_manager().load_project, project_name)
    return {
        "unit_id": resource_id,
        "synthesized": synthesized,
        "resource_type": "audio",
        "narrator_gender": project_narrator_gender(project),
    }


async def execute_dub_auto_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """一个视频的完整配音：缺台词先写台词 → 检测原台词开始时间 → 合成音频。

    台词写不出来整个任务失败（后面没有东西可配）；检测不可用或没对上只记在结果里、不拦合成，
    开始时间退回估算。
    """
    script_file = _script_file_of(payload)
    pm = get_project_manager()
    project = await asyncio.to_thread(pm.load_project, project_name)
    if project_dub_target_language(project) is None:
        raise ValueError("项目未启用配音（dub_target_language）")

    def current_unit() -> dict[str, Any] | None:
        return find_unit(pm.load_script(project_name, script_file), resource_id)

    unit = await asyncio.to_thread(current_unit)
    if unit is None:
        raise ValueError(f"video unit 不存在: {resource_id}")
    steps: dict[str, Any] = {}

    if not unit_utterances(str(unit.get("text") or "")):
        # 没有台词也没有画外音的视频无需配音：直接成功，不是失败
        return {"unit_id": resource_id, "steps": {"lines": "not_needed"}, "synthesized": [], "resource_type": "audio"}

    if not unit.get("dub_lines"):
        from server.text_generation import generate_reference_dub_lines  # 延迟导入：文本生成链很重，只在缺台词时用

        episode = payload.get("episode")
        if type(episode) is not int:
            raise ValueError("dub_auto 任务缺台词时需要 payload.episode")
        await generate_reference_dub_lines(project_name, episode, [resource_id], projects=pm)
        unit = await asyncio.to_thread(current_unit)
        if unit is None or not unit.get("dub_lines"):
            raise ValueError(f"unit {resource_id} 没有写出越南语台词")
        steps["lines"] = "written"
    else:
        steps["lines"] = "existing"

    video = (unit.get("generated_assets") or {}).get("video_clip")
    if not video:
        steps["timing"] = "no_video"
    elif payload.get("skip_timing") or all(
        line.get("detected_start_seconds") is not None for line in unit["dub_lines"]
    ):
        steps["timing"] = "skipped" if payload.get("skip_timing") else "existing"
    else:
        try:
            results = await detect_start_times(pm, project_name, script_file, [resource_id], language=None)
            steps["timing"] = results[0]["status"] if results else "skipped"
        except AsrError as exc:
            logger.warning("配音开始时间检测不可用，按估算排布 unit=%s: %s", resource_id, exc)
            steps["timing"] = "unavailable"
            steps["timing_error"] = str(exc)

    synthesized = await _synthesize_unit(
        project_name, resource_id, script_file, payload, user_id=user_id, task_id=task_id
    )
    return {"unit_id": resource_id, "steps": steps, "synthesized": synthesized, "resource_type": "audio"}
