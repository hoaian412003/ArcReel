"""The H3 Ref2VA profile switches the final reference-video prompt to the resolved six-section payload."""

import pytest

from lib.script.reference_video.h3_prompt import H3PromptUnavailable, bind_h3_prompt
from lib.script.reference_video.prompt_render import WARN_H3_AUDIO_UNREFERENCED, render_video_unit_prompt
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import ReferenceResource

TEXT = "@[Lan]站在@[Tavern]门口。\n@[Lan]{你终于来了。}"
REFERENCES = [ReferenceResource(type="character", name="Lan"), ReferenceResource(type="scene", name="Tavern")]
SECTIONS = {
    "subject_definitions": (
        "@[Lan] is the young woman in <Picture @[Lan]>.\n@[Tavern] is the old tavern in <Picture @[Tavern]>."
    ),
    "summary": "[reference generation] @[Lan] waits at @[Tavern].",
    "retention_analysis": "@[Lan] (appears in [Shot 1]): fully_preserved - retained.",
    "detailed_description": (
        "Realistic cinematic style.\n[Shot 1] @[Lan] (S1) stands at @[Tavern]. She says: <d>[Chinese] 你终于来了。</d>"
    ),
    "overall_soundscape": "Soft wind.",
    "non_diegetic_music": "N/A",
}


def _project(**overrides) -> dict:
    return {
        "generation_mode": "reference_video",
        "content_mode": "drama",
        "style": "国漫",
        "characters": {"Lan": {"description": "young woman", "voice_style": "calm"}},
        "scenes": {"Tavern": {"description": "old tavern"}},
        "props": {},
        **overrides,
    }


def _unit(**overrides) -> dict:
    return {"unit_id": "E1U01", "text": TEXT, "duration_seconds": 6, **overrides}


def _render(project: dict, unit: dict, settings: VoiceRenderSettings | None = None):
    return render_video_unit_prompt(unit, project, settings or VoiceRenderSettings(), request_references=REFERENCES)


def test_h3_profile_sends_the_resolved_sections():
    payload = bind_h3_prompt(TEXT, SECTIONS).model_dump()
    rendered = _render(_project(video_prompt_profile="h3_ref2va"), _unit(h3_prompt=payload))

    assert rendered.prompt.startswith("subject_definitions:\n<Subject 1> is the young woman in <Picture 1>.")
    assert "<Subject 2> is the old tavern in <Picture 2>." in rendered.prompt
    assert "Avoid:" not in rendered.prompt
    assert "@[" not in rendered.prompt


def test_default_profile_is_unchanged_by_a_stored_payload():
    payload = bind_h3_prompt(TEXT, SECTIONS).model_dump()
    with_payload = _render(_project(), _unit(h3_prompt=payload))
    without_payload = _render(_project(), _unit())
    assert with_payload.prompt == without_payload.prompt
    assert "Avoid:" in with_payload.prompt


def test_h3_profile_without_a_payload_is_unavailable():
    with pytest.raises(H3PromptUnavailable) as raised:
        _render(_project(video_prompt_profile="h3_ref2va"), _unit())
    assert raised.value.code == "h3_prompt_missing"


def test_h3_profile_with_edited_text_is_stale():
    payload = bind_h3_prompt(TEXT, SECTIONS).model_dump()
    with pytest.raises(H3PromptUnavailable) as raised:
        _render(_project(video_prompt_profile="h3_ref2va"), _unit(text=TEXT + "\n镜头推近。", h3_prompt=payload))
    assert raised.value.code == "h3_prompt_stale"


def test_h3_profile_on_a_storyboard_project_falls_back_to_the_default_render():
    rendered = _render(_project(video_prompt_profile="h3_ref2va", generation_mode="storyboard"), _unit())
    assert "Avoid:" in rendered.prompt


def test_reference_audio_without_an_audio_label_warns():
    payload = bind_h3_prompt(TEXT, SECTIONS).model_dump()
    settings = VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, audio_ready={"Lan"})
    rendered = _render(_project(video_prompt_profile="h3_ref2va"), _unit(h3_prompt=payload), settings)
    assert rendered.audio_speakers == ["Lan"]
    assert WARN_H3_AUDIO_UNREFERENCED in [warning["key"] for warning in rendered.warnings]


def test_audio_label_resolves_against_the_bound_speaker():
    sections = {**SECTIONS, "retention_analysis": "<Audio @[Lan]>: reference - the timbre of @[Lan]."}
    payload = bind_h3_prompt(TEXT, sections).model_dump()
    settings = VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, audio_ready={"Lan"})
    rendered = _render(_project(video_prompt_profile="h3_ref2va"), _unit(h3_prompt=payload), settings)
    assert "<Audio 1>: reference - the timbre of <Subject 1>." in rendered.prompt
    assert WARN_H3_AUDIO_UNREFERENCED not in [warning["key"] for warning in rendered.warnings]
