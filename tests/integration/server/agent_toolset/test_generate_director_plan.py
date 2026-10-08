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
    reference_director_brief,
    submit_reference_director_plan,
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
# 参考生视频：导演由子智能体撰写，每个单元一段自由文本，服务端只做机械检查
# ---------------------------------------------------------------------------

_REF_TEXTS = ["@[阿离] 推开山门。\n@[阿离]{我回来了。}", "@[阿离] 望向远处的灯火。"]

_DIRECTION_1 = (
    "阿离回到阔别三年的山门，既想念又怕被认出。第 0 秒全景，阿离居中，固定机位，她推开山门；第 4 秒切近景推近，"
    "看清她侧脸的迟疑。阿离动作迟疑，呼吸放慢，声音清亮偏低、语速缓。台词「我回来了。」落在第二镜，轻声，像对自己说。"
    "门轴吱呀，远处钟声，无配乐。末帧停在门槛内；开场山门外黄昏，结束山门内黄昏。"
)
_DIRECTION_2 = (
    "阿离站在山门内望向远处的灯火，神情由紧绷转为松动。单镜头缓慢拉远，风声渐起，无台词，无配乐。"
    "末帧是灯火在她身后虚化；开场与上一单元的结束衔接：山门内，黄昏，阿离停在门槛内。"
)


def _ref_script(*, directed: bool = False, legacy: bool = True) -> dict[str, Any]:
    units: list[dict[str, Any]] = []
    for number, text in enumerate(_REF_TEXTS, start=1):
        unit: dict[str, Any] = {
            "unit_id": f"E1U0{number}",
            "text": text,
            "duration_seconds": 8,
            "pending_authoring": True,
        }
        if legacy:
            unit["beats"] = [{"at_pct": 0, "action": "存量节点"}]
            unit["brief"] = "存量说明"
        if directed:
            unit["direction"] = _DIRECTION_1 if number == 1 else _DIRECTION_2
        units.append(unit)
    script: dict[str, Any] = {"episode": 1, "title": "第一集", "content_mode": "drama", "video_units": units}
    if directed:
        script["director_plan"] = _plan()
    return script


async def _submit(pm: ProjectManager, *, plan: bool = True, units: dict[str, str] | None = None) -> str:
    submitted = {"E1U01": _DIRECTION_1, "E1U02": _DIRECTION_2} if units is None else units
    result = await submit_reference_director_plan(
        episode=1,
        director_plan=_plan() if plan else None,
        units=[{"unit_id": unit_id, "direction": text} for unit_id, text in submitted.items()],
        project_name="demo",
        projects=pm,
    )
    return result.message


@pytest.mark.parametrize("content_mode", ["narration", "drama"])
async def test_reference_direction_is_stored_as_one_free_text_per_unit(tmp_path: Path, content_mode: str) -> None:
    pm = _project(tmp_path, content_mode, "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")

    message = await _submit(pm)

    saved = _saved(pm)
    first = saved["video_units"][0]
    assert first["direction"] == _DIRECTION_1
    assert saved["director_plan"]["tone"] == "先压后松"
    # 自由文本取代分项字段：存量的分项导演字段整组清掉，待编写标记不动
    assert "beats" not in first
    assert "brief" not in first
    assert first["pending_authoring"] is True
    assert direction_review.review_status(pm.load_project("demo"), saved, 1) == "pending_review"
    assert "待人工审阅" in message


async def test_reference_direction_can_be_submitted_in_batches(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(legacy=False), "episode_1.json")

    first = await _submit(pm, units={"E1U01": _DIRECTION_1})

    assert "E1U02" in first
    assert "尚未完成" in first
    saved = _saved(pm)
    assert direction_review.review_status(pm.load_project("demo"), saved, 1) == "no_direction"
    assert "direction" not in saved["video_units"][1]

    done = await _submit(pm, plan=False, units={"E1U02": _DIRECTION_2})

    assert "待人工审阅" in done
    assert direction_review.review_status(pm.load_project("demo"), _saved(pm), 1) == "pending_review"


@pytest.mark.parametrize(
    ("units", "fragment"),
    [
        ({"E1U01": "太短"}, "至少"),
        ({"E1U09": _DIRECTION_1}, "不在第 1 集"),
    ],
    ids=["too_short", "unknown_unit"],
)
async def test_reference_direction_that_fails_the_checks_writes_nothing(
    tmp_path: Path, units: dict[str, str], fragment: str
) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")
    before = _saved(pm)

    with pytest.raises(TextGenerationError, match=fragment):
        await _submit(pm, units=units)

    assert _saved(pm) == before


async def test_reference_director_plan_needs_a_turn_in_the_arc(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")

    with pytest.raises(TextGenerationError, match="arc"):
        await submit_reference_director_plan(
            episode=1,
            director_plan={
                "episode_intent": "回山门",
                "arc": [{"beat": "回来", "units": [1]}],
                "tone": "压抑",
                "motifs": [],
            },
            units=[],
            project_name="demo",
            projects=pm,
        )


async def test_reference_direction_warns_when_a_spoken_line_is_missing(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")
    without_line = _DIRECTION_1.replace("我回来了。", "她开口")

    message = await _submit(pm, units={"E1U01": without_line})

    assert "我回来了。" in message
    assert "提示" in message
    assert _saved(pm)["video_units"][0]["direction"] == without_line


async def test_reference_direction_warns_when_a_shown_character_has_no_reference(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    script = _ref_script()
    script["video_units"][1]["text"] = "灯火渐渐亮起。"
    pm.save_script("demo", script, "episode_1.json")

    message = await _submit(pm, units={"E1U02": _DIRECTION_2})

    assert "E1U02" in message
    assert "阿离" in message
    assert "没有 @ 引用" in message


async def test_reference_direction_stays_quiet_when_the_shown_character_is_referenced(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")

    message = await _submit(pm)

    assert "没有 @ 引用" not in message


async def test_reference_revision_replaces_only_the_named_unit(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(directed=True, legacy=False), "episode_1.json")
    revised = _DIRECTION_2.replace("缓慢拉远", "快速拉远")

    await _submit(pm, plan=False, units={"E1U02": revised})

    saved = _saved(pm)
    assert saved["video_units"][0]["direction"] == _DIRECTION_1
    assert saved["video_units"][1]["direction"] == revised


async def test_reference_brief_lists_the_units_and_the_submit_call(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")

    brief = (await reference_director_brief(episode=1, project_name="demo", projects=pm)).message

    assert "@[阿离] 推开山门" in brief
    assert "E1U01" in brief
    assert "submit_director_plan" in brief


async def test_reference_brief_marks_the_targeted_units(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(directed=True, legacy=False), "episode_1.json")

    brief = (await reference_director_brief(episode=1, entry_ids=["E1U02"], project_name="demo", projects=pm)).message

    assert "（本次重做）" in brief
    assert "只为下列单元写导演说明：E1U02" in brief


async def test_reference_direction_tools_serve_only_reference_video_projects(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "storyboard")

    with pytest.raises(TextGenerationError, match="参考生视频"):
        await reference_director_brief(episode=1, project_name="demo", projects=pm)
    with pytest.raises(TextGenerationError, match="参考生视频"):
        await _submit(pm)


async def test_generate_director_plan_refuses_reference_video_projects(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(), "episode_1.json")

    with pytest.raises(TextGenerationError, match="submit_director_plan"):
        await _generate(pm)


_VOICEOVER_TEXT = "@[阿离] 推开山门。\n{山外传来钟声。}\n{远处有人在喊她。}"


def _voiceover_script() -> dict[str, Any]:
    script = _ref_script(legacy=False)
    script["video_units"][0]["text"] = _VOICEOVER_TEXT
    return script


def _enable_dub(pm: ProjectManager) -> None:
    pm.update_project("demo", lambda project: project.__setitem__("dub_target_language", "vi"))


async def _submit_cues(pm: ProjectManager, cues: list[dict[str, Any]] | None) -> str:
    item: dict[str, Any] = {"unit_id": "E1U01", "direction": _DIRECTION_1}
    if cues is not None:
        item["speech_cues"] = cues
    result = await submit_reference_director_plan(
        episode=1, director_plan=_plan(), units=[item], project_name="demo", projects=pm
    )
    return result.message


def _cue(index: int, start: float, end: float, gender: str | None = "male") -> dict[str, Any]:
    cue: dict[str, Any] = {"index": index, "start_seconds": start, "end_seconds": end}
    if gender:
        cue["gender"] = gender
    return cue


async def test_speech_cues_are_stored_sorted_on_the_unit(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _voiceover_script(), "episode_1.json")
    _enable_dub(pm)

    await _submit_cues(pm, [_cue(1, 5.0, 8.0, "female"), _cue(0, 1.0, 4.0, "male")])

    assert [cue["index"] for cue in _saved(pm)["video_units"][0]["speech_cues"]] == [0, 1]


async def test_voiceover_without_speech_cues_is_refused_when_dubbing_is_on(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _voiceover_script(), "episode_1.json")
    _enable_dub(pm)

    with pytest.raises(TextGenerationError) as raised:
        await _submit_cues(pm, None)

    assert "画外音" in str(raised.value)
    assert "direction" not in _saved(pm)["video_units"][0]


async def test_voiceover_cues_need_a_gender(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _voiceover_script(), "episode_1.json")
    _enable_dub(pm)

    with pytest.raises(TextGenerationError) as raised:
        await _submit_cues(pm, [_cue(0, 1.0, 4.0, None), _cue(1, 5.0, 8.0)])

    assert "gender" in str(raised.value)


async def test_voiceover_without_cues_is_fine_when_dubbing_is_off(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _voiceover_script(), "episode_1.json")

    await _submit_cues(pm, None)

    assert "speech_cues" not in _saved(pm)["video_units"][0]


@pytest.mark.parametrize(
    ("cues", "reason"),
    [
        ([_cue(0, 1.0, 4.0), _cue(1, 5.0, 9.0)], "超出单元时长"),
        ([_cue(0, 5.0, 6.0), _cue(1, 1.0, 2.0)], "递增"),
        ([_cue(0, 1.0, 2.0), _cue(0, 3.0, 4.0), _cue(1, 5.0, 6.0)], "重复"),
        ([_cue(0, 1.0, 2.0), _cue(7, 3.0, 4.0)], "不是该单元的发声"),
    ],
)
async def test_invalid_speech_cues_write_nothing(tmp_path: Path, cues: list[dict[str, Any]], reason: str) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _voiceover_script(), "episode_1.json")
    _enable_dub(pm)

    with pytest.raises(TextGenerationError) as raised:
        await _submit_cues(pm, cues)

    assert reason in str(raised.value)
    assert "direction" not in _saved(pm)["video_units"][0]


async def test_reference_brief_numbers_the_utterances_for_speech_cues(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _voiceover_script(), "episode_1.json")

    result = await reference_director_brief(episode=1, project_name="demo", projects=pm)

    assert "speech_cues" in result.message
    assert "- [0] 画外音：山外传来钟声。" in result.message
    assert "- [1] 画外音：远处有人在喊她。" in result.message


async def test_spoken_dialogue_also_needs_speech_cues_when_dubbing_is_on(tmp_path: Path) -> None:
    pm = _project(tmp_path, "drama", "reference_video")
    pm.save_script("demo", _ref_script(legacy=False), "episode_1.json")
    _enable_dub(pm)

    with pytest.raises(TextGenerationError) as raised:
        await _submit_cues(pm, None)

    assert "speech_cues" in str(raised.value)

    await _submit_cues(pm, [_cue(0, 3.0, 5.0, None)])

    assert _saved(pm)["video_units"][0]["speech_cues"][0]["start_seconds"] == 3.0
