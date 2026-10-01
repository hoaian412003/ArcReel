from lib.billing.text_step_estimate import estimate_text_steps, estimate_tokens


def test_estimate_tokens_weights_cjk_higher_than_latin():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 5
    assert estimate_tokens("abcdefgh") == 2


def test_no_source_means_no_steps():
    assert estimate_text_steps({}, source_text="", scripts_by_episode={}, episode_numbers=[1]) == []


def test_only_episode_plan_before_episodes_exist():
    steps = estimate_text_steps({}, source_text="a" * 4000, scripts_by_episode={}, episode_numbers=[])
    assert [s.id for s in steps] == ["episode_plan"]
    assert steps[0].input_tokens == 2_000 + 1_000


def test_per_episode_steps_scale_with_episode_count_and_prefer_existing_script_size():
    source = "a" * 4000
    without = {
        s.id: s for s in estimate_text_steps({}, source_text=source, scripts_by_episode={}, episode_numbers=[1, 2])
    }
    assert without["final_script"].calls == 2
    assert without["final_script"].output_tokens == 2 * round(500 * 1.2)

    scripts = {1: {"segments": ["x" * 400]}, 2: {"segments": ["y" * 400]}}
    with_scripts = {
        s.id: s for s in estimate_text_steps({}, source_text=source, scripts_by_episode=scripts, episode_numbers=[1, 2])
    }
    assert with_scripts["final_script"].output_tokens != without["final_script"].output_tokens
    assert with_scripts["script_plan_content"].output_tokens == without["script_plan_content"].output_tokens
