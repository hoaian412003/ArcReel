import pytest

from lib.billing.cost_calculator import cost_calculator
from lib.billing.pricing.strategies import PricingParams

RUNNINGHUB_IMAGE_MODEL = "ai-app-2104532059371909121"


@pytest.mark.parametrize("resolution", ["1K", "2K", "4K"])
def test_runninghub_image_is_a_flat_price_per_call(resolution):
    amount, currency = cost_calculator.calculate_cost(
        "runninghub",
        PricingParams(call_type="image", model=RUNNINGHUB_IMAGE_MODEL, resolution=resolution),
    )
    assert (amount, currency) == (0.015, "USD")


def test_runninghub_image_price_scales_with_image_count():
    amount, _ = cost_calculator.calculate_cost(
        "runninghub",
        PricingParams(call_type="image", model=RUNNINGHUB_IMAGE_MODEL, n=4),
    )
    assert amount == pytest.approx(0.06)
