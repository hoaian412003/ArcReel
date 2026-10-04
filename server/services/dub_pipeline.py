"""越南语配音流水线的共用部件：剧本写入串行化、开始时间检测、识别并发闸门。

多个视频的配音任务会同时跑（audio lane 并发 4），它们都要读改写同一份剧本；剧本写入带 revision 校验，
并发写会互相撞车，所以同一份剧本的配音写入统一排队。语音识别模型常驻约 2 GB 内存，同一时刻只放一个
识别进程在跑，其余视频的识别排队，合成与其它步骤不受影响。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any

from lib.infra.path_safety import safe_exists, safe_join
from lib.project.project_manager import ProjectManager
from lib.script.script_batch_edit import ScriptBatchEditCommand, ScriptBatchEditor, script_revision
from lib.speech.asr_alignment import align_lines_to_segments
from lib.speech.asr_runner import AsrError, transcribe_files

logger = logging.getLogger(__name__)

#: Whisper 能识别的剧本源语言；项目源语言不在其中（或未设置）时按中文识别。
ASR_LANGUAGES = frozenset({"zh", "en", "vi", "ja", "ko"})


class DubWriteError(RuntimeError):
    """配音台词写回剧本失败。"""


# asyncio 的锁与信号量绑定事件循环；按循环各建一份，测试里多个循环依次跑也不会串。
_script_locks: dict[tuple[int, str], asyncio.Lock] = {}
_asr_gates: dict[int, asyncio.Semaphore] = {}


def script_write_lock(project_name: str, script_file: str) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), f"{project_name}\0{script_file}")
    return _script_locks.setdefault(key, asyncio.Lock())


def asr_gate() -> asyncio.Semaphore:
    return _asr_gates.setdefault(id(asyncio.get_running_loop()), asyncio.Semaphore(1))


def find_unit(script: dict[str, Any], unit_id: str) -> dict[str, Any] | None:
    return next((u for u in script.get("video_units") or [] if u.get("unit_id") == unit_id), None)


async def update_unit_dub_lines(
    pm: ProjectManager,
    project_name: str,
    script_file: str,
    unit_id: str,
    mutate: Callable[[list[dict[str, Any]]], list[dict[str, Any]] | None],
) -> bool:
    """在写锁内读最新剧本、让 ``mutate`` 改该 unit 的配音行并写回；``mutate`` 返回 ``None`` 即不写。

    返回是否真的写了。找不到 unit 视为没有可写的目标，不报错。
    """
    async with script_write_lock(project_name, script_file):
        script = await asyncio.to_thread(pm.load_script, project_name, script_file)
        unit = find_unit(script, unit_id)
        if unit is None:
            return False
        updated = mutate([dict(line) for line in unit.get("dub_lines") or []])
        if updated is None:
            return False
        command = ScriptBatchEditCommand.model_validate(
            {
                "script": script_file,
                "expected_revision": script_revision(script),
                "operations": [{"op": "update", "id": unit_id, "fields": {"dub_lines": updated}}],
            }
        )
        edit = await asyncio.to_thread(ScriptBatchEditor(pm).execute, project_name, command)
        if not edit.success:
            raise DubWriteError(f"写回配音台词失败: {edit.model_dump_json()}")
        return True


class _DetectedStarts:
    """把识别结果对回一个 unit 的配音行，并记下检测到几条。"""

    def __init__(self, transcript: Any) -> None:
        self._transcript = transcript
        self.detected = 0

    def apply(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = sorted(lines, key=lambda line: line.get("index", 0))
        starts = align_lines_to_segments([str(line.get("source_text") or "") for line in ordered], self._transcript)
        self.detected = 0
        for line, start in zip(ordered, starts, strict=True):
            line["detected_start_seconds"] = start
            self.detected += start is not None
        return lines


def _timing_result(unit_id: str, status: str, detected: int, total: int) -> dict[str, Any]:
    return {"unit_id": unit_id, "status": status, "detected": detected, "total": total}


async def detect_start_times(
    pm: ProjectManager,
    project_name: str,
    script_file: str,
    unit_ids: Sequence[str],
    *,
    language: str | None,
) -> list[dict[str, Any]]:
    """用语音识别从成片读出各 unit 原台词的开始时间，写入配音行的 ``detected_start_seconds``。

    整批 unit 共用一次模型加载；识别不可用时抛 :class:`AsrError`，单个 unit 对不上则在结果里标出。
    结局：``detected`` / ``no_speech`` / ``no_match`` / ``no_video`` / ``failed``。
    """
    project_dir = pm.get_project_path(project_name)
    script = await asyncio.to_thread(pm.load_script, project_name, script_file)
    results: list[dict[str, Any]] = []
    videos: dict[str, Any] = {}
    for unit_id in unit_ids:
        unit = find_unit(script, unit_id)
        if unit is None or not unit.get("dub_lines"):
            continue
        video_rel = (unit.get("generated_assets") or {}).get("video_clip")
        if video_rel and safe_exists(project_dir, video_rel):
            videos[unit_id] = safe_join(project_dir, video_rel)
        else:
            results.append(_timing_result(unit_id, "no_video", 0, len(unit["dub_lines"])))
    if not videos:
        return results

    project = await asyncio.to_thread(pm.load_project, project_name)
    source_language = str(project.get("source_language") or "").lower()
    async with asr_gate():
        transcripts = await transcribe_files(
            list(dict.fromkeys(videos.values())),
            language=source_language if source_language in ASR_LANGUAGES else (language or "zh"),
        )

    for unit_id, video in videos.items():
        transcript = transcripts.get(video)
        total = len((find_unit(script, unit_id) or {}).get("dub_lines") or [])
        if not isinstance(transcript, list):
            results.append(_timing_result(unit_id, "failed", 0, total))
            continue
        outcome = _DetectedStarts(transcript)
        await update_unit_dub_lines(pm, project_name, script_file, unit_id, outcome.apply)
        counted = {"detected": outcome.detected, "total": total}
        status = "detected" if counted["detected"] else ("no_speech" if not transcript else "no_match")
        results.append(_timing_result(unit_id, status, counted["detected"], counted["total"]))
    return results


__all__ = [
    "ASR_LANGUAGES",
    "AsrError",
    "DubWriteError",
    "asr_gate",
    "detect_start_times",
    "find_unit",
    "script_write_lock",
    "update_unit_dub_lines",
]
