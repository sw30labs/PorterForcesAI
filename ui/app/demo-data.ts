export type ViewId =
  | "overview"
  | "new-analysis"
  | "run"
  | "forces"
  | "evidence"
  | "economics"
  | "brief"
  | "approvals"
  | "settings";

export type Tone = "cyan" | "amber" | "green" | "red" | "violet" | "slate";

export interface ForceAssessment {
  id: string;
  shortName: string;
  name: string;
  score: number;
  delta: number;
  trend?: string;
  confidence: number;
  tone: Tone;
  thesis: string;
  mechanism: string[];
  evidenceFor: string;
  evidenceAgainst: string;
  implication: string;
  leadingIndicators?: string[];
  conditionsThatChange?: string[];
}

export interface EvidenceItem {
  id: string;
  title: string;
  publisher: string;
  sourceClass: string;
  published: string;
  captured: string;
  stance: "Supports" | "Contradicts" | "Context";
  authority: number;
  force: string;
  url: string;
  status: "Verified" | "Review";
}

export interface Scenario {
  id: string;
  name: string;
  posture: string;
  investment: number;
  annualBenefit: number;
  downside: number;
  npvLow: number;
  npvHigh: number;
  breakeven: string;
  delayCost: number;
  confidence: string;
  tone: Tone;
}

export interface Approval {
  role: "Strategy" | "Finance" | "Technology" | "Risk";
  reviewer: string;
  status: "Approved" | "Pending" | "Returned";
  time: string;
  note: string;
  briefSha256?: string;
}

export interface RunPhase {
  id: string;
  label: string;
  detail: string;
  start: number;
  end: number;
}

export const forces: ForceAssessment[] = [
  {
    id: "new-entrants",
    shortName: "Entrants",
    name: "Threat of new entrants",
    score: 62,
    delta: 7,
    confidence: 76,
    tone: "cyan",
    thesis:
      "Foundation-model access lowers product prototyping barriers, but balance-sheet trust, licensing and distribution still protect the core franchise.",
    mechanism: [
      "Model access compresses build time",
      "Fintechs target profitable workflows",
      "Licensing and trust slow full-stack entry",
      "Margin pressure arrives before deposit displacement",
    ],
    evidenceFor: "Seven captured sources show declining software and distribution barriers.",
    evidenceAgainst: "No evidence that general-purpose AI removes capital or licensing requirements.",
    implication: "Defend high-trust workflows; do not mistake regulatory barriers for product insulation.",
  },
  {
    id: "suppliers",
    shortName: "Suppliers",
    name: "Supplier power",
    score: 78,
    delta: 11,
    confidence: 84,
    tone: "amber",
    thesis:
      "Concentration in frontier models, accelerators and specialist talent transfers negotiating power to a small supplier set.",
    mechanism: [
      "Critical capability concentrates",
      "Switching costs enter data and controls",
      "Exit lead-time lengthens",
      "Unit economics and resilience become coupled",
    ],
    evidenceFor: "Regulatory surveys and vendor disclosures converge on concentration risk.",
    evidenceAgainst: "Open-weight models create a credible ceiling on application-layer pricing.",
    implication: "Fund portability and model-routing as strategic options, not only architecture hygiene.",
  },
  {
    id: "buyers",
    shortName: "Buyers",
    name: "Buyer power",
    score: 71,
    delta: 3,
    confidence: 73,
    tone: "green",
    thesis:
      "Corporate and wealth clients increasingly expect AI-assisted service without accepting weaker controls or higher fees.",
    mechanism: [
      "Experience benchmark rises",
      "Price transparency improves",
      "Differentiation window narrows",
      "Retention cost increases",
    ],
    evidenceFor: "Client surveys show rising expectations for personalized, immediate service.",
    evidenceAgainst: "Switching inertia remains material in regulated, integrated relationships.",
    implication: "Use AI to increase relationship depth before passing efficiency gains into price.",
  },
  {
    id: "substitutes",
    shortName: "Substitutes",
    name: "Threat of substitutes",
    score: 69,
    delta: 9,
    confidence: 70,
    tone: "violet",
    thesis:
      "Embedded finance and AI-native advice can unbundle selected interactions even when the regulated balance sheet stays central.",
    mechanism: [
      "Advice separates from product",
      "Distribution moves into platforms",
      "Primary interface ownership shifts",
      "Cross-sell economics erode",
    ],
    evidenceFor: "Platform partnerships demonstrate substitution at the interface layer.",
    evidenceAgainst: "Customers still prefer regulated institutions for custody and complex risk transfer.",
    implication: "Measure share of customer decisioning, not only share of product balances.",
  },
  {
    id: "rivalry",
    shortName: "Rivalry",
    name: "Competitive rivalry",
    score: 84,
    delta: 12,
    confidence: 88,
    tone: "red",
    thesis:
      "Peers are converting AI from experimentation into operating-model leverage; undirected pilots create cost without strategic learning.",
    mechanism: [
      "Peer investment accelerates",
      "Cycle time and service quality diverge",
      "Talent follows credible platforms",
      "Capability gap compounds",
    ],
    evidenceFor: "Peer filings consistently link AI programs to service, controls and productivity.",
    evidenceAgainst: "Public disclosures rarely establish realized risk-adjusted returns.",
    implication: "Compete on governed learning velocity, not pilot count or model novelty.",
  },
];

export const evidence: EvidenceItem[] = [
  {
    id: "E-001",
    title: "Artificial intelligence in UK financial services — 2024 survey",
    publisher: "Bank of England / FCA",
    sourceClass: "Regulator",
    published: "21 Nov 2024",
    captured: "18 Aug 2026",
    stance: "Supports",
    authority: 96,
    force: "Supplier power",
    url: "https://www.bankofengland.co.uk/report/2024/artificial-intelligence-in-uk-financial-services-2024",
    status: "Verified",
  },
  {
    id: "E-002",
    title: "Digital Operational Resilience Act — consolidated requirements",
    publisher: "European Commission",
    sourceClass: "Regulator",
    published: "17 Jan 2025",
    captured: "18 Aug 2026",
    stance: "Context",
    authority: 98,
    force: "Supplier power",
    url: "https://finance.ec.europa.eu/regulation-and-supervision/financial-services-legislation/digital-operational-resilience-act_en",
    status: "Verified",
  },
  {
    id: "E-003",
    title: "Annual report: AI-enabled client service and operating leverage",
    publisher: "Peer bank filing",
    sourceClass: "Company filing",
    published: "24 Feb 2026",
    captured: "18 Aug 2026",
    stance: "Supports",
    authority: 82,
    force: "Competitive rivalry",
    url: "https://www.sec.gov/edgar/search/",
    status: "Verified",
  },
  {
    id: "E-004",
    title: "Third-party risk management guidance for banking organizations",
    publisher: "Federal Reserve / FDIC / OCC",
    sourceClass: "Regulator",
    published: "06 Jun 2023",
    captured: "17 Aug 2026",
    stance: "Context",
    authority: 97,
    force: "Supplier power",
    url: "https://www.federalreserve.gov/newsevents/pressreleases/bcreg20230606a.htm",
    status: "Verified",
  },
  {
    id: "E-005",
    title: "AI adoption: public claims exceed disclosed realized returns",
    publisher: "Cross-filing review",
    sourceClass: "Research",
    published: "12 Aug 2026",
    captured: "18 Aug 2026",
    stance: "Contradicts",
    authority: 74,
    force: "Competitive rivalry",
    url: "https://www.sec.gov/edgar/search/",
    status: "Review",
  },
  {
    id: "E-006",
    title: "Global financial services AI market and customer expectations",
    publisher: "Industry consortium",
    sourceClass: "Industry",
    published: "03 Apr 2026",
    captured: "17 Aug 2026",
    stance: "Supports",
    authority: 68,
    force: "Buyer power",
    url: "https://www.bis.org/",
    status: "Review",
  },
  {
    id: "E-007",
    title: "AI Act: risk classification and deployer obligations",
    publisher: "European Commission",
    sourceClass: "Regulator",
    published: "01 Aug 2024",
    captured: "16 Aug 2026",
    stance: "Context",
    authority: 97,
    force: "Threat of new entrants",
    url: "https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai",
    status: "Verified",
  },
  {
    id: "E-008",
    title: "Open-weight model economics reduce inference switching barriers",
    publisher: "Model ecosystem review",
    sourceClass: "Research",
    published: "11 Aug 2026",
    captured: "18 Aug 2026",
    stance: "Contradicts",
    authority: 71,
    force: "Supplier power",
    url: "https://huggingface.co/models",
    status: "Review",
  },
];

export const scenarios: Scenario[] = [
  {
    id: "controlled",
    name: "Controlled adoption",
    posture: "Recommended",
    investment: 84,
    annualBenefit: 126,
    downside: 36,
    npvLow: 148,
    npvHigh: 282,
    breakeven: "17 months",
    delayCost: 9.8,
    confidence: "Medium–high",
    tone: "cyan",
  },
  {
    id: "accelerated",
    name: "Accelerated scale",
    posture: "Higher variance",
    investment: 144,
    annualBenefit: 212,
    downside: 74,
    npvLow: 210,
    npvHigh: 395,
    breakeven: "14 months",
    delayCost: 14.2,
    confidence: "Medium",
    tone: "green",
  },
  {
    id: "hold",
    name: "Hold / no incremental action",
    posture: "Comparator",
    investment: 24,
    annualBenefit: 12,
    downside: 188,
    npvLow: -208,
    npvHigh: -91,
    breakeven: "Not reached",
    delayCost: 12.4,
    confidence: "Medium",
    tone: "amber",
  },
];

export const initialApprovals: Approval[] = [
  {
    role: "Strategy",
    reviewer: "A. Mercer",
    status: "Approved",
    time: "20 Aug · 09:12",
    note: "Decision framing and option set accepted.",
  },
  {
    role: "Finance",
    reviewer: "L. Chen",
    status: "Pending",
    time: "Awaiting review",
    note: "Validate benefit realization ranges and accountable owners.",
  },
  {
    role: "Technology",
    reviewer: "R. Patel",
    status: "Approved",
    time: "20 Aug · 10:34",
    note: "Portability and control-plane assumptions accepted.",
  },
  {
    role: "Risk",
    reviewer: "S. Okafor",
    status: "Pending",
    time: "Awaiting review",
    note: "Review model-risk boundary and third-party concentration treatment.",
  },
];

export const runPhases: RunPhase[] = [
  { id: "frame", label: "Decision framing", detail: "Canonical decision contract", start: 0, end: 12 },
  { id: "research", label: "Five-force research", detail: "Bounded force-specific work", start: 12, end: 43 },
  { id: "ledger", label: "Evidence ledger", detail: "Capture and claim lineage", start: 43, end: 58 },
  { id: "assess", label: "Force assessment", detail: "Weighted ordinal scoring", start: 58, end: 70 },
  { id: "economics", label: "Scenario economics", detail: "Deterministic inputs only", start: 70, end: 80 },
  { id: "challenge", label: "Red-team challenge", detail: "Countercase and invalidation", start: 80, end: 90 },
  { id: "brief", label: "Board brief", detail: "Traceable synthesis", start: 90, end: 98 },
  { id: "approval", label: "Human approval", detail: "Exact-content role decisions", start: 98, end: 100 },
];

export const initialLogs = [
  { time: "14:31:48.204", agent: "evidence-gate", text: "E-008 retained as counterevidence; source authority 0.71" },
  { time: "14:31:41.882", agent: "force/suppliers", text: "Mechanism M-03 linked across supplier power and rivalry" },
  { time: "14:31:26.515", agent: "ralph", text: "Quality score 0.82; continuing because countercase coverage < target" },
  { time: "14:31:08.093", agent: "economics", text: "Recomputed delay scenario with finance-owned ranges" },
  { time: "14:30:54.611", agent: "source-capture", text: "Captured 8/42 discovered pages; 2 flagged for human review" },
];
