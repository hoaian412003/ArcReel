"""导演步骤的生成工具：prompt 构建、产出校验、写回正式脚本与修订范围。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.project.project_manager import ProjectManager
from lib.script import direction_review
from server.text_generation import (
    TextGenerationError,
    TextGenerationRequest,
    director_preflight,
    generate_director_plan,
)
from tests.fakes import FakeConfigResolver


def _camera(**overrides: str) -> dict[str, str]:
    return {
        "angle": "低角度",
        "shot_size": "特写",
        "movement": "缓慢推进",
        "intent": "托出对方压人的气势",
        **overrides,
    }


def _direction(**camera: str) -> dict[str, Any]:
    return {
        "camera": _camera(**camera),
        "performance": [{"character": "阿离", "note": "语速放慢，目光不动"}],
        "continuity_in": "屋檐下",
        "continuity_out": "屋檐下",
    }


def _plan() -> dict[str, Any]:
    return {
        "episode_intent": "让观众感到压迫",
        "arc": [{"beat": "压迫", "units": [1]}, {"beat": "释然", "units": [2]}],
        "tone": "先压后松",
        "motifs": [],
    }


def _model_output(ids: list[str], **camera: str) -> str:
    return json.dumps(
        {
            "director_plan": _plan(),
            "entries": [{"id": entry_id, "direction": _direction(**camera)} for entry_id in ids],
        },
        ensure_ascii=False,
    )


class _Generator:
    """替身文本模型：返回预设文本，可在调用期间执行一个副作用（模拟并发写入）。"""

    def __init__(self, text: str, during_call=None) -> None:
        self.text = text
        self.during_call = during_call
        self.prompts: list[str] = []

    async def generate(self, request, project_name=None):
        self.prompts.append(request.prompt)
        if self.during_call is not None:
            self.during_call()

        class _Result:
            text = self.text

        return _Result()


def _use_generator(monkeypatch: pytest.MonkeyPatch, generator: _Generator) -> None:
    from server import text_generation as mod

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return generator

    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)


def _script(content_mode: str = "narration", *, pending: bool = True, directed: bool = False) -> dict[str, Any]:
    kind, id_field, chars_field = (
        ("segments", "segment_id", "characters_in_segment")
        if content_mode == "narration"
        else ("scenes", "scene_id", "characters_in_scene")
    )
    entries: list[dict[str, Any]] = []
    for number in (1, 2):
        entry: dict[str, Any] = {
            id_field: f"E1S0{number}",
            "duration_seconds": 4,
            chars_field: ["阿离"],
            "scenes": [],
            "props": [],
        }
        if content_mode == "narration":
            entry["novel_text"] = f"第 {number} 段原文"
        else:
            entry["scene_description"] = f"第 {number} 幕画面"
            entry["utterances"] = [{"kind": "dialogue", "speaker": "阿离", "text": "你来了。"}]
        if pending:
            entry["pending_authoring"] = True
        else:
            entry["image_prompt"] = "画面"
            entry["video_prompt"] = "动作" if content_mode == "narration" else {"action": "动作", "ambiance_audio": ""}
        if directed:
            entry["direction"] = _direction()
        entries.append(entry)
    script: dict[str, Any] = {"episode": 1, "title": "第一集", "content_mode": content_mode, kind: entries}
    if directed:
        script["director_plan"] = _plan()
    return script


def _project(tmp_path: Path, content_mode: str = "narration", generation_mode: str = "storyboard") -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    pm.add_character("demo", "阿离", "少女")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    pm.update_project("demo", lambda project: project.__setitem__("generation_mode", generation_mode))
    return pm


async def _generate(pm: ProjectManager, **request: Any) -> str:
    result = await generate_director_plan(
        TextGenerationRequest(episode=1, **request),
        project_name="demo",
        projects=pm,
        config_resolver=FakeConfigResolver(),
    )
    return result.message


def _saved(pm: ProjectManager) -> dict[str, Any]:
    return pm.load_script_readonly("demo", "episode_1.json")


@pytest.mark.parametrize("content_mode", ["narration", "drama"])
async def test_whole_episode_direction_is_written_into_the_formal_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_mode: str
) -> None:
    pm = _project(tmp_path, content_mode)
    pm.save_script("demo", _script(content_mode), "episode_1.json")
    generator = _Generator(_model_output(["E1S01", "E1S02"]))
    _use_generator(monkeypatch, generator)

    message = await _generate(pm)

    kind = "segments" if content_mode == "narration" else "scenes"
    saved = _saved(pm)
    assert saved["director_plan"]["tone"] == "先压后松"
    assert [entry["direction"]["camera"]["intent"] for entry in saved[kind]] == ["托出对方压人的气势"] * 2
    assert all(entry["pending_authoring"] is True for entry in saved[kind])
    assert direction_review.review_status(pm.load_project("demo"), saved, 1) == "pending_review"
    assert "E1S01" in generator.prompts[0]
    assert "待人工审阅" in message


async def test_output_that_fails_validation_is_rejected_and_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(), "episode_1.json")
    _use_generator(monkeypatch, _Generator(_model_output(["E1S01", "E1S02"], intent="好看")))

    with pytest.raises(TextGenerationError) as excinfo:
        await _generate(pm)

    assert "camera.intent" in str(excinfo.value)
    assert "director_plan" not in _saved(pm)


async def test_output_must_cover_exactly_the_requested_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(), "episode_1.json")
    _use_generator(monkeypatch, _Generator(_model_output(["E1S01"])))

    with pytest.raises(TextGenerationError, match="director_entries_mismatch"):
        await _generate(pm)


async def test_unparsable_model_output_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(), "episode_1.json")
    _use_generator(monkeypatch, _Generator("not json"))

    with pytest.raises(TextGenerationError, match="导演产出"):
        await _generate(pm)


async def test_revision_replaces_only_the_named_entries_and_keeps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(directed=True), "episode_1.json")
    _use_generator(monkeypatch, _Generator(_model_output(["E1S02"], angle="俯视", intent="俯视让人物显得渺小无助")))

    message = await _generate(pm, entry_ids=("E1S02",))

    segments = _saved(pm)["segments"]
    assert segments[0]["direction"]["camera"]["angle"] == "低角度"
    assert segments[1]["direction"]["camera"]["angle"] == "俯视"
    assert "E1S02" in message


async def test_revision_prompt_carries_the_reviewers_note_and_marks_the_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    script = _script(directed=True)
    pm.save_script("demo", script, "episode_1.json")
    fingerprint = direction_review.direction_fingerprint(_saved(pm), "segments")
    assert fingerprint is not None
    pm.update_project(
        "demo",
        lambda project: direction_review.apply_decision(
            project,
            1,
            fingerprint=fingerprint,
            decision="changes",
            decided_at="2026-10-03T00:00:00+00:00",
            note="第二个镜头太平",
            entry_ids=["E1S02"],
        ),
    )
    generator = _Generator(_model_output(["E1S02"]))
    _use_generator(monkeypatch, generator)

    await _generate(pm, entry_ids=("E1S02",))

    prompt = generator.prompts[0]
    assert "第二个镜头太平" in prompt
    assert "（本次重做）" in prompt


async def test_revising_a_partially_directed_episode_asks_for_a_whole_episode_run(
    tmp_path: Path,
) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(), "episode_1.json")

    with pytest.raises(TextGenerationError, match="整集导演"):
        await _generate(pm, entry_ids=("E1S01",))


async def test_unknown_entry_ids_are_refused(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(directed=True), "episode_1.json")

    with pytest.raises(TextGenerationError, match="E1S99"):
        await _generate(pm, entry_ids=("E1S99",))


async def test_authored_episodes_are_not_directed(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(pending=False), "episode_1.json")

    with pytest.raises(TextGenerationError, match="没有待编写"):
        await _generate(pm)


@pytest.mark.parametrize("generation_mode", ["storyboard", "reference_video"])
async def test_ad_projects_have_no_director_step(tmp_path: Path, generation_mode: str) -> None:
    pm = _project(tmp_path, "ad", generation_mode)

    with pytest.raises(TextGenerationError, match="广告"):
        await _generate(pm)


async def test_missing_formal_script_is_refused(tmp_path: Path) -> None:
    pm = _project(tmp_path)

    with pytest.raises(TextGenerationError, match="尚无正式脚本"):
        await _generate(pm)


async def test_script_changed_during_the_model_call_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(), "episode_1.json")

    def concurrent_edit() -> None:
        edited = _script()
        edited["segments"][0]["novel_text"] = "被别处改过的原文"
        pm.save_script("demo", edited, "episode_1.json")

    _use_generator(monkeypatch, _Generator(_model_output(["E1S01", "E1S02"]), during_call=concurrent_edit))

    with pytest.raises(TextGenerationError, match="被改动"):
        await _generate(pm)

    saved = _saved(pm)
    assert saved["segments"][0]["novel_text"] == "被别处改过的原文"
    assert "director_plan" not in saved


async def test_dry_run_returns_the_prompt_without_calling_the_model(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    pm.save_script("demo", _script(), "episode_1.json")

    message = await _generate(pm, dry_run=True)

    assert message.startswith("DRY RUN")
    assert "第 1 段原文" in message
    assert "director_plan" in message


def test_preflight_applies_the_same_checks_without_calling_the_model(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    with pytest.raises(TextGenerationError, match="尚无正式脚本"):
        director_preflight(pm, "demo", TextGenerationRequest(episode=1))

    pm.save_script("demo", _script(), "episode_1.json")
    director_preflight(pm, "demo", TextGenerationRequest(episode=1))


# ---------------------------------------------------------------------------
# 参考生视频：导演步骤写镜头表、表演与声音、台词落位
# ---------------------------------------------------------------------------

_REF_TEXTS = ["@[阿离] 推开山门。\n@[阿离]{我回来了。}", "@[阿离] 望向远处的灯火。"]


def _ref_script(*, directed: bool = False) -> dict[str, Any]:
    units: list[dict[str, Any]] = []
    for number, text in enumerate(_REF_TEXTS, start=1):
        unit: dict[str, Any] = {
            "unit_id": f"E1U0{number}",
            "text": text,
            "duration_seconds": 8,
            "pending_authoring": True,
            "beats": [{"at_pct": 0, "action": "存量节点"}],
        }
        if directed:
            unit.update(_ref_direction(number))
        units.append(unit)
    script: dict[str, Any] = {"episode": 1, "title": "第一集", "content_mode": "drama", "video_units": units}
    if directed:
        script["director_plan"] = _plan()
    return script


def _ref_direction(number: int, **overrides: Any) -> dict[str, Any]:
    direction: dict[str, Any] = {
        "unit_type": "character_intro" if number == 1 else "establishing",
        "brief": "阿离回到阔别三年的山门，既想念又怕被认出，观众跟着她屏住呼吸。",
        "shots": [
            {"at_seconds": 0, "framing": "全景，阿离居中", "camera": {"type": "static_shot"}, "action": "阿离推开山门"},
            {
                "at_seconds": 4,
                "framing": "近景，阿离侧脸",
                "camera": {"type": "push_in", "amplitude": "small", "speed": "slow"},
                "action": "她低声开口",
                "why_cut": "新视角：看清她的表情",
            },
        ],
        "performance": [{"character": "阿离", "note": "动作迟疑，呼吸放慢", "voice": "清亮偏低，语速缓"}],
        "lines": [{"speaker": "阿离", "text": "我回来了。", "shot": 2, "delivery": "轻声，像对自己说"}]
        if number == 1
        else [],
        "sounds": "门轴吱呀，远处钟声",
        "music": "",
        "must_not_show": "",
        "end_frame": "阿离停在门槛内",
        "continuity_in": "山门外，黄昏，阿离独自一人",
        "continuity_out": "山门内，黄昏，阿离停在门槛内",
    }
    direction.update(overrides)
    return direction


def _ref_output(ids: list[str], **overrides: Any) -> str:
    numbers = {"E1U01": 1, "E1U02": 2}
    return json.dumps(
        {
            "director_plan": _plan(),
            "units": [{"id": unit_id, "direction": _ref_direction(numbers[unit_id], **overrides)} for unit_id in ids],
        },
        ensure_ascii=False,
    )


@pytest.mark.parametrize("content_mode", ["narration", "drama"])
async def test_reference_units_get_shots_voices_and_line_placement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_mode: str
) -> None:
    pm = _project(tmp_path, content_mode, "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")
    generator = _Generator(_ref_output(["E1U01", "E1U02"]))
    _use_generator(monkeypatch, generator)

    message = await _generate(pm)

    saved = _saved(pm)
    first = saved["video_units"][0]
    assert saved["director_plan"]["tone"] == "先压后松"
    assert first["shots"][1]["camera"] == {"type": "push_in", "amplitude": "small", "speed": "slow"}
    assert first["lines"] == [{"speaker": "阿离", "text": "我回来了。", "shot": 2, "delivery": "轻声，像对自己说"}]
    assert first["performance"][0]["voice"] == "清亮偏低，语速缓"
    assert "beats" not in first
    assert "music" not in first
    assert first["pending_authoring"] is True
    assert direction_review.review_status(pm.load_project("demo"), saved, 1) == "pending_review"
    assert "@[阿离] 推开山门" in generator.prompts[0]
    assert "待人工审阅" in message


async def test_reference_lines_must_match_the_unit_dialogue_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")
    output = json.loads(_ref_output(["E1U01", "E1U02"]))
    output["units"][0]["direction"]["lines"][0]["text"] = "我回来啦。"
    _use_generator(monkeypatch, _Generator(json.dumps(output, ensure_ascii=False)))

    with pytest.raises(TextGenerationError, match="lines 与正文的角色台词不一致"):
        await _generate(pm)

    assert "director_plan" not in _saved(pm)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        (
            {"shots": [{"at_seconds": 2, "framing": "全景", "camera": {"type": "static_shot"}, "action": "推门"}]},
            "第一镜",
        ),
        (
            {
                "shots": [
                    {"at_seconds": 0, "framing": "全景", "camera": {"type": "static_shot"}, "action": "推门"},
                    {
                        "at_seconds": 9,
                        "framing": "近景",
                        "camera": {"type": "push_in"},
                        "action": "开口",
                        "why_cut": "新视角",
                    },
                ]
            },
            "超出单元时长",
        ),
        (
            {
                "shots": [
                    {"at_seconds": 0, "framing": "全景", "camera": {"type": "static_shot"}, "action": "推门"},
                    {"at_seconds": 4, "framing": "近景", "camera": {"type": "push_in"}, "action": "开口"},
                ]
            },
            "why_cut",
        ),
        ({"performance": [{"character": "阿离", "note": "迟疑"}]}, "voice"),
    ],
)
async def test_reference_direction_mechanics_are_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], fragment: str
) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")
    output = json.loads(_ref_output(["E1U01", "E1U02"]))
    output["units"][0]["direction"].update(overrides)
    _use_generator(monkeypatch, _Generator(json.dumps(output, ensure_ascii=False)))

    with pytest.raises(TextGenerationError, match=fragment):
        await _generate(pm)


async def test_reference_revision_replaces_only_the_named_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(directed=True), "episode_1.json")
    _use_generator(monkeypatch, _Generator(_ref_output(["E1U02"], sounds="风声渐大")))

    await _generate(pm, entry_ids=["E1U02"])

    saved = _saved(pm)
    assert saved["video_units"][0]["sounds"] == "门轴吱呀，远处钟声"
    assert saved["video_units"][1]["sounds"] == "风声渐大"
