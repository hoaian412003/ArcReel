"""编剧步骤 prompt：槽位渲染、可选分节与动态文本。"""

from __future__ import annotations

from lib.prompts.prompt_builders_script import build_screenplay_prompt


def _prompt(**overrides) -> str:
    params = {
        "novel_text": "裴与出征后的第二年，千里加急给我送回一个襁褓中的婴儿。",
        "project_overview": {"synopsis": "边关故事", "genre": "古风", "theme": "信任", "world_setting": "架空"},
        "style": "水彩",
        "characters": {"裴与": {}, "老管家": {}},
        "scenes": {"府门": {}},
        "props": {"书信": {}},
        "episode": 3,
        "source_language": "中文",
    }
    params.update(overrides)
    return build_screenplay_prompt(**params)


def test_prompt_carries_the_source_the_assets_and_the_episode_number() -> None:
    prompt = _prompt()

    assert "第 3 集" in prompt
    assert "裴与出征后的第二年" in prompt
    for name in ("裴与", "老管家", "府门", "书信"):
        assert name in prompt
    assert "estimated_seconds" in prompt


def test_prompt_leaves_out_technical_unit_work() -> None:
    prompt = _prompt()

    assert "不要输出它们" in prompt
    assert "supported_durations" not in prompt


def test_episode_target_duration_is_a_soft_target_only_when_set() -> None:
    assert "本集成片目标约 90 秒" in _prompt(episode_target_duration=90)
    assert "本集成片目标" not in _prompt()


def test_outline_hook_is_demanded_only_when_the_outline_has_one() -> None:
    with_hook = _prompt(episode_outline={"title": "t", "hook": "悬念收尾", "story_beats": ["a", "b"]})
    without = _prompt(episode_outline={"title": "t", "story_beats": ["a"]})

    assert "集尾钩子" in with_hook
    assert "悬念收尾" in with_hook
    assert "集尾钩子" not in without


def test_screenplay_source_kind_keeps_authored_lines_verbatim() -> None:
    assert "<screenplay>" in _prompt(source_kind="screenplay")
    assert "<novel>" in _prompt(source_kind="anything-else")


def test_additional_instructions_are_appended() -> None:
    assert "多写悬念" in _prompt(instructions="多写悬念")
    assert "多写悬念" not in _prompt()
