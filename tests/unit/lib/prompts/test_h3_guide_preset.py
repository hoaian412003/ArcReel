import pytest

from lib.prompts.h3_guide_preset import (
    DEFAULT_H3_GUIDE_PRESET,
    MAX_CUSTOM_GUIDE_CHARS,
    VALID_H3_GUIDE_PRESETS,
    parse_h3_guide_custom,
    parse_h3_guide_preset,
    resolve_h3_guide_custom,
    resolve_h3_guide_preset,
)
from lib.prompts.prompt_builders_reference import build_reference_video_h3_ref2va_prompt


def test_resolution_order_is_project_then_global_then_default():
    assert resolve_h3_guide_preset({"h3_guide_preset": "action"}, "dialogue") == "action"
    assert resolve_h3_guide_preset({}, "dialogue") == "dialogue"
    assert resolve_h3_guide_preset(None, None) == DEFAULT_H3_GUIDE_PRESET


@pytest.mark.parametrize("raw", [None, "", "  ", "unknown", 3, ["action"]])
def test_unreadable_values_fall_through_to_the_next_layer(raw):
    assert parse_h3_guide_preset(raw) is None
    assert resolve_h3_guide_preset({"h3_guide_preset": raw}, "dialogue") == "dialogue"
    assert resolve_h3_guide_preset({"h3_guide_preset": raw}, raw) == DEFAULT_H3_GUIDE_PRESET


def _prompt(preset: str, custom: str | None = None) -> str:
    return build_reference_video_h3_ref2va_prompt(
        project_overview={},
        style="s",
        style_description="d",
        characters={},
        scenes={},
        props={},
        script_plan_units=[],
        max_refs=None,
        episode=1,
        audio_reference_names=[],
        h3_guide_preset=preset,
        h3_guide_custom=custom,
    )


def test_each_preset_renders_its_own_detail_guidance_and_keeps_structure_rules():
    rendered = {preset: _prompt(preset) for preset in sorted(VALID_H3_GUIDE_PRESETS)}
    assert len(set(rendered.values())) == len(rendered)
    assert "350–500" in rendered["standard"]
    assert "台词优先" in rendered["dialogue"]
    assert "动作优先" in rendered["action"]
    for text in rendered.values():
        assert "`<d>[语言] 台词</d>`" in text
        assert "[Shot N] At MM:SS.mmm" in text


def test_custom_guide_resolves_project_then_global_then_none():
    assert resolve_h3_guide_custom({"h3_guide_custom": " mine "}, "global") == "mine"
    assert resolve_h3_guide_custom({}, " global ") == "global"
    assert resolve_h3_guide_custom(None, None) is None


@pytest.mark.parametrize("raw", [None, "", "   ", 3, "x" * (MAX_CUSTOM_GUIDE_CHARS + 1)])
def test_unreadable_custom_guide_falls_through(raw):
    assert parse_h3_guide_custom(raw) is None
    assert resolve_h3_guide_custom({"h3_guide_custom": raw}, "global") == "global"


def test_custom_guide_replaces_the_builtin_guide_but_keeps_structure_rules():
    standard = _prompt("standard")
    custom = _prompt("standard", "  - MY CUSTOM RULE")
    assert "350–500" in standard
    assert "MY CUSTOM RULE" in custom
    assert "350–500" not in custom
    assert "细节要具体" not in custom
    assert "[Shot N] At MM:SS.mmm" in custom
    assert "`<d>[语言] 台词</d>`" in custom
