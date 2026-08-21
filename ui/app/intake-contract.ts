export type FinanceRangeKey =
  | "upfrontCost"
  | "annualGrossBenefit"
  | "benefitRealizationRate"
  | "annualRunCost"
  | "annualControlCost"
  | "annualExpectedLoss";

export interface FinanceRangeForm {
  low: string;
  base: string;
  high: string;
}

export interface FinanceScenarioForm {
  enabled: boolean;
  scenarioName: string;
  currency: string;
  horizonYears: string;
  discountRatePercent: string;
  benefitStartMonth: string;
  basisId: string;
  upfrontCost: FinanceRangeForm;
  annualGrossBenefit: FinanceRangeForm;
  benefitRealizationRate: FinanceRangeForm;
  annualRunCost: FinanceRangeForm;
  annualControlCost: FinanceRangeForm;
  annualExpectedLoss: FinanceRangeForm;
}

export function canonicalScenario(finance: FinanceScenarioForm): Record<string, unknown> {
  const basisIds = [finance.basisId.trim()];
  const moneyRange = (range: FinanceRangeForm) => ({
    low: Number(range.low),
    base: Number(range.base),
    high: Number(range.high),
    unit: finance.currency,
    basis_ids: basisIds,
    owner: "Finance",
  });
  const ratioRange = (range: FinanceRangeForm) => ({
    low: Number(range.low) / 100,
    base: Number(range.base) / 100,
    high: Number(range.high) / 100,
    unit: "ratio",
    basis_ids: basisIds,
    owner: "Finance",
  });
  return {
    scenario_name: finance.scenarioName,
    currency: finance.currency,
    horizon_years: Number(finance.horizonYears),
    discount_rate: Number(finance.discountRatePercent) / 100,
    benefit_start_month: Number(finance.benefitStartMonth),
    upfront_cost: moneyRange(finance.upfrontCost),
    annual_gross_benefit: moneyRange(finance.annualGrossBenefit),
    benefit_realization_rate: ratioRange(finance.benefitRealizationRate),
    annual_run_cost: moneyRange(finance.annualRunCost),
    annual_control_cost: moneyRange(finance.annualControlCost),
    annual_expected_loss: moneyRange(finance.annualExpectedLoss),
  };
}
