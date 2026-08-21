"""Deterministic scenario economics.

The language model may explain these results, but it never performs or silently
changes the arithmetic. Inputs retain their evidence/assumption references so a
finance owner can reproduce and challenge every number.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from porter_forces_ai.domain import ContractModel

ZERO = Decimal("0")
ONE = Decimal("1")
TWELVE = Decimal("12")


class RangeEstimate(ContractModel):
    """Ordered low/base/high values sharing one unit and provenance."""

    low: Decimal
    base: Decimal
    high: Decimal
    unit: str = Field(min_length=1, max_length=40)
    basis_ids: list[str] = Field(
        min_length=1,
        description="Evidence or assumption IDs supporting this estimate.",
    )
    owner: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def values_are_ordered(self) -> RangeEstimate:
        if not self.low <= self.base <= self.high:
            raise ValueError("range values must satisfy low <= base <= high")
        return self


class ScenarioEconomicsInputs(ContractModel):
    scenario_name: str = Field(min_length=2, max_length=300)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    horizon_years: int = Field(ge=1, le=10)
    discount_rate: Decimal = Field(ge=ZERO, le=Decimal("0.50"))
    benefit_start_month: int = Field(default=1, ge=1, le=120)
    upfront_cost: RangeEstimate
    annual_gross_benefit: RangeEstimate
    benefit_realization_rate: RangeEstimate
    annual_run_cost: RangeEstimate
    annual_control_cost: RangeEstimate
    annual_expected_loss: RangeEstimate

    @model_validator(mode="after")
    def units_are_compatible(self) -> ScenarioEconomicsInputs:
        money_inputs = (
            self.upfront_cost,
            self.annual_gross_benefit,
            self.annual_run_cost,
            self.annual_control_cost,
            self.annual_expected_loss,
        )
        if any(item.unit != self.currency for item in money_inputs):
            raise ValueError("all monetary estimate units must equal currency")
        if any(item.low < ZERO for item in money_inputs):
            raise ValueError("scenario benefits and costs cannot be negative")
        if self.benefit_realization_rate.unit != "ratio":
            raise ValueError("benefit_realization_rate must use unit='ratio'")
        rates = self.benefit_realization_rate
        if rates.low < ZERO or rates.high > ONE:
            raise ValueError("benefit realization rates must be between 0 and 1")
        if self.benefit_start_month > self.horizon_years * 12:
            raise ValueError("benefit_start_month must fall inside the scenario horizon")
        return self


class ScenarioEconomicsResult(ContractModel):
    scenario_name: str
    currency: str
    npv: RangeEstimate
    undiscounted_roi: RangeEstimate | None
    base_discounted_payback_month: int | None
    formulas: list[str]
    warnings: list[str] = Field(default_factory=list)


class CostOfDelayInputs(ContractModel):
    currency: str = Field(default="USD", min_length=3, max_length=3)
    period_months: int = Field(ge=1, le=120)
    foregone_benefit: RangeEstimate
    competitive_erosion: RangeEstimate
    accumulated_technical_and_control_debt: RangeEstimate
    lost_learning_advantage: RangeEstimate
    savings_from_waiting: RangeEstimate

    @model_validator(mode="after")
    def monetary_units_match(self) -> CostOfDelayInputs:
        estimates = (
            self.foregone_benefit,
            self.competitive_erosion,
            self.accumulated_technical_and_control_debt,
            self.lost_learning_advantage,
            self.savings_from_waiting,
        )
        if any(item.unit != self.currency for item in estimates):
            raise ValueError("all cost-of-delay estimate units must equal currency")
        if any(item.low < ZERO for item in estimates):
            raise ValueError("cost-of-delay components cannot be negative")
        return self


class CostOfDelayResult(ContractModel):
    currency: str
    period_months: int
    cost_of_delay: RangeEstimate
    formula: str


def _values(value: RangeEstimate) -> tuple[Decimal, Decimal, Decimal]:
    return value.low, value.base, value.high


def _active_fraction_for_year(start_month: int, year: int) -> Decimal:
    """Fraction of a year accruing benefits, using inclusive start month."""

    first_month = (year - 1) * 12 + 1
    last_month = year * 12
    if start_month > last_month:
        return ZERO
    active_start = max(first_month, start_month)
    return Decimal(last_month - active_start + 1) / TWELVE


def _net_annual_ranges(inputs: ScenarioEconomicsInputs) -> tuple[Decimal, Decimal, Decimal]:
    benefit = _values(inputs.annual_gross_benefit)
    realization = _values(inputs.benefit_realization_rate)
    run_cost = _values(inputs.annual_run_cost)
    control_cost = _values(inputs.annual_control_cost)
    expected_loss = _values(inputs.annual_expected_loss)

    low = benefit[0] * realization[0] - run_cost[2] - control_cost[2] - expected_loss[2]
    base = benefit[1] * realization[1] - run_cost[1] - control_cost[1] - expected_loss[1]
    high = benefit[2] * realization[2] - run_cost[0] - control_cost[0] - expected_loss[0]
    return low, base, high


def _discounted_payback_month(
    inputs: ScenarioEconomicsInputs,
    annual_base_net: Decimal,
) -> int | None:
    """Return base-case discounted payback using nominal monthly cash flows."""

    if annual_base_net <= ZERO:
        return None
    balance = -inputs.upfront_cost.base
    monthly_rate = inputs.discount_rate / TWELVE
    total_months = inputs.horizon_years * 12
    for month in range(1, total_months + 1):
        run_only_cost = (
            inputs.annual_run_cost.base
            + inputs.annual_control_cost.base
            + inputs.annual_expected_loss.base
        ) / TWELVE
        cash_flow = -run_only_cost
        if month >= inputs.benefit_start_month:
            realized_benefit = (
                inputs.annual_gross_benefit.base
                * inputs.benefit_realization_rate.base
                / TWELVE
            )
            cash_flow += realized_benefit
        balance += cash_flow / ((ONE + monthly_rate) ** month)
        if balance >= ZERO:
            return month
    return None


def calculate_scenario(inputs: ScenarioEconomicsInputs) -> ScenarioEconomicsResult:
    """Calculate conservative, base, and upside NPV/ROI scenarios."""

    annual_net = _net_annual_ranges(inputs)
    upfront = _values(inputs.upfront_cost)
    npv = [-upfront[2], -upfront[1], -upfront[0]]
    undiscounted = [-upfront[2], -upfront[1], -upfront[0]]

    for year in range(1, inputs.horizon_years + 1):
        active = _active_fraction_for_year(inputs.benefit_start_month, year)
        # Costs apply throughout the year; gross benefit only after the start month.
        benefit = _values(inputs.annual_gross_benefit)
        realization = _values(inputs.benefit_realization_rate)
        run_cost = _values(inputs.annual_run_cost)
        control_cost = _values(inputs.annual_control_cost)
        expected_loss = _values(inputs.annual_expected_loss)
        yearly = (
            benefit[0] * realization[0] * active
            - run_cost[2]
            - control_cost[2]
            - expected_loss[2],
            benefit[1] * realization[1] * active
            - run_cost[1]
            - control_cost[1]
            - expected_loss[1],
            benefit[2] * realization[2] * active
            - run_cost[0]
            - control_cost[0]
            - expected_loss[0],
        )
        discount_factor = (ONE + inputs.discount_rate) ** year
        for index in range(3):
            npv[index] += yearly[index] / discount_factor
            undiscounted[index] += yearly[index]

    run_cost = _values(inputs.annual_run_cost)
    control_cost = _values(inputs.annual_control_cost)
    expected_loss = _values(inputs.annual_expected_loss)
    total_cost = (
        upfront[2]
        + Decimal(inputs.horizon_years)
        * (run_cost[2] + control_cost[2] + expected_loss[2]),
        upfront[1]
        + Decimal(inputs.horizon_years)
        * (run_cost[1] + control_cost[1] + expected_loss[1]),
        upfront[0]
        + Decimal(inputs.horizon_years)
        * (run_cost[0] + control_cost[0] + expected_loss[0]),
    )
    warnings: list[str] = []
    roi: tuple[Decimal, Decimal, Decimal] | None = None
    if all(item > ZERO for item in total_cost):
        raw_roi = tuple(
            undiscounted[index] / total_cost[index] for index in range(3)
        )
        roi = (min(raw_roi), raw_roi[1], max(raw_roi))
    else:
        warnings.append(
            "ROI is undefined because at least one scenario has zero total modeled cost."
        )
    if inputs.benefit_realization_rate.base == ONE:
        warnings.append(
            "Base case assumes every modeled benefit is realized; finance-owner review is required."
        )

    basis_ids = sorted(
        {
            basis_id
            for estimate in (
                inputs.upfront_cost,
                inputs.annual_gross_benefit,
                inputs.benefit_realization_rate,
                inputs.annual_run_cost,
                inputs.annual_control_cost,
                inputs.annual_expected_loss,
            )
            for basis_id in estimate.basis_ids
        }
    )
    return ScenarioEconomicsResult(
        scenario_name=inputs.scenario_name,
        currency=inputs.currency,
        npv=RangeEstimate(
            low=npv[0],
            base=npv[1],
            high=npv[2],
            unit=inputs.currency,
            basis_ids=basis_ids,
        ),
        undiscounted_roi=(
            RangeEstimate(
                low=roi[0],
                base=roi[1],
                high=roi[2],
                unit="ratio",
                basis_ids=basis_ids,
            )
            if roi is not None
            else None
        ),
        base_discounted_payback_month=_discounted_payback_month(inputs, annual_net[1]),
        formulas=[
            "realized benefit = gross benefit x realization rate",
            "annual net = realized benefit - run cost - control cost - expected loss",
            "NPV = -upfront cost + sum(annual net / (1 + discount rate)^year)",
            "ROI = (total realized benefits - total costs) / total costs",
        ],
        warnings=warnings,
    )


def calculate_cost_of_delay(inputs: CostOfDelayInputs) -> CostOfDelayResult:
    """Calculate both the cost and possible benefit of waiting."""

    positive = (
        inputs.foregone_benefit,
        inputs.competitive_erosion,
        inputs.accumulated_technical_and_control_debt,
        inputs.lost_learning_advantage,
    )
    low = sum((item.low for item in positive), ZERO) - inputs.savings_from_waiting.high
    base = sum((item.base for item in positive), ZERO) - inputs.savings_from_waiting.base
    high = sum((item.high for item in positive), ZERO) - inputs.savings_from_waiting.low
    basis_ids = sorted(
        {
            basis_id
            for estimate in (*positive, inputs.savings_from_waiting)
            for basis_id in estimate.basis_ids
        }
    )
    return CostOfDelayResult(
        currency=inputs.currency,
        period_months=inputs.period_months,
        cost_of_delay=RangeEstimate(
            low=low,
            base=base,
            high=high,
            unit=inputs.currency,
            basis_ids=basis_ids,
        ),
        formula=(
            "foregone benefit + competitive erosion + accumulated technical/control debt "
            "+ lost learning advantage - savings from waiting"
        ),
    )
