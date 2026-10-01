"""``generate_reference_dub_lines``：为已有 unit 回填越南语配音台词并经批量编辑器写回正式剧本。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.backends.text_backends.base import TextTaskType
from lib.project.project_manager import ProjectManager
from server import text_generation as mod
from server.text_generation import TextGenerationError

_CURRENT_LINE = {"index": 0, "speaker": "李明", "gender": "male", "source_text": "再见", "text_vi": "Tạm biệt."}


def _unit(unit_id: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"unit_id": unit_id, "text": text, "duration_seconds": 8, "source_text": "", "generated_assets": {}, **extra}


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path))
    manager.create_project("demo", content_mode="narration")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.update_project(
        "demo",
        lambda project: project.update(
            {
                "generation_mode": "reference_video",
                "dub_target_language": "vi",
                "narrator_gender": "female",
                "characters": {"李明": {"description": "少年", "gender": "male"}},
                "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            }
        ),
    )
    manager.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "video_units": [
                _unit("E1U01", "@[李明]：{你好}\n@[李明]：{天黑了}"),
                _unit("E1U02", "远景。风吹过旷野。"),
                _unit("E1U03", "@[李明]：{再见}", dub_lines=[_CURRENT_LINE]),
            ],
        },
        "episode_1.json",
    )
    return manager


class _Recorder:
    def __init__(self, response_units: list[dict[str, Any]]) -> None:
        self.response_units = response_units
        self.prompts: list[str] = []
        self.task_types: list[Any] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = self

        class _Generator:
            async def generate(self, request, project_name=None):
                recorder.prompts.append(request.prompt)

                class _Result:
                    text = json.dumps({"units": recorder.response_units}, ensure_ascii=False)

                return _Result()

        async def fake_create(task_type, project_name=None, **kwargs):
            recorder.task_types.append(task_type)
            return _Generator()

        monkeypatch.setattr(mod.TextGenerator, "create", fake_create)


def _lines(*texts: str) -> list[dict[str, Any]]:
    return [{"index": i, "speaker": "李明", "gender": "male", "text_vi": text} for i, text in enumerate(texts)]


async def test_backfill_writes_dub_lines_only_for_missing_units(pm: ProjectManager, monkeypatch) -> None:
    recorder = _Recorder([{"unit_id": "E1U01", "dub_lines": _lines("Chào anh.", "Trời tối rồi.")}])
    recorder.install(monkeypatch)

    result = await mod.generate_reference_dub_lines("demo", 1, None, projects=pm)

    assert "E1U01" in result.message
    assert recorder.task_types == [TextTaskType.TRANSLATE_DUB]
    prompt = recorder.prompts[0]
    assert "## E1U01" in prompt
    assert "## E1U02" not in prompt  # 没有发声
    assert "## E1U03" not in prompt  # 配音行仍是现行的
    assert "李明（male）" in prompt
    units = {unit["unit_id"]: unit for unit in pm.load_script("demo", "episode_1.json")["video_units"]}
    assert [line["source_text"] for line in units["E1U01"]["dub_lines"]] == ["你好", "天黑了"]
    assert units["E1U03"]["dub_lines"] == [_CURRENT_LINE]
    assert "dub_lines" not in units["E1U02"]


async def test_named_unit_is_retranslated_even_when_current(pm: ProjectManager, monkeypatch) -> None:
    recorder = _Recorder([{"unit_id": "E1U03", "dub_lines": _lines("Hẹn gặp lại.")}])
    recorder.install(monkeypatch)

    await mod.generate_reference_dub_lines("demo", 1, ["E1U03"], projects=pm)

    units = {unit["unit_id"]: unit for unit in pm.load_script("demo", "episode_1.json")["video_units"]}
    assert units["E1U03"]["dub_lines"][0]["text_vi"] == "Hẹn gặp lại."


async def test_units_failing_validation_are_reported_and_not_written(pm: ProjectManager, monkeypatch) -> None:
    recorder = _Recorder([{"unit_id": "E1U01", "dub_lines": _lines("Chào anh.")}])  # 少一行
    recorder.install(monkeypatch)

    with pytest.raises(TextGenerationError, match="E1U01"):
        await mod.generate_reference_dub_lines("demo", 1, None, projects=pm)

    assert "dub_lines" not in pm.load_script("demo", "episode_1.json")["video_units"][0]


async def test_units_are_translated_in_chunks_of_ten(pm: ProjectManager, monkeypatch) -> None:
    units = [_unit(f"E1U{i:02d}", f"@[李明]：{{话{i}}}") for i in range(1, 13)]
    script = pm.load_script("demo", "episode_1.json")
    script["video_units"] = units
    pm.save_script("demo", script, "episode_1.json")
    recorder = _Recorder([{"unit_id": u["unit_id"], "dub_lines": _lines("Một câu.")} for u in units])
    recorder.install(monkeypatch)

    await mod.generate_reference_dub_lines("demo", 1, None, projects=pm)

    assert len(recorder.prompts) == 2
    assert "## E1U10" in recorder.prompts[0]
    assert "## E1U11" in recorder.prompts[1]


async def test_backfill_requires_dubbing_to_be_enabled(pm: ProjectManager) -> None:
    pm.update_project("demo", lambda project: project.pop("dub_target_language"))
    with pytest.raises(TextGenerationError, match="未启用配音"):
        await mod.generate_reference_dub_lines("demo", 1, None, projects=pm)


async def test_nothing_to_backfill_does_not_call_the_model(pm: ProjectManager, monkeypatch) -> None:
    recorder = _Recorder([])
    recorder.install(monkeypatch)
    result = await mod.generate_reference_dub_lines("demo", 1, ["E1U02"], projects=pm)
    assert "无需回填" in result.message
    assert recorder.prompts == []


async def test_queued_text_task_runs_the_backfill_and_reports_its_message(
    pm: ProjectManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.tool_runtime import execute_queued_text_task

    monkeypatch.setenv("ARCREEL_DATA_DIR", str(pm.data_root))
    _Recorder([{"unit_id": "E1U01", "dub_lines": _lines("Chào anh.", "Trời tối rồi.")}]).install(monkeypatch)
    task = {
        "task_id": "t1",
        "task_type": "text_reference_dub_lines",
        "project_name": "demo",
        "payload": {"episode": 1, "entry_ids": []},
    }

    result = await execute_queued_text_task(task)

    assert "E1U01" in result["message"]
    saved = pm.load_script("demo", "episode_1.json")["video_units"][0]
    assert len(saved["dub_lines"]) == 2
