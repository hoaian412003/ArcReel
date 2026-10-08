"""编剧提交的编译：确定性渲染、按字数算时长，以及 error / warning 两档检查。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lib.script.screenplay_authoring import Beat, ScreenplaySubmission, compile_submission


def _submission(*scenes: list[dict[str, Any]], title: str = "标题", logline: str = "看点") -> ScreenplaySubmission:
    return ScreenplaySubmission.model_validate(
        {
            "title": title,
            "logline": logline,
            "scenes": [
                {"heading": f"内景 · 地点{i} · 日", "summary": "概要", "beats": beats}
                for i, beats in enumerate(scenes, 1)
            ],
        }
    )


def _action(text: str = "他推开门。", seconds: int = 3) -> dict[str, Any]:
    return {"kind": "action", "text": text, "seconds": seconds}


def _say(text: str, speaker: str = "阿离", tone: str | None = None) -> dict[str, Any]:
    return {"kind": "dialogue", "text": text, "speaker": speaker, **({"tone": tone} if tone else {})}


def _codes(compiled, severity: str) -> set[str]:
    return {gate.code for gate in compiled.gates if gate.severity == severity}


def test_beats_render_to_the_stored_markdown_format() -> None:
    submission = _submission(
        [_action("她攥紧信。"), _say("你来了。", tone="冷淡"), _say("嗯。"), {"kind": "voice_over", "text": "三年了。"}]
    )

    compiled = compile_submission(submission, language="zh")

    assert (
        compiled.draft.scenes[0].text
        == "她攥紧信。\n\n**阿离** (冷淡): 你来了。\n\n**阿离**: 嗯。\n\n（画外音）三年了。"
    )


def test_scene_seconds_come_from_spoken_units_at_the_speech_rate_plus_action_seconds() -> None:
    # zh 按字数 / 语速：5 个字 / 10 = 0.5 秒念白 + 0.5 秒停顿 + 3 秒动作
    submission = _submission([_action(seconds=3), _say("一二三四五")])

    compiled = compile_submission(submission, language="zh", speech_rate_override=10.0)

    assert compiled.draft.scenes[0].estimated_seconds == 4
    assert compiled.total_seconds == 4


def test_a_clean_submission_has_no_gates() -> None:
    submission = _submission([_say("你来了。"), _action(), _say("嗯。")], [_action("门开了。"), _say("谁？")])

    compiled = compile_submission(submission, language="zh", character_names=["阿离"], target_seconds=None)

    assert compiled.gates == []


def test_no_spoken_beats_is_an_error() -> None:
    compiled = compile_submission(_submission([_action()]), language="zh")

    assert _codes(compiled, "error") == {"no_spoken_beats"}


def test_cjk_text_in_a_non_chinese_project_is_an_error_but_not_in_a_chinese_one() -> None:
    submission = _submission([_action("Anh mở cửa."), _say("你好")])

    assert "wrong_language" in _codes(compile_submission(submission, language="vi"), "error")
    assert "wrong_language" not in _codes(compile_submission(submission, language="zh"), "error")


def test_a_line_over_the_hard_limit_is_an_error_and_over_the_soft_limit_a_warning() -> None:
    soft = compile_submission(_submission([_action(), _say("一" * 30)]), language="zh")
    hard = compile_submission(_submission([_action(), _say("一" * 70)]), language="zh")

    assert _codes(soft, "warning") == {"long_lines"}
    assert soft.errors == []
    assert "line_too_long" in _codes(hard, "error")


def test_vietnamese_lines_are_counted_in_words() -> None:
    long_line = " ".join(["từ"] * 20)

    compiled = compile_submission(
        _submission([_action("Anh đẩy cửa."), _say(long_line)], title="Tiêu đề", logline="Điểm nhấn"), language="vi"
    )

    assert _codes(compiled, "warning") == {"long_lines"}
    assert compiled.errors == []


@pytest.mark.parametrize(("target", "flagged"), [(10, True), (13, False), (100, True)])
def test_duration_far_from_the_target_is_a_warning(target: int, flagged: bool) -> None:
    compiled = compile_submission(
        _submission([_action(seconds=12)]) if False else _submission([_action(seconds=12), _say("好。")]),
        language="zh",
        target_seconds=target,
    )

    assert ("duration_off_target" in _codes(compiled, "warning")) is flagged


def test_a_long_cold_open_without_speech_in_the_first_beats_is_flagged() -> None:
    quiet = compile_submission(
        _submission([_action(seconds=40), _action(seconds=2), _action(seconds=2), _say("嗯")]), language="zh"
    )
    lively = compile_submission(_submission([_say("出事了！"), _action(seconds=40)]), language="zh")

    assert "cold_open" in _codes(quiet, "warning")
    assert "cold_open" not in _codes(lively, "warning")


def test_scene_level_shape_warnings() -> None:
    dialogue_only = [_say("甲") for _ in range(9)]
    compiled = compile_submission(_submission([_action(), _say("好")], dialogue_only), language="zh")

    assert {"no_action_scene", "dialogue_heavy"} <= _codes(compiled, "warning")


def test_unregistered_speakers_and_a_voice_over_ending_are_warnings() -> None:
    submission = _submission([_action(), _say("好", speaker="陌生人/少年"), {"kind": "voice_over", "text": "完。"}])

    compiled = compile_submission(submission, language="zh", character_names=["阿离"])

    assert {"unregistered_speakers", "weak_ending"} <= _codes(compiled, "warning")
    assert compile_submission(submission, language="zh", character_names=[]).warnings != []
    assert "unregistered_speakers" not in _codes(compile_submission(submission, language="zh"), "warning")


@pytest.mark.parametrize(
    "beat",
    [
        {"kind": "dialogue", "text": "嗯"},
        {"kind": "action", "text": "走"},
        {"kind": "voice_over", "text": "旁白", "speaker": "某人"},
        {"kind": "dialogue", "text": "嗯", "speaker": "阿离", "seconds": 2},
    ],
    ids=["dialogue-needs-speaker", "action-needs-seconds", "voice-over-has-no-speaker", "spoken-has-no-seconds"],
)
def test_ill_formed_beats_fail_validation(beat: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Beat.model_validate(beat)
