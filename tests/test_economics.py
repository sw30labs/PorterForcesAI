from decimal import Decimal

import pytest
from pydantic import ValidationError

from porter_forces_ai.economics import (
    CostOfDelayInputs,
    RangeEstimate,
    ScenarioEconomicsInputs,
    calculate_cost_of_delay,
    calculate_scenario,
)


def estimate(low: str, base: str, high: str, unit: str = "USD") -> RangeEstimate:
    return RangeEstimate(
        low=Decimal(low),
        base=Decimal(base),
        high=Decimal(high),
        unit=unit,
        basis_ids=["A-finance"],
    )


def test_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="low <= base <= high"):
        estimate("10", "5", "20")


def test_scenario_math_is_reproducible() -> None:
    result = calculate_scenario(
        ScenarioEconomicsInputs(
            scenario_name="Controlled adoption",
            horizon_years=3,
            discount_rate=Decimal("0"),
            benefit_start_month=1,
            upfront_cost=estimate("90", "100", "110"),
            annual_gross_benefit=estimate("100", "120", "140"),
            benefit_realization_rate=estimate("0.5", "0.6", "0.7", "ratio"),
            annual_run_cost=estimate("20", "20", "20"),
            annual_control_cost=estimate("10", "10", "10"),
            annual_expected_loss=estimate("5", "5", "5"),
        )
    )
    assert result.npv.low == Decimal("-65")
    assert result.npv.base == Decimal("11")
    assert result.npv.high == Decimal("99")
    assert result.undiscounted_roi is not None
    assert result.undiscounted_roi.base == Decimal("11") / Decimal("205")
    assert result.base_discounted_payback_month == 33


def test_zero_cost_no_action_baseline_has_npv_but_no_roi_or_payback() -> None:
    result = calculate_scenario(
        ScenarioEconomicsInputs(
            scenario_name="No incremental action",
            horizon_years=3,
            discount_rate=Decimal("0.08"),
            benefit_start_month=1,
            upfront_cost=estimate("0", "0", "0"),
            annual_gross_benefit=estimate("0", "0", "0"),
            benefit_realization_rate=estimate("0", "0", "0", "ratio"),
            annual_run_cost=estimate("0", "0", "0"),
            annual_control_cost=estimate("0", "0", "0"),
            annual_expected_loss=estimate("0", "0", "0"),
        )
    )

    assert result.npv.low == result.npv.base == result.npv.high == Decimal("0")
    assert result.undiscounted_roi is None
    assert result.base_discounted_payback_month is None
    assert "zero total modeled cost" in result.warnings[0]


def test_cost_of_delay_credits_the_benefit_of_waiting() -> None:
    result = calculate_cost_of_delay(
        CostOfDelayInputs(
            period_months=18,
            foregone_benefit=estimate("10", "20", "30"),
            competitive_erosion=estimate("5", "10", "20"),
            accumulated_technical_and_control_debt=estimate("2", "4", "8"),
            lost_learning_advantage=estimate("3", "6", "12"),
            savings_from_waiting=estimate("4", "8", "15"),
        )
    )
    assert result.cost_of_delay.low == Decimal("5")
    assert result.cost_of_delay.base == Decimal("32")
    assert result.cost_of_delay.high == Decimal("66")
