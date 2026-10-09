import pytest

from lib.backends.runninghub_usage import RunningHubUsage, parse_runninghub_usage


def test_reads_coins_and_third_party_usd():
    payload = {"usage": {"consumeMoney": None, "consumeCoins": "8", "thirdPartyConsumeMoney": "0.015"}}
    assert parse_runninghub_usage(payload) == RunningHubUsage(coins=8.0, third_party_usd=0.015)


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"usage": None}, {"usage": {"consumeCoins": "abc", "thirdPartyConsumeMoney": None}}],
)
def test_missing_or_malformed_usage_yields_none(payload):
    assert parse_runninghub_usage(payload) == RunningHubUsage()


def test_poll_delay_is_fast_at_first_then_slows_to_the_ceiling():
    from lib.backends.runninghub_usage import poll_delay

    assert poll_delay(0, 5) == 2
    assert poll_delay(29, 5) == 2
    assert poll_delay(31, 5) == 5
    assert poll_delay(0, 0) == 0
