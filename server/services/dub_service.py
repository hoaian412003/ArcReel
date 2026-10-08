"""越南语配音的读写共用逻辑：配音时间线视图、自动配音入队。

HTTP 路由与 Agent 工具共用同一份实现，两边看到的时间线与入队行为一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.backends.providers import PROVIDER_VIENEU
from lib.generation.generation_queue import GenerationQueue, get_generation_queue
from lib.infra.path_safety import safe_exists
from lib.script.reference_video.dub_lines import align_dub_lines, dub_audio_is_current, unit_utterances
from lib.script.script_models import DubLine, SpeechCue
from lib.speech.dub_timeline import DubTimingInput, dub_overflow_seconds, schedule_dub_lines
from server.services.tasks.dub_tasks import DUB_AUDIO_PAYLOAD, DUB_AUTO_TASK_TYPE


def dub_lines_of(unit: dict) -> list[DubLine]:
    try:
        return [DubLine.model_validate(raw) for raw in unit.get("dub_lines") or []]
    except ValueError:
        return []


def speech_cues_of(unit: dict) -> list[SpeechCue]:
    """导演给这个 unit 定的发声时间窗；结构非法按没有处理。"""
    try:
        return [SpeechCue.model_validate(raw) for raw in unit.get("speech_cues") or []]
    except ValueError:
        return []


def audio_is_current(line: DubLine) -> bool:
    """已登记的音频仍对应现行台词（音色是否变化由合成任务按现行音色判定）。"""
    return dub_audio_is_current(line)


def build_unit_dub(unit: dict) -> dict[str, Any]:
    """一个 unit 的配音时间线：逐行的生效开始时间、时长、音频与过期状态，及超出 unit 时长的秒数。"""
    lines = dub_lines_of(unit)
    text = str(unit.get("text") or "")
    status_by_index = {item.index: item.status for item in align_dub_lines(text, lines)}
    utterance_count = len(unit_utterances(text))
    live = [line for line in lines if line.index < utterance_count]
    planned = {cue.index: cue for cue in speech_cues_of(unit) if cue.index < utterance_count}
    slots = schedule_dub_lines(
        [
            DubTimingInput(
                text=line.text_vi,
                start_override=line.start_seconds,
                audio_duration=line.audio_duration_seconds if audio_is_current(line) else None,
                # 原文改过的配音行，旧文本的检测时间不再可信
                detected_start=line.detected_start_seconds if status_by_index.get(line.index) == "current" else None,
                planned_start=planned[line.index].start_seconds if line.index in planned else None,
            )
            for line in live
        ]
    )
    duration = float(unit.get("duration_seconds") or 0)
    return {
        "unit_id": unit.get("unit_id"),
        "duration_seconds": duration,
        "overflow_seconds": dub_overflow_seconds(slots, duration),
        "lines": [
            {
                "index": line.index,
                "speaker": line.speaker,
                "gender": line.gender,
                "text_vi": line.text_vi,
                "source_text": line.source_text,
                "start_seconds": slot.start_seconds,
                "start_override": line.start_seconds,
                "start_source": slot.source,
                "delayed_seconds": round(slot.delayed_seconds, 2),
                "duration_seconds": slot.duration_seconds,
                "estimated": slot.estimated,
                "audio_path": line.audio_path if audio_is_current(line) else None,
                "stale": status_by_index.get(line.index) in ("stale", "orphan"),
            }
            for line, slot in zip(live, slots, strict=True)
        ],
    }


async def enqueue_auto_dub(
    project_name: str,
    episode: int,
    script: dict,
    script_file: str,
    unit_ids: list[str] | None,
    *,
    skip_timing: bool,
    user_id: str,
    source: str,
    queue: GenerationQueue | None = None,
) -> dict[str, Any]:
    """为每个已有成片且有台词的视频入队一个完整配音任务（写台词 → 检测开始时间 → 合成）；供路由与 Agent 工具共用。

    没有成片（``no_video``）或文本里没有任何台词 / 画外音（``no_speech``）的视频不入队，列在 ``skipped`` 里。
    """
    queue = queue or get_generation_queue()
    task_ids: list[str] = []
    queued: list[str] = []
    skipped: list[dict[str, str]] = []
    wanted = set(unit_ids) if unit_ids is not None else None
    for unit in script.get("video_units") or []:
        unit_id = str(unit.get("unit_id"))
        if wanted is not None and unit_id not in wanted:
            continue
        if not (unit.get("generated_assets") or {}).get("video_clip"):
            skipped.append({"unit_id": unit_id, "reason": "no_video"})
            continue
        if not unit_utterances(str(unit.get("text") or "")):
            # 视频里没有台词也没有画外音：没有可配的内容，既不是失败也不入队
            skipped.append({"unit_id": unit_id, "reason": "no_speech"})
            continue
        payload: dict[str, Any] = {
            "script_file": script_file,
            "episode": episode,
            "skip_timing": skip_timing,
            **DUB_AUDIO_PAYLOAD,
        }
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type=DUB_AUTO_TASK_TYPE,
            media_type="audio",
            resource_id=unit_id,
            payload=payload,
            script_file=script_file,
            source=source,
            user_id=user_id,
            provider_id=PROVIDER_VIENEU,
        )
        task_ids.append(result["task_id"])
        queued.append(unit_id)
    return {"success": True, "task_ids": task_ids, "unit_ids": queued, "skipped": skipped}


def timeline_dub_lines(script: dict, project_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """剪辑时间线要用的配音：单元 ID → 该单元已合成且仍对应现行台词、文件也在盘上的配音行。

    ``start_s`` / ``duration_s`` 相对该单元视频的起点，与时间线片段的入出点同一时间轴。
    """
    result: dict[str, list[dict[str, Any]]] = {}
    for unit in script.get("video_units") or []:
        lines: list[dict[str, Any]] = []
        for line in build_unit_dub(unit)["lines"]:
            audio_path = line["audio_path"]
            if not audio_path or not safe_exists(project_dir, audio_path):
                continue
            lines.append(
                {
                    "index": line["index"],
                    "text_vi": line["text_vi"],
                    "start_s": line["start_seconds"],
                    "duration_s": line["duration_seconds"],
                    "audio_path": audio_path,
                }
            )
        if lines:
            result[str(unit.get("unit_id"))] = lines
    return result
