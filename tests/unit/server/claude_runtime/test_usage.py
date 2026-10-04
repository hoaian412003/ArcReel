from server.claude_runtime.usage import cost_delta, usage_delta


def test_usage_delta_subtracts_cumulative_sdk_counters() -> None:
    assert usage_delta(
        {
            "input_tokens": 140,
            "output_tokens": 35,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 50,
        },
        {
            "input_tokens": 100,
            "output_tokens": 25,
            "cache_creation_input_tokens": 12,
            "cache_read_input_tokens": 30,
        },
    ) == {
        "input_tokens": 40,
        "output_tokens": 10,
        "cache_creation_input_tokens": 8,
        "cache_read_input_tokens": 20,
    }


def test_usage_delta_treats_lower_counter_as_reset() -> None:
    delta = usage_delta({"input_tokens": 4, "output_tokens": 2}, {"input_tokens": 100, "output_tokens": 50})
    assert delta["input_tokens"] == 4
    assert delta["output_tokens"] == 2


def test_cost_delta_never_double_counts_cumulative_cost() -> None:
    assert cost_delta(0.32, 0.20) == 0.12
    assert cost_delta(0.05, 0.20) == 0.05
    assert cost_delta(None, 0.20) is None
