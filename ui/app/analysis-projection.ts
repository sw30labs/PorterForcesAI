import {
  evidence as illustrativeEvidence,
  forces as illustrativeForces,
  initialApprovals,
  initialLogs,
  scenarios as illustrativeScenarios,
  type Approval,
  type EvidenceItem,
  type ForceAssessment,
  type Tone,
} from "./demo-data.ts";

export type AnalysisOrigin = "backend" | "illustrative";

export interface ScenarioProjection {
  id: string;
  name: string;
  currency: string;
  unitLabel: string;
  npvLow: number;
  npvBase: number;
  npvHigh: number;
  payback: string;
  basisIds: string[];
  warnings: string[];
  tone: Tone;
}

export interface BoardBriefProjection {
  asOf: string;
  decisionRequested: string;
  recommendation: string;
  whyNow: string[];
  noActionCase: string[];
  uncertainties: string[];
  smallestCommitment: string;
  dissentingView: string;
  boardQuestions: string[];
  analogy: {
    unfamiliarConcept: string;
    familiarMechanism: string;
    correspondences: string[];
    decisionImplication: string;
    whereItBreaks: string;
  } | null;
}

export interface QualityProjection {
  draftValid: boolean;
  publishable: boolean;
  findings: Array<{ code: string; severity: string; message: string }>;
}

export interface AnalysisProjection {
  origin: AnalysisOrigin;
  runId: string | null;
  applicationStatus: string;
  question: string;
  organization: string;
  horizon: string;
  marketBoundary: string;
  completedAt: string | null;
  model: string;
  forces: ForceAssessment[];
  evidence: EvidenceItem[];
  scenarios: ScenarioProjection[];
  costOfDelay: {
    currency: string;
    periodMonths: number;
    low: number;
    base: number;
    high: number;
    basisIds: string[];
  } | null;
  boardBrief: BoardBriefProjection;
  approvals: Approval[];
  quality: QualityProjection;
  logs: Array<{ time: string; agent: string; text: string }>;
  artifactUrls: Record<string, string>;
}

type JsonRecord = Record<string, unknown>;

const forceMeta: Record<string, { id: string; shortName: string; name: string; tone: Tone }> = {
  threat_of_new_entrants: {
    id: "new-entrants",
    shortName: "Entrants",
    name: "Threat of new entrants",
    tone: "cyan",
  },
  bargaining_power_of_suppliers: {
    id: "suppliers",
    shortName: "Suppliers",
    name: "Bargaining power of suppliers",
    tone: "amber",
  },
  bargaining_power_of_buyers: {
    id: "buyers",
    shortName: "Buyers",
    name: "Bargaining power of buyers",
    tone: "green",
  },
  threat_of_substitutes: {
    id: "substitutes",
    shortName: "Substitutes",
    name: "Threat of substitutes",
    tone: "violet",
  },
  competitive_rivalry: {
    id: "rivalry",
    shortName: "Rivalry",
    name: "Competitive rivalry",
    tone: "red",
  },
};

const sourceAuthority: Record<string, number> = {
  regulator: 1,
  legislation: 1,
  official_statistics: 0.95,
  audited_filing: 0.95,
  academic: 0.85,
  company_disclosure: 0.8,
  internal_document: 0.8,
  industry_research: 0.7,
  reputable_media: 0.65,
  user_assertion: 0.5,
  vendor: 0.45,
  search_snippet: 0.1,
  model_prior: 0.1,
  calculator: 1,
};

const approvalRoles: Approval["role"][] = ["Strategy", "Finance", "Technology", "Risk"];

function record(value: unknown): JsonRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.map(record).filter((item): item is JsonRecord => item !== null) : [];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function text(value: unknown, fallback = ""):
  string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function numeric(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return fallback;
}

function percentage(value: unknown): number {
  const parsed = numeric(value);
  return Math.round(Math.max(0, Math.min(1, parsed)) * 100);
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function dateLabel(value: unknown, fallback = "Not supplied"): string {
  if (typeof value !== "string" || !value) return fallback;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(parsed);
}

function boardPoint(value: unknown, fallback: string): string {
  return text(record(value)?.text, fallback);
}

function boardPoints(value: unknown): string[] {
  return records(value).map((item) => text(item.text)).filter(Boolean);
}

function pendingApprovals(): Approval[] {
  return approvalRoles.map((role) => ({
    role,
    reviewer: "Unassigned",
    status: "Pending",
    time: "Awaiting review",
    note: `No ${role.toLowerCase()} decision is recorded for this revision.`,
  }));
}

export function projectApprovals(value: unknown): Approval[] {
  const canonical = records(value)
    .map((item) => {
      const role = titleCase(text(item.role)) as Approval["role"];
      if (!approvalRoles.includes(role)) return null;
      const decision = text(item.decision).toLowerCase();
      const status: Approval["status"] = decision === "approve"
        ? "Approved"
        : decision === "reject"
          ? "Returned"
          : "Pending";
      return {
        role,
        reviewer: text(item.reviewer, "Unassigned"),
        status,
        time: dateLabel(item.reviewed_at, "Awaiting review"),
        note: status === "Approved"
          ? "Exact current brief fingerprint approved."
          : status === "Returned"
            ? "Exact current brief rejected; a new revision is required."
            : `No ${role.toLowerCase()} decision is recorded for this revision.`,
        briefSha256: text(item.brief_sha256),
        reviewedAt: text(item.reviewed_at),
      };
    })
    .filter((item) => item !== null)
    .sort((left, right) => left.reviewedAt.localeCompare(right.reviewedAt));

  const latest = new Map<Approval["role"], Approval>();
  for (const item of canonical) latest.set(item.role, item);
  return pendingApprovals().map((item) => latest.get(item.role) ?? item);
}

function forceProjection(
  value: JsonRecord,
  claimsById: Map<string, JsonRecord>,
): ForceAssessment | null {
  const forceName = text(value.force);
  const meta = forceMeta[forceName];
  if (!meta) return null;
  const drivers = records(value.drivers);
  const supporting = strings(value.evidence_for_claim_ids)
    .map((id) => text(claimsById.get(id)?.statement))
    .filter(Boolean);
  const opposing = strings(value.evidence_against_claim_ids)
    .map((id) => text(claimsById.get(id)?.statement))
    .filter(Boolean);
  const implications = strings(value.strategic_implications);
  const exposures = strings(value.organization_exposures);
  return {
    ...meta,
    score: Math.round(Math.max(1, Math.min(5, numeric(value.pressure_score, 1))) * 20),
    delta: 0,
    trend: titleCase(text(value.trend, "uncertain")),
    confidence: percentage(value.confidence),
    thesis: exposures.join(" ") || "No organization-specific exposure was supplied.",
    mechanism: drivers.map((driver) => text(driver.mechanism)).filter(Boolean),
    evidenceFor: supporting.join(" ") || "No explicit supporting claim is linked.",
    evidenceAgainst: opposing.join(" ") || "No explicit counterclaim is linked.",
    implication: implications.join(" ") || "No strategic implication was supplied.",
    leadingIndicators: strings(value.leading_indicators),
    conditionsThatChange: strings(value.conditions_that_change_conclusion),
  };
}

function evidenceProjection(
  item: JsonRecord,
  links: JsonRecord[],
  claimForce: Map<string, string>,
): EvidenceItem | null {
  const id = text(item.evidence_id);
  if (!id) return null;
  const relevantLinks = links.filter((link) => text(link.evidence_id) === id);
  const firstLink = relevantLinks[0];
  const force = firstLink ? claimForce.get(text(firstLink.claim_id)) : undefined;
  const sourceClass = text(item.source_class, "unknown");
  const utility = sourceAuthority[sourceClass] ?? 0;
  const authority = Math.round(
    utility
      * numeric(item.quality_score)
      * numeric(item.freshness_score)
      * numeric(item.applicability_score)
      * 100,
  );
  const stanceValue = text(firstLink?.stance, "context");
  const stance: EvidenceItem["stance"] = stanceValue === "supports"
    ? "Supports"
    : stanceValue === "contradicts"
      ? "Contradicts"
      : "Context";
  const url = text(item.source_url);
  return {
    id,
    title: text(item.title, "Untitled evidence item"),
    publisher: text(item.publisher, titleCase(text(item.origin, "Unknown basis"))),
    sourceClass: titleCase(sourceClass),
    published: dateLabel(item.published_at),
    captured: dateLabel(item.retrieved_at),
    stance,
    authority,
    force: force ? forceMeta[force]?.name ?? titleCase(force) : "Cross-force",
    url,
    status: text(item.origin) === "public_web" && Boolean(item.content_sha256) ? "Captured" : "Review",
  };
}

function scenarioProjection(value: JsonRecord, index: number): ScenarioProjection | null {
  const npv = record(value.npv);
  const name = text(value.scenario_name);
  if (!npv || !name) return null;
  const currency = text(value.currency, text(npv.unit, "currency units"));
  return {
    id: `scenario-${index + 1}`,
    name,
    currency,
    unitLabel: text(npv.unit, currency),
    npvLow: numeric(npv.low),
    npvBase: numeric(npv.base),
    npvHigh: numeric(npv.high),
    payback: typeof value.base_discounted_payback_month === "number"
      ? `${value.base_discounted_payback_month} months`
      : "Not reached in horizon",
    basisIds: strings(npv.basis_ids),
    warnings: strings(value.warnings),
    tone: index === 0 ? "cyan" : index === 1 ? "green" : "amber",
  };
}

function artifactUrls(value: unknown): Record<string, string> {
  const artifacts = record(value);
  if (!artifacts) return {};
  return Object.fromEntries(
    Object.entries(artifacts).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].startsWith("/api/"),
    ),
  );
}

export function projectCompletedAnalysis(payload: unknown): AnalysisProjection | null {
  const root = record(payload);
  const details = record(root?.details);
  if (!root || !details) return null;
  const applicationStatus = text(root.application_status, text(details.status));
  if (!["achieved_draft", "human_required", "publishable", "blocked"].includes(applicationStatus)) return null;

  const brief = record(details.board_brief);
  const forceRows = records(details.force_assessments);
  const evidenceLedger = record(details.evidence_ledger);
  if (!brief || forceRows.length !== 5 || !evidenceLedger) return null;

  const claims = records(evidenceLedger.claims);
  const links = records(evidenceLedger.links);
  const claimsById = new Map(claims.map((item) => [text(item.claim_id), item]));
  const forceRowsByName = new Map(forceRows.map((item) => [text(item.force), item]));
  const claimForce = new Map<string, string>();
  for (const [forceName, force] of forceRowsByName) {
    for (const claimId of [
      ...strings(force.evidence_for_claim_ids),
      ...strings(force.evidence_against_claim_ids),
    ]) claimForce.set(claimId, forceName);
  }
  const projectedForces = forceRows
    .map((item) => forceProjection(item, claimsById))
    .filter((item): item is ForceAssessment => item !== null);
  if (projectedForces.length !== 5) return null;

  const request = record(details.request);
  const analogies = records(brief.analogies);
  const analogy = analogies[0];
  const quality = record(details.quality_report);
  const ralph = record(details.ralph_state);
  const attempts = records(ralph?.attempts);
  const projectedLogs = attempts.flatMap((attempt) => {
    const report = record(attempt.report);
    const evaluations = records(report?.evaluations);
    const attemptNumber = numeric(attempt.attempt_number);
    return evaluations.map((evaluation) => ({
      time: dateLabel(attempt.completed_at, `Attempt ${attemptNumber}`),
      agent: "ralph/evaluator",
      text: `${text(evaluation.criterion_id, "criterion")} · ${text(evaluation.outcome, "unknown")} · ${text(evaluation.explanation, "No evaluator explanation")}`,
    }));
  });
  const cost = record(details.cost_of_delay);
  const costRange = record(cost?.cost_of_delay);

  return {
    origin: "backend",
    runId: text(root.run_id, text(details.run_id)),
    applicationStatus,
    question: text(request?.question, text(brief.decision_requested)),
    organization: titleCase(text(request?.archetype, "Financial services organization")),
    horizon: `${numeric(request?.time_horizon_months, 0)} months`,
    marketBoundary: text(request?.industry_arena, "Not supplied"),
    completedAt: text(root.completed_at, text(details.completed_at)) || null,
    model: text(root.model, text(details.model_id, "Not reported")),
    forces: projectedForces,
    evidence: records(evidenceLedger.evidence)
      .map((item) => evidenceProjection(item, links, claimForce))
      .filter((item): item is EvidenceItem => item !== null),
    scenarios: records(details.scenario_economics)
      .map(scenarioProjection)
      .filter((item): item is ScenarioProjection => item !== null),
    costOfDelay: cost && costRange ? {
      currency: text(cost.currency, text(costRange.unit, "currency units")),
      periodMonths: numeric(cost.period_months),
      low: numeric(costRange.low),
      base: numeric(costRange.base),
      high: numeric(costRange.high),
      basisIds: strings(costRange.basis_ids),
    } : null,
    boardBrief: {
      asOf: text(brief.as_of),
      decisionRequested: text(brief.decision_requested),
      recommendation: boardPoint(brief.recommendation, "No recommendation supplied."),
      whyNow: boardPoints(brief.why_now),
      noActionCase: boardPoints(brief.no_action_case),
      uncertainties: boardPoints(brief.largest_uncertainties),
      smallestCommitment: boardPoint(brief.smallest_sensible_commitment, "Not supplied."),
      dissentingView: boardPoint(brief.dissenting_view, "No dissenting view supplied."),
      boardQuestions: strings(brief.board_questions),
      analogy: analogy ? {
        unfamiliarConcept: text(analogy.unfamiliar_concept),
        familiarMechanism: text(analogy.familiar_mechanism),
        correspondences: strings(analogy.correspondences),
        decisionImplication: text(analogy.decision_implication),
        whereItBreaks: text(analogy.where_it_breaks),
      } : null,
    },
    approvals: projectApprovals(details.approvals),
    quality: {
      draftValid: quality?.draft_valid === true,
      publishable: quality?.publishable === true,
      findings: records(quality?.findings).map((finding) => ({
        code: text(finding.code, "UNSPECIFIED"),
        severity: text(finding.severity, "info"),
        message: text(finding.message, "No finding detail supplied."),
      })),
    },
    logs: projectedLogs,
    artifactUrls: artifactUrls(details.artifact_paths),
  };
}

export const illustrativeProjection: AnalysisProjection = {
  origin: "illustrative",
  runId: null,
  applicationStatus: "illustrative",
  question: "Should we fund a governed enterprise AI platform now, scale selected use cases, or defer investment for 18 months?",
  organization: "Illustrative global bank",
  horizon: "36 months",
  marketBoundary: "Illustrative global banking market",
  completedAt: null,
  model: "Illustrative fixture",
  forces: illustrativeForces,
  evidence: illustrativeEvidence,
  scenarios: illustrativeScenarios.map((scenario) => ({
    id: scenario.id,
    name: scenario.name,
    currency: "USD",
    unitLabel: "USD millions",
    npvLow: scenario.npvLow,
    npvBase: Math.round((scenario.npvLow + scenario.npvHigh) / 2),
    npvHigh: scenario.npvHigh,
    payback: scenario.breakeven,
    basisIds: ["ILLUSTRATIVE-ASSUMPTIONS"],
    warnings: ["Demonstration values only; not a forecast or completed analysis."],
    tone: scenario.tone,
  })),
  costOfDelay: {
    currency: "USD",
    periodMonths: 36,
    low: 91,
    base: 149,
    high: 208,
    basisIds: ["ILLUSTRATIVE-ASSUMPTIONS"],
  },
  boardBrief: {
    asOf: "2026-08-18T00:00:00Z",
    decisionRequested: "Choose a bounded adoption posture for an illustrative enterprise AI program.",
    recommendation: "Fund controlled adoption with explicit option value and evidence-gated tranches.",
    whyNow: [
      "Competitive pressure is moving from experimentation toward operating-model leverage.",
      "Waiting postpones the institution-specific evidence needed to govern production AI.",
      "Portability can preserve option value when it is designed and tested explicitly.",
    ],
    noActionCase: [
      "Delay may reduce near-term spending while allowing capability, talent, and learning gaps to compound.",
    ],
    uncertainties: [
      "Realized productivity depends on workflow adoption and accountable capacity release.",
      "Provider portability has not been demonstrated under production controls.",
    ],
    smallestCommitment: "Bound the first tranche to four workflows, named owners, explicit controls, and stop conditions.",
    dissentingView: "Model economics may improve quickly enough that a platform commitment today creates avoidable lock-in.",
    boardQuestions: [
      "What evidence would earn the next investment tranche?",
      "When will realized value appear in accountable financial measures?",
      "What would cause management to stop or redesign the program?",
    ],
    analogy: {
      unfamiliarConcept: "Governed enterprise AI adoption",
      familiarMechanism: "A bounded trading desk",
      correspondences: [
        "Risk capital ↔ investment tranche",
        "Position limits ↔ use-case boundary",
        "Daily P&L ↔ value telemetry",
        "Stop-loss ↔ scale or exit gate",
      ],
      decisionImplication: "Approve bounded risk-taking with evidence required to earn the next allocation.",
      whereItBreaks: "AI delivery changes workflows and controls across the institution; it is not a separable book of market positions.",
    },
  },
  approvals: initialApprovals,
  quality: {
    draftValid: true,
    publishable: false,
    findings: [{
      code: "ILLUSTRATIVE_ONLY",
      severity: "info",
      message: "These quality signals demonstrate the interface and do not describe a backend run.",
    }],
  },
  logs: initialLogs,
  artifactUrls: {},
};
