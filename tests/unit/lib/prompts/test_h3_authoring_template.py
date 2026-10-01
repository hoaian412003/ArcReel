import pytest

from lib.prompts.h3_authoring_template import (
    MAX_TEMPLATE_CHARS,
    TEMPLATE_ID,
    parse_h3_authoring_template,
    resolve_h3_authoring_template,
)
from lib.prompts.prompt_builders_reference import preview_h3_ref2va_template, validate_h3_authoring_template
from lib.prompts.prompt_templates import TemplateError
from lib.prompts.prompt_templates.builtin import builtin_templates

BUILTIN, _ = builtin_templates.read_source(TEMPLATE_ID)


def test_resolution_order_is_project_then_global_then_builtin():
    assert resolve_h3_authoring_template({"h3_authoring_template": "p"}, "g") == "p"
    assert resolve_h3_authoring_template({}, "g") == "g"
    assert resolve_h3_authoring_template(None, None) is None


@pytest.mark.parametrize("raw", [None, "", "  \n", 3, "x" * (MAX_TEMPLATE_CHARS + 1)])
def test_unreadable_values_are_treated_as_unset(raw):
    assert parse_h3_authoring_template(raw) is None
    assert resolve_h3_authoring_template({"h3_authoring_template": raw}, "g") == "g"


def test_builtin_body_as_override_renders_identically():
    assert preview_h3_ref2va_template(BUILTIN) == preview_h3_ref2va_template()


def test_override_replaces_the_prompt_text_and_keeps_slots_working():
    body = (
        "只写六段。\n风格：{{ style }}\n"
        '{{ partial("shared/writing_syntax") }}\n{{ partial("shared/overview_block") }}\n'
        '{{ partial("shared/lists/asset_appearance_blocks") }}\n'
        '{% if instructions %}\n{{ partial("shared/additional_instructions") }}\n{% endif %}'
    )
    rendered = preview_h3_ref2va_template(body)
    assert "只写六段。" in rendered
    assert "风格：(项目画风)" in rendered
    assert "资深的短视频分镜编剧" not in rendered


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace('{{ partial("shared/writing_syntax") }}', ""),
        lambda s: s + "{{ unknown_slot }}",
        lambda s: s + "{% set x = 1 %}",
        lambda s: s + '{{ partial("shared/" ~ style) }}',
        lambda s: s + "{{ ",
    ],
)
def test_invalid_overrides_are_rejected(mutate):
    with pytest.raises(TemplateError):
        validate_h3_authoring_template(mutate(BUILTIN))
