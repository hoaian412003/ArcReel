"""Tests for the MiniMax H3 Ref2VA prompt payload: fingerprint, validation and label resolution."""

import pytest

from lib.script.draft_violation import DraftViolation
from lib.script.reference_video.h3_prompt import (
    CODE_DIALOGUE_MISMATCH,
    CODE_NOT_ENGLISH,
    CODE_RAW_LABEL,
    CODE_SHOT_STRUCTURE,
    CODE_SUBJECT_UNDEFINED,
    CODE_UNKNOWN_REFERENCE,
    H3PromptUnavailable,
    bind_h3_prompt,
    load_h3_prompt,
    render_h3_prompt,
    source_text_hash,
    validate_h3_prompt,
)
from lib.script.script_models import ReferenceResource

PROJECT = {
    "characters": {"Lan": {"description": "young woman"}, "Mo": {"description": "young man"}},
    "scenes": {"Tavern": {"description": "old tavern"}},
    "props": {},
}
TEXT = "@[Lan]站在@[Tavern]门口，回头看向@[Mo]。\n@[Lan]{你终于来了。}\n{夜风灌进来}"


def _sections(**overrides: str) -> dict[str, str]:
    sections = {
        "subject_definitions": (
            "@[Lan] is the young woman in <Picture @[Lan]>, with long dark hair.\n"
            "@[Mo] is the young man in <Picture @[Mo]>.\n"
            "@[Tavern] is the old tavern in <Picture @[Tavern]>."
        ),
        "summary": "[reference generation] @[Lan] turns to @[Mo] at the door of @[Tavern].",
        "retention_analysis": "@[Lan] (appears in [Shot 1]): fully_preserved - identity retained.",
        "detailed_description": (
            "The target video uses a realistic cinematic style.\n"
            "[Shot 1] A medium shot of @[Lan] (S1) at the door of @[Tavern]. She says: <d>[Chinese] 你终于来了。</d>\n"
            "[Shot 2] At 00:03.000, the camera cuts to a close-up of @[Mo]."
        ),
        "overall_soundscape": "Wind moves through the tavern doorway.",
        "non_diegetic_music": "N/A",
    }
    sections.update(overrides)
    return sections


def _validate(sections: dict[str, str], *, text: str = TEXT, duration: int = 8) -> None:
    validate_h3_prompt("unit E1U01", text, sections, duration_seconds=duration, project=PROJECT)


def _violation_code(sections: dict[str, str], **kwargs) -> str:
    with pytest.raises(DraftViolation) as raised:
        _validate(sections, **kwargs)
    return raised.value.code


class TestFingerprint:
    def test_hash_ignores_outer_whitespace_and_unicode_form(self):
        assert source_text_hash("  café\n") == source_text_hash("café")

    def test_hash_changes_with_text(self):
        assert source_text_hash("a") != source_text_hash("b")

    def test_current_payload_loads(self):
        payload = bind_h3_prompt(TEXT, _sections())
        assert load_h3_prompt({"text": TEXT, "h3_prompt": payload.model_dump()}).summary == payload.summary

    def test_missing_payload_is_unavailable(self):
        with pytest.raises(H3PromptUnavailable) as raised:
            load_h3_prompt({"text": TEXT})
        assert raised.value.code == "h3_prompt_missing"

    def test_edited_text_makes_payload_stale(self):
        payload = bind_h3_prompt(TEXT, _sections())
        with pytest.raises(H3PromptUnavailable) as raised:
            load_h3_prompt({"text": TEXT + "\n镜头推近。", "h3_prompt": payload.model_dump()})
        assert raised.value.code == "h3_prompt_stale"


class TestValidation:
    def test_well_formed_payload_passes(self):
        assert _validate(_sections()) is None

    def test_numeric_labels_are_rejected(self):
        sections = _sections(summary="[reference generation] <Subject 1> turns to <Subject 2>.")
        assert _violation_code(sections) == CODE_RAW_LABEL

    def test_reference_outside_the_unit_text_is_rejected(self):
        sections = _sections(summary="[reference generation] @[Stranger] appears.")
        assert _violation_code(sections) == CODE_UNKNOWN_REFERENCE

    def test_every_referenced_asset_needs_a_definition(self):
        sections = _sections(subject_definitions="@[Lan] is the young woman in <Picture @[Lan]>.")
        assert _violation_code(sections) == CODE_SUBJECT_UNDEFINED

    def test_shot_one_must_not_carry_a_timestamp(self):
        sections = _sections(detailed_description="[Shot 1] At 00:00.000, a shot. <d>[Chinese] 你终于来了。</d>")
        assert _violation_code(sections) == CODE_SHOT_STRUCTURE

    def test_later_shot_must_carry_a_timestamp(self):
        sections = _sections(
            detailed_description="[Shot 1] A shot. <d>[Chinese] 你终于来了。</d>\n[Shot 2] The camera cuts to Mo."
        )
        assert _violation_code(sections) == CODE_SHOT_STRUCTURE

    def test_cut_times_must_increase_and_stay_inside_the_duration(self):
        base = "[Shot 1] A shot. <d>[Chinese] 你终于来了。</d>\n"
        late = _sections(detailed_description=base + "[Shot 2] At 00:09.000, cut.")
        assert _violation_code(late, duration=8) == CODE_SHOT_STRUCTURE
        backwards = _sections(detailed_description=base + "[Shot 2] At 00:04.000, cut.\n[Shot 3] At 00:02.000, cut.")
        assert _violation_code(backwards) == CODE_SHOT_STRUCTURE

    def test_dialogue_must_appear_word_for_word(self):
        sections = _sections(
            detailed_description="[Shot 1] A shot. <d>[Chinese] 你来了。</d>\n[Shot 2] At 00:03.000, cut."
        )
        assert _violation_code(sections) == CODE_DIALOGUE_MISMATCH

    def test_voiceover_must_not_be_spoken(self):
        text = "@[Lan]站在@[Tavern]门口，看向@[Mo]。\n{夜风灌进来}"
        sections = _sections(
            detailed_description="[Shot 1] A shot. <d>[Chinese] 夜风灌进来</d>\n[Shot 2] At 00:03.000, cut."
        )
        assert _violation_code(sections, text=text) == CODE_DIALOGUE_MISMATCH

    def test_non_english_body_is_rejected_but_dialogue_and_quoted_text_are_allowed(self):
        assert _violation_code(_sections(summary="[reference generation] 她转身看向他。")) == CODE_NOT_ENGLISH
        _validate(_sections(overall_soundscape='A neon sign reading "营业中" hums over the door.'))


class TestRendering:
    def _render(self, *, references, audio_speakers=()):
        payload = bind_h3_prompt(TEXT, _sections())
        return render_h3_prompt(payload, TEXT, PROJECT, references, audio_speakers)

    def test_labels_follow_the_request_order(self):
        references = [
            ReferenceResource(type="scene", name="Tavern"),
            ReferenceResource(type="character", name="Lan"),
            ReferenceResource(type="character", name="Mo"),
        ]
        prompt = self._render(references=references)
        assert prompt.startswith("subject_definitions:\n<Subject 1> is the young woman in <Picture 2>")
        assert "<Subject 2> is the old tavern in <Picture 1>." in prompt
        assert "<Subject 3> is the young man in <Picture 3>." in prompt
        assert "\n\nnon_diegetic_music:\nN/A" in prompt
        assert "@[" not in prompt

    def test_reference_trimmed_from_the_request_is_unavailable(self):
        with pytest.raises(H3PromptUnavailable) as raised:
            self._render(references=[ReferenceResource(type="character", name="Lan")])
        assert raised.value.code == "h3_prompt_unresolved_reference"

    def test_audio_label_resolves_from_the_bound_speakers(self):
        sections = _sections(retention_analysis="<Audio @[Lan]>: reference - the voice timbre of @[Lan].")
        payload = bind_h3_prompt(TEXT, sections)
        references = [
            ReferenceResource(type="character", name="Lan"),
            ReferenceResource(type="character", name="Mo"),
            ReferenceResource(type="scene", name="Tavern"),
        ]
        prompt = render_h3_prompt(payload, TEXT, PROJECT, references, ["Lan"])
        assert "<Audio 1>: reference - the voice timbre of <Subject 1>." in prompt
        with pytest.raises(H3PromptUnavailable):
            render_h3_prompt(payload, TEXT, PROJECT, references, [])
