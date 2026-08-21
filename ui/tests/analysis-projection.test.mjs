import assert from "node:assert/strict";
import test from "node:test";

import { projectCompletedAnalysis } from "../app/analysis-projection.ts";
import { canonicalScenario } from "../app/intake-contract.ts";

const forceNames = [
  "threat_of_new_entrants",
  "bargaining_power_of_suppliers",
  "bargaining_power_of_buyers",
  "threat_of_substitutes",
  "competitive_rivalry",
];

function completedPayload({ approvals = [] } = {}) {
  return {
    run_id: "RUN-real-001",
    application_status: "human_required",
    completed_at: "2026-08-21T12:00:00Z",
    model: "Qwen3.8-27B-4bit",
    details: {
      run_id: "RUN-real-001",
      status: "human_required",
      request: {
        question: "Should the bank fund a governed AI control plane?",
        archetype: "global_bank",
        industry_arena: "Global regulated banking",
        time_horizon_months: 36,
      },
      force_assessments: forceNames.map((force, index) => ({
        force,
        pressure_score: 3 + index * 0.1,
        trend: index % 2 ? "stable" : "increasing",
        confidence: 0.8,
        drivers: [{ mechanism: `Mechanism ${index + 1}` }],
        evidence_for_claim_ids: index === 0 ? ["C-1"] : [],
        evidence_against_claim_ids: [],
        organization_exposures: [`Exposure ${index + 1}`],
        strategic_implications: [`Implication ${index + 1}`],
        leading_indicators: [`Indicator ${index + 1}`],
        conditions_that_change_conclusion: [`Condition ${index + 1}`],
      })),
      evidence_ledger: {
        evidence: [{
          evidence_id: "E-1",
          origin: "public_web",
          source_class: "regulator",
          title: "Supervisory report",
          publisher: "Regulator",
          source_url: "https://example.org/report",
          published_at: "2026-06-01",
          retrieved_at: "2026-08-20T10:00:00Z",
          content_sha256: "a".repeat(64),
          quality_score: 0.9,
          freshness_score: 0.8,
          applicability_score: 0.75,
        }],
        claims: [{ claim_id: "C-1", statement: "Captured evidence supports the entry thesis." }],
        links: [{ claim_id: "C-1", evidence_id: "E-1", stance: "supports" }],
      },
      board_brief: {
        as_of: "2026-08-21T12:00:00Z",
        decision_requested: "Approve or reject the bounded first tranche.",
        recommendation: { text: "Approve a bounded tranche." },
        why_now: [{ text: "The learning decision is time-sensitive." }],
        no_action_case: [{ text: "Waiting defers institution-specific evidence." }],
        largest_uncertainties: [{ text: "Realized adoption remains uncertain." }],
        smallest_sensible_commitment: { text: "Fund one controlled workflow." },
        dissenting_view: { text: "Model costs may fall quickly." },
        board_questions: ["What earns the next tranche?"],
        analogies: [],
      },
      quality_report: { draft_valid: true, publishable: false, findings: [] },
      ralph_state: { attempts: [] },
      scenario_economics: [{
        scenario_name: "Controlled tranche",
        currency: "USD",
        npv: { low: "10", base: "20", high: "30", unit: "USD", basis_ids: ["A-FIN-1"] },
        base_discounted_payback_month: 18,
        warnings: [],
      }],
      cost_of_delay: null,
      artifact_paths: {
        board_memo: "/api/runs/RUN-real-001/artifacts/board_memo",
        secret_sidecar: "/tmp/private.json",
      },
      approvals,
    },
  };
}

test("projects a completed canonical response without fixture substitution", () => {
  const result = projectCompletedAnalysis(completedPayload());
  assert.ok(result);
  assert.equal(result.origin, "backend");
  assert.equal(result.runId, "RUN-real-001");
  assert.equal(result.forces.length, 5);
  assert.equal(result.forces[0].evidenceFor, "Captured evidence supports the entry thesis.");
  assert.equal(result.evidence[0].authority, 54);
  assert.equal(result.evidence[0].status, "Captured");
  assert.equal(result.scenarios[0].npvBase, 20);
  assert.equal(result.scenarios[0].unitLabel, "USD");
  assert.deepEqual(result.artifactUrls, {
    board_memo: "/api/runs/RUN-real-001/artifacts/board_memo",
  });
  assert.deepEqual(result.approvals.map((approval) => approval.status), [
    "Pending",
    "Pending",
    "Pending",
    "Pending",
  ]);
});

test("projects Ralph's evaluator explanation rather than inventing an event message", () => {
  const payload = completedPayload();
  payload.details.ralph_state.attempts = [{
    attempt_number: 1,
    completed_at: "2026-08-21T12:00:00Z",
    report: {
      evaluations: [{
        criterion_id: "G-evidence-integrity",
        outcome: "pass",
        explanation: "The frozen evidence snapshot passed deterministic gates.",
      }],
    },
  }];

  const result = projectCompletedAnalysis(payload);
  assert.ok(result);
  assert.match(result.logs[0].text, /frozen evidence snapshot passed deterministic gates/i);
});

test("hydrates the latest backend approval record and exact fingerprint", () => {
  const result = projectCompletedAnalysis(completedPayload({
    approvals: [{
      role: "strategy",
      reviewer: "Strategy reviewer",
      reviewed_at: "2026-08-21T13:00:00Z",
      decision: "approve",
      brief_sha256: "b".repeat(64),
    }],
  }));
  assert.ok(result);
  assert.equal(result.approvals[0].status, "Approved");
  assert.equal(result.approvals[0].briefSha256, "b".repeat(64));
  assert.deepEqual(result.approvals.slice(1).map((approval) => approval.status), [
    "Pending",
    "Pending",
    "Pending",
  ]);
});

test("rejects partial and nonterminal responses instead of inventing analysis", () => {
  assert.equal(projectCompletedAnalysis({ status: "running", progress: 32 }), null);
  const partial = completedPayload();
  partial.details.force_assessments = partial.details.force_assessments.slice(0, 4);
  assert.equal(projectCompletedAnalysis(partial), null);
});

test("projects complete blocked results but leaves failed runs to the raw status view", () => {
  const blocked = completedPayload();
  blocked.application_status = "blocked";
  blocked.details.status = "blocked";
  assert.equal(projectCompletedAnalysis(blocked)?.applicationStatus, "blocked");

  const failed = completedPayload();
  failed.application_status = "failed";
  failed.details.status = "failed";
  assert.equal(projectCompletedAnalysis(failed), null);
});

test("serializes only explicit Finance-owned ranges into the canonical calculator contract", () => {
  const range = (low, base, high) => ({ low, base, high });
  const result = canonicalScenario({
    enabled: true,
    scenarioName: "Bounded workflow",
    currency: "USD",
    horizonYears: "3",
    discountRatePercent: "12.5",
    benefitStartMonth: "7",
    basisId: " A-FIN-001 ",
    upfrontCost: range("10", "12", "15"),
    annualGrossBenefit: range("20", "25", "30"),
    benefitRealizationRate: range("40", "50", "60"),
    annualRunCost: range("2", "3", "4"),
    annualControlCost: range("1", "2", "3"),
    annualExpectedLoss: range("1", "1.5", "2"),
  });

  assert.equal(result.discount_rate, 0.125);
  assert.equal(result.benefit_start_month, 7);
  assert.deepEqual(result.upfront_cost, {
    low: 10,
    base: 12,
    high: 15,
    unit: "USD",
    basis_ids: ["A-FIN-001"],
    owner: "Finance",
  });
  assert.deepEqual(result.benefit_realization_rate, {
    low: 0.4,
    base: 0.5,
    high: 0.6,
    unit: "ratio",
    basis_ids: ["A-FIN-001"],
    owner: "Finance",
  });
});
