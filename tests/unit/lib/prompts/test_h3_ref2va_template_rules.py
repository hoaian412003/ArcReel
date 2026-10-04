"""H3 Ref2VA 提示词编写模版：参考图定义外观、身份用名词、运镜词表、说话人声音与切点规则。"""

from __future__ import annotations

from lib.prompts.prompt_builders_reference import build_reference_video_h3_ref2va_prompt


def _prompt(*, audio_reference_names: list[str] | None = None) -> str:
    return build_reference_video_h3_ref2va_prompt(
        project_overview={},
        style="s",
        style_description="d",
        characters={"阿岚": {"description": "（外观描述）"}},
        scenes={},
        props={},
        script_plan_units=[{"unit_id": "E1U01", "text": "@[阿岚]{走吧。}", "duration_seconds": 8}],
        max_refs=None,
        episode=1,
        audio_reference_names=audio_reference_names or [],
    )


def test_appearance_is_left_to_the_reference_picture() -> None:
    prompt = _prompt()

    assert "不要写任何外貌描述" in prompt
    assert "the reference picture defines his appearance." in prompt


def test_subjects_are_named_by_their_role_not_their_temperament() -> None:
    prompt = _prompt()

    assert "the host at the head seat" in prompt
    assert "不写性格、气质、神态" in prompt


def test_camera_movement_uses_the_official_vocabulary() -> None:
    prompt = _prompt()

    for term in (
        "push in / pull out",
        "arc shot",
        "tracking shot",
        "static shot",
        "with small amplitude",
        "at slow speed",
    ):
        assert term in prompt
    assert "同一镜只用一种主要运镜" in prompt


def test_speakers_get_a_voice_description_on_their_first_line() -> None:
    without_audio = _prompt()
    with_audio = _prompt(audio_reference_names=["阿岚"])

    assert "第一次发声" in without_audio
    assert "(S1,S2)" in without_audio
    assert "沿用 `<Audio @[名称]>` 的音色" not in without_audio
    assert "沿用 `<Audio @[名称]>` 的音色" in with_audio


def test_a_line_never_straddles_a_cut() -> None:
    prompt = _prompt()

    assert "一句台词不要跨过切点" in prompt
    assert "标点也照抄" in prompt
