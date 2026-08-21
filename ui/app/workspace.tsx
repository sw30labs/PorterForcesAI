"use client";

import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  runPhases,
  type Approval,
  type ForceAssessment,
  type Tone,
  type ViewId,
} from "./demo-data";
import {
  illustrativeProjection,
  projectApprovals,
  projectCompletedAnalysis,
  type AnalysisProjection,
  type ScenarioProjection,
} from "./analysis-projection";
import {
  canonicalScenario,
  type FinanceRangeForm,
  type FinanceRangeKey,
  type FinanceScenarioForm,
} from "./intake-contract";

type ApiMode = "checking" | "connected" | "offline";

interface RunState {
  id: string;
  analysisId: string;
  status: "running" | "complete" | "awaiting_approval" | "blocked" | "failed" | "illustrative";
  progress: number;
  current: string;
  started: string;
  iteration: number;
  maxIterations: number;
  quality: number;
  error: string | null;
}

interface AnalysisFormState {
  question: string;
  organization: string;
  horizon: string;
  geography: string;
  context: string;
  publicResearchContext: string;
  boardObjection: string;
  webResearch: boolean;
  target: "draft" | "publishable";
  evidenceCutoff: string;
  finance: FinanceScenarioForm;
}

interface SettingsState {
  endpoint: string;
  model: string;
  searchRegion: string;
  maxSources: string;
}

interface RunSummary {
  id: string;
  status: string;
  updatedAt: string;
}

const navItems: Array<{
  id: ViewId;
  label: string;
  icon: IconName;
  section: "workspace" | "governance";
  badge?: string;
}> = [
  { id: "overview", label: "Overview", icon: "grid", section: "workspace" },
  { id: "new-analysis", label: "New analysis", icon: "plus", section: "workspace" },
  { id: "run", label: "Ralph monitor", icon: "pulse", section: "workspace" },
  { id: "forces", label: "Five forces", icon: "pentagon", section: "workspace" },
  { id: "evidence", label: "Evidence", icon: "sources", section: "workspace" },
  { id: "economics", label: "Economics", icon: "chart", section: "workspace" },
  { id: "brief", label: "Board brief", icon: "document", section: "workspace" },
  { id: "approvals", label: "Review & approvals", icon: "shield", section: "governance" },
  { id: "settings", label: "Settings", icon: "settings", section: "governance" },
];

const defaultRun: RunState = {
  id: "No backend run",
  analysisId: "Illustrative walkthrough",
  status: "illustrative",
  progress: 0,
  current: "No run commissioned",
  started: "—",
  iteration: 0,
  maxIterations: 0,
  quality: 0,
  error: null,
};

const defaultForm: AnalysisFormState = {
  question:
    "Should we fund a governed enterprise AI platform now, scale selected use cases, or defer investment for 18 months?",
  organization: "Global bank",
  horizon: "36 months",
  geography: "Global · US / UK / EU",
  context:
    "The bank has 34 production AI use cases, 117 pilots and fragmented controls across three cloud providers. The board must decide the 2027 investment envelope.",
  publicResearchContext:
    "Publicly reported AI adoption, operating-model investment, model-provider concentration, regulatory obligations, and realized returns across global banking. Exclude institution-specific internal facts.",
  boardObjection:
    "We survived cloud disruption and multiple crises. Why will waiting on AI be different?",
  webResearch: true,
  target: "publishable",
  evidenceCutoff: new Date().toISOString().slice(0, 10),
  finance: {
    enabled: false,
    scenarioName: "",
    currency: "USD",
    horizonYears: "",
    discountRatePercent: "",
    benefitStartMonth: "",
    basisId: "",
    upfrontCost: { low: "", base: "", high: "" },
    annualGrossBenefit: { low: "", base: "", high: "" },
    benefitRealizationRate: { low: "", base: "", high: "" },
    annualRunCost: { low: "", base: "", high: "" },
    annualControlCost: { low: "", base: "", high: "" },
    annualExpectedLoss: { low: "", base: "", high: "" },
  },
};

const defaultSettings: SettingsState = {
  endpoint: "http://127.0.0.1:8000/v1",
  model: "Qwen3.8-27B-4bit",
  searchRegion: "us-en",
  maxSources: "12",
};

type IconName =
  | "grid"
  | "plus"
  | "pulse"
  | "pentagon"
  | "sources"
  | "chart"
  | "document"
  | "shield"
  | "settings"
  | "arrow"
  | "search"
  | "clock"
  | "check"
  | "alert"
  | "download"
  | "play"
  | "refresh"
  | "external"
  | "chevron"
  | "spark"
  | "lock"
  | "close";

function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "grid":
      return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>;
    case "plus":
      return <svg {...common}><path d="M12 5v14M5 12h14"/><circle cx="12" cy="12" r="9"/></svg>;
    case "pulse":
      return <svg {...common}><path d="M3 12h4l2.2-6 4.1 12 2.2-6H21"/></svg>;
    case "pentagon":
      return <svg {...common}><path d="m12 2 9 6.5-3.4 10.6H6.4L3 8.5 12 2Z"/><path d="m12 7 4.3 3.1-1.6 5.1H9.3l-1.6-5.1L12 7Z"/></svg>;
    case "sources":
      return <svg {...common}><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v14H6.5A2.5 2.5 0 0 0 4 19.5v-14Z"/><path d="M4 19.5A1.5 1.5 0 0 0 5.5 21H20v-4H6.5A2.5 2.5 0 0 0 4 19.5Z"/><path d="M8 7h8M8 11h6"/></svg>;
    case "chart":
      return <svg {...common}><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>;
    case "document":
      return <svg {...common}><path d="M6 2h8l4 4v16H6V2Z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></svg>;
    case "shield":
      return <svg {...common}><path d="M12 22s8-3.7 8-10V5l-8-3-8 3v7c0 6.3 8 10 8 10Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/></svg>;
    case "settings":
      return <svg {...common}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.82 2.82-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21H9.6v-.1A1.7 1.7 0 0 0 8.5 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.82-2.82.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.5-1H3v-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.82-2.82.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.5V3h4v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.82 2.82-.06.06A1.7 1.7 0 0 0 19.4 9a1.7 1.7 0 0 0 1.5 1h.1v4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>;
    case "arrow":
      return <svg {...common}><path d="M5 12h14M14 7l5 5-5 5"/></svg>;
    case "search":
      return <svg {...common}><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>;
    case "clock":
      return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>;
    case "check":
      return <svg {...common}><path d="m5 12 4 4L19 6"/></svg>;
    case "alert":
      return <svg {...common}><path d="M12 3 2.8 20h18.4L12 3Z"/><path d="M12 9v4M12 17h.01"/></svg>;
    case "download":
      return <svg {...common}><path d="M12 3v12M7 10l5 5 5-5M4 21h16"/></svg>;
    case "play":
      return <svg {...common}><path d="m8 5 11 7-11 7V5Z"/></svg>;
    case "refresh":
      return <svg {...common}><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 8A7 7 0 0 1 18.5 6.5L20 12M4 12l1.5 5.5A7 7 0 0 0 17.9 16"/></svg>;
    case "external":
      return <svg {...common}><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v6H5V6h6"/></svg>;
    case "chevron":
      return <svg {...common}><path d="m8 10 4 4 4-4"/></svg>;
    case "spark":
      return <svg {...common}><path d="m12 2 1.4 5.6L19 9l-5.6 1.4L12 16l-1.4-5.6L5 9l5.6-1.4L12 2ZM19 15l.6 2.4L22 18l-2.4.6L19 21l-.6-2.4L16 18l2.4-.6L19 15Z"/></svg>;
    case "lock":
      return <svg {...common}><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>;
    case "close":
      return <svg {...common}><path d="m6 6 12 12M18 6 6 18"/></svg>;
  }
}

function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 42 42" aria-hidden="true">
      <defs>
        <linearGradient id="markGradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#67e8f9" />
          <stop offset="1" stopColor="#34d399" />
        </linearGradient>
      </defs>
      <path d="M21 2.5 37 11.7l-2.2 18.2L21 39.5 7.2 29.9 5 11.7 21 2.5Z" fill="rgba(34,211,238,.04)" stroke="url(#markGradient)" strokeWidth="1.2"/>
      <path d="m21 8.5 10.2 7.4-3.9 12H14.7l-3.9-12L21 8.5Z" fill="none" stroke="rgba(103,232,249,.55)"/>
      <path d="m21 13.5 4.5 3.3-1.7 5.3-2.8 2-2.8-2-1.7-5.3 4.5-3.3Z" fill="rgba(52,211,153,.16)" stroke="#5eead4"/>
      <circle cx="21" cy="21" r="1.8" fill="#67e8f9"/>
    </svg>
  );
}

function StatusBadge({
  children,
  tone = "slate",
  dot = false,
}: {
  children: ReactNode;
  tone?: Tone;
  dot?: boolean;
}) {
  return (
    <span className={`status-badge tone-${tone}`}>
      {dot && <span className="status-dot" />}
      {children}
    </span>
  );
}

function Panel({
  title,
  eyebrow,
  actions,
  className = "",
  children,
}: {
  title?: string;
  eyebrow?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <div className="panel-head">
          <div>
            {eyebrow && <span className="panel-eyebrow">{eyebrow}</span>}
            {title && <h2>{title}</h2>}
          </div>
          {actions && <div className="panel-actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

function ViewHeader({
  code,
  title,
  accent,
  description,
  actions,
}: {
  code: string;
  title: string;
  accent: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="view-header">
      <div>
        <span className="view-code">{code}</span>
        <h1>{title} <em>{accent}</em></h1>
        <p>{description}</p>
      </div>
      {actions && <div className="view-actions">{actions}</div>}
    </header>
  );
}

function PrimaryButton({
  children,
  icon,
  onClick,
  type = "button",
  disabled = false,
}: {
  children: ReactNode;
  icon?: IconName;
  onClick?: () => void;
  type?: "button" | "submit";
  disabled?: boolean;
}) {
  return (
    <button className="button primary" type={type} onClick={onClick} disabled={disabled}>
      {icon && <Icon name={icon} size={15} />}
      {children}
    </button>
  );
}

function SecondaryButton({
  children,
  icon,
  onClick,
  danger = false,
  disabled = false,
}: {
  children: ReactNode;
  icon?: IconName;
  onClick?: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button className={`button secondary${danger ? " danger" : ""}`} type="button" onClick={onClick} disabled={disabled}>
      {icon && <Icon name={icon} size={15} />}
      {children}
    </button>
  );
}

function ScoreBar({ score, tone = "cyan", compact = false }: { score: number; tone?: Tone; compact?: boolean }) {
  return (
    <div className={`score-bar ${compact ? "compact" : ""}`}>
      <span className={`score-fill tone-${tone}`} style={{ width: `${Math.max(0, Math.min(score, 100))}%` }} />
    </div>
  );
}

function formatMoney(value: number) {
  const sign = value < 0 ? "−" : "";
  return `${sign}$${Math.abs(value)}M`;
}

function formatScenarioValue(value: number, scenario: ScenarioProjection) {
  if (scenario.unitLabel === "USD millions") return formatMoney(value);
  return `${scenario.unitLabel} ${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)}`;
}

function currentPhase(progress: number) {
  return runPhases.find((phase) => progress >= phase.start && progress < phase.end) ?? runPhases.at(-1)!;
}

function apiRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : null;
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload = apiRecord(await response.json());
    if (typeof payload?.detail === "string") return payload.detail;
    if (Array.isArray(payload?.detail)) {
      const messages = payload.detail.flatMap((item) => {
        const issue = apiRecord(item);
        const location = Array.isArray(issue?.loc) ? issue.loc.slice(1).join(".") : "request";
        return typeof issue?.msg === "string" ? [`${location}: ${issue.msg}`] : [];
      });
      if (messages.length) return messages.join(" · ");
    }
  } catch {
    // The status code remains the authoritative fallback for a non-JSON error.
  }
  return `Request failed with HTTP ${response.status}`;
}

function remoteRunState(payload: unknown, previous: RunState): RunState | null {
  const root = apiRecord(payload);
  if (!root) return null;
  const candidate = apiRecord(root.active_run) ?? apiRecord(root.activeRun) ?? apiRecord(root.run) ?? root;
  const progress = typeof candidate.progress === "number" ? candidate.progress : null;
  if (progress === null) return null;
  const rawStatus = typeof candidate.status === "string" ? candidate.status : "";
  const applicationStatus = typeof candidate.application_status === "string" ? candidate.application_status : "";
  const statusToken = applicationStatus || rawStatus;
  const status: RunState["status"] = statusToken === "failed"
    ? "failed"
    : statusToken === "blocked"
      ? "blocked"
      : statusToken === "human_required" || rawStatus === "awaiting_approval"
        ? "awaiting_approval"
        : statusToken === "achieved_draft" || statusToken === "publishable" || rawStatus === "complete"
          ? "complete"
          : ["created", "acquiring_evidence", "verifying", "queued", "running"].includes(statusToken)
            ? "running"
        : previous.status;
  const startedAt = typeof candidate.started_at === "string" ? new Date(candidate.started_at) : null;
  return {
    ...previous,
    id: typeof candidate.id === "string" ? candidate.id : previous.id,
    analysisId: typeof candidate.analysis_id === "string" ? candidate.analysis_id : previous.analysisId,
    progress: Math.max(0, Math.min(100, progress)),
    current: typeof candidate.current_phase === "string" ? candidate.current_phase : currentPhase(progress).label,
    status,
    started: startedAt && !Number.isNaN(startedAt.valueOf())
      ? startedAt.toLocaleTimeString("en-US", { hour12: false })
      : previous.started,
    iteration: typeof candidate.iteration === "number" ? candidate.iteration : previous.iteration,
    maxIterations: typeof candidate.max_iterations === "number" && candidate.max_iterations > 0
      ? candidate.max_iterations
      : previous.maxIterations,
    quality: typeof candidate.quality_score === "number"
      ? Math.round(candidate.quality_score <= 1 ? candidate.quality_score * 100 : candidate.quality_score)
      : previous.quality,
    error: typeof candidate.error === "string" ? candidate.error : null,
  };
}

function ForceRadar({ assessments }: { assessments: ForceAssessment[] }) {
  const center = 160;
  const radius = 112;
  const pointsFor = (factor: number) => assessments.map((force, index) => {
    const angle = -Math.PI / 2 + index * (Math.PI * 2 / assessments.length);
    const r = radius * factor * (force.score / 100);
    return `${center + Math.cos(angle) * r},${center + Math.sin(angle) * r}`;
  }).join(" ");
  const ringPoints = (factor: number) => assessments.map((_, index) => {
    const angle = -Math.PI / 2 + index * (Math.PI * 2 / assessments.length);
    return `${center + Math.cos(angle) * radius * factor},${center + Math.sin(angle) * radius * factor}`;
  }).join(" ");
  const average = Math.round(assessments.reduce((total, force) => total + force.score, 0) / assessments.length);

  return (
    <div className="radar-wrap">
      <svg className="force-radar" viewBox="0 0 320 320" role="img" aria-label="Five Forces pressure radar">
        {[0.25, 0.5, 0.75, 1].map((ring) => (
          <polygon key={ring} points={ringPoints(ring)} className="radar-ring" />
        ))}
        {assessments.map((force, index) => {
          const angle = -Math.PI / 2 + index * (Math.PI * 2 / assessments.length);
          return <line key={force.id} x1={center} y1={center} x2={center + Math.cos(angle) * radius} y2={center + Math.sin(angle) * radius} className="radar-axis" />;
        })}
        <polygon points={pointsFor(1)} className="radar-area" />
        {assessments.map((force, index) => {
          const angle = -Math.PI / 2 + index * (Math.PI * 2 / assessments.length);
          const r = radius * force.score / 100;
          const lx = center + Math.cos(angle) * (radius + 28);
          const ly = center + Math.sin(angle) * (radius + 28);
          return (
            <g key={force.id}>
              <circle cx={center + Math.cos(angle) * r} cy={center + Math.sin(angle) * r} r="4" className={`radar-point tone-${force.tone}`} />
              <text x={lx} y={ly} textAnchor={lx < 145 ? "end" : lx > 175 ? "start" : "middle"} dominantBaseline="middle" className="radar-label">{force.shortName}</text>
            </g>
          );
        })}
        <text x="160" y="153" textAnchor="middle" className="radar-center-value">{average}</text>
        <text x="160" y="170" textAnchor="middle" className="radar-center-label">PRESSURE</text>
      </svg>
    </div>
  );
}

function OverviewView({
  navigate,
  analysis,
  run,
}: {
  navigate: (id: ViewId) => void;
  analysis: AnalysisProjection;
  run: RunState;
}) {
  const pressure = Math.round(analysis.forces.reduce((sum, force) => sum + force.score, 0) / analysis.forces.length);
  const evidenceUtility = analysis.evidence.length
    ? Math.round(analysis.evidence.reduce((sum, item) => sum + item.authority, 0) / analysis.evidence.length)
    : 0;
  const pendingApprovals = analysis.approvals.filter((approval) => approval.status !== "Approved").length;
  const delay = analysis.costOfDelay;
  const analogy = analysis.boardBrief.analogy;
  return (
    <div className="view-stack">
      <ViewHeader
        code="01 / DECISION ROOM"
        title="Board intelligence"
        accent="overview"
        description="What the board needs to decide, what the evidence says, and what inaction costs."
        actions={<PrimaryButton icon="plus" onClick={() => navigate("new-analysis")}>New analysis</PrimaryButton>}
      />

      <div className="signal-strip">
        <div><span className="live-pip" /> <b>{analysis.runId ?? "WALKTHROUGH"}</b> · {run.current}</div>
        <span>Analysis as of · {new Date(analysis.boardBrief.asOf).toLocaleDateString()}</span>
        <span>Model · {analysis.model}</span>
        <button type="button" onClick={() => navigate("run")}>Open Ralph monitor <Icon name="arrow" size={13}/></button>
      </div>

      <div className="kpi-grid">
        <article className="kpi-card"><span>Analysis source</span><strong className="cyan">{analysis.origin === "backend" ? "LIVE" : "DEMO"}</strong><small>{analysis.runId ?? "Illustrative fixture"}</small></article>
        <article className="kpi-card"><span>Pressure index</span><strong>{pressure}<sup>/100</sup></strong><small>mean ordinal force pressure</small></article>
        <article className="kpi-card"><span>Evidence utility</span><strong className="green">{evidenceUtility}%</strong><small>{analysis.evidence.length} canonical evidence items</small></article>
        <article className="kpi-card"><span>Cost of delay</span><strong className="amber">{delay ? `${delay.currency} ${delay.base}` : "—"}</strong><small>{delay ? `${delay.periodMonths}-month base estimate` : "not modeled"}</small></article>
        <article className="kpi-card"><span>Publication</span><strong>{analysis.quality.publishable ? "OPEN" : "LOCKED"}</strong><small>{pendingApprovals} role approvals not recorded</small></article>
      </div>

      <div className="overview-grid">
        <Panel className="mandate-card" eyebrow="CURRENT MANDATE" title={analysis.boardBrief.decisionRequested} actions={<StatusBadge tone={analysis.quality.draftValid ? "green" : "amber"} dot>{analysis.quality.draftValid ? "Draft valid" : "Review required"}</StatusBadge>}>
          <p className="mandate-question">{analysis.question}</p>
          <div className="recommendation-callout">
            <span className="callout-icon"><Icon name="spark" size={19}/></span>
            <div>
              <span className="micro-label">BOARD-BRIEF RECOMMENDATION</span>
              <h3>{analysis.boardBrief.recommendation}</h3>
              <p>{analysis.boardBrief.smallestCommitment}</p>
            </div>
          </div>
          <div className="mandate-footer">
            <div><span>Organization</span><b>{analysis.organization}</b></div>
            <div><span>Time horizon</span><b>{analysis.horizon}</b></div>
            <div><span>Market boundary</span><b>{analysis.marketBoundary}</b></div>
          </div>
        </Panel>

        <Panel className="pressure-panel" eyebrow="FIVE FORCES" title="Pressure map" actions={<button className="text-button" type="button" onClick={() => navigate("forces")}>Full assessment <Icon name="arrow" size={12}/></button>}>
          <div className="force-summary-list">
            {analysis.forces.map((force) => (
              <button type="button" key={force.id} className="force-summary" onClick={() => navigate("forces")}>
                <span>{force.shortName}</span>
                <ScoreBar score={force.score} tone={force.tone} compact />
                <b className={`text-${force.tone}`}>{force.score}</b>
                <small>{force.trend ?? "Assessed"}</small>
              </button>
            ))}
          </div>
          <div className="pressure-legend"><span>Low</span><span>Structural pressure</span><span>High</span></div>
        </Panel>
      </div>

      <div className="three-grid">
        <Panel eyebrow="BOARD LENS" title="What changes the decision?">
          <div className="decision-list">
            {analysis.boardBrief.uncertainties.slice(0, 3).map((uncertainty, index) => <div className="decision-row" key={uncertainty}><span className="decision-index">{String(index + 1).padStart(2, "0")}</span><div><b>Material uncertainty</b><p>{uncertainty}</p></div><StatusBadge tone="amber">Open</StatusBadge></div>)}
          </div>
        </Panel>

        <Panel eyebrow="NO-ACTION CASE" title="The cost of standing still">
          <div className="no-action-number">{delay ? `${delay.currency} ${delay.base}` : "Not modeled"}<span>{delay ? `${delay.periodMonths}-month base estimate` : "No deterministic cost-of-delay input"}</span></div>
          <p className="panel-copy">{analysis.boardBrief.noActionCase.join(" ")}</p>
          {delay && <div className="mini-bars">{([['Low', delay.low], ['Base', delay.base], ['High', delay.high]] as const).map(([label, value]) => <div key={label}><span>{label}</span><i><b style={{width:`${delay.high === 0 ? 0 : Math.max(4, Math.round(value / delay.high * 100))}%`}} /></i><em>{value}</em></div>)}</div>}
        </Panel>

        <Panel eyebrow="BOARD NARRATIVE" title="Analogy that travels">
          <blockquote>{analogy ? `“${analogy.unfamiliarConcept} works like ${analogy.familiarMechanism}.”` : "No board analogy was supplied."}</blockquote>
          <p className="panel-copy">{analogy?.decisionImplication ?? analysis.boardBrief.recommendation}</p>
          <button className="text-button spaced" type="button" onClick={() => navigate("brief")}>Open board Q&amp;A <Icon name="arrow" size={12}/></button>
        </Panel>
      </div>

      <Panel className="recent-panel" eyebrow="RECENT INTELLIGENCE" title="Evidence changing the thesis" actions={<button className="text-button" type="button" onClick={() => navigate("evidence")}>Evidence ledger <Icon name="arrow" size={12}/></button>}>
        <div className="intel-list">
          {analysis.evidence.slice(0, 4).map((item) => (
            <div className="intel-row" key={item.id}>
              <span className="intel-time">{item.captured.replace(" Aug 2026", " AUG")}</span>
              <span className={`intel-marker ${item.stance.toLowerCase()}`} />
              <div><b>{item.title}</b><small>{item.publisher} · {item.sourceClass}</small></div>
              <StatusBadge tone={item.stance === "Supports" ? "green" : item.stance === "Contradicts" ? "red" : "slate"}>{item.stance}</StatusBadge>
              <strong>{item.authority}</strong>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function NewAnalysisView({
  form,
  setForm,
  onSubmit,
  submitting,
}: {
  form: AnalysisFormState;
  setForm: (next: AnalysisFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  submitting: boolean;
}) {
  const update = <K extends keyof AnalysisFormState>(key: K, value: AnalysisFormState[K]) => setForm({ ...form, [key]: value });
  const updateFinance = <K extends keyof FinanceScenarioForm>(key: K, value: FinanceScenarioForm[K]) => setForm({ ...form, finance: { ...form.finance, [key]: value } });
  const updateRange = (key: FinanceRangeKey, field: keyof FinanceRangeForm, value: string) => setForm({ ...form, finance: { ...form.finance, [key]: { ...form.finance[key], [field]: value } } });
  return (
    <div className="view-stack">
      <ViewHeader code="02 / DECISION INTAKE" title="Commission a new" accent="analysis" description="Start with a board decision—not a technology topic. Ralph will convert it into a bounded, evidence-testable mandate." />
      <form className="analysis-layout" onSubmit={onSubmit}>
        <div className="form-stack">
          <Panel eyebrow="01 / DECISION" title="What must the board decide?">
            <label className="field full-field">
              <span>Decision question <em>Required</em></span>
              <textarea value={form.question} onChange={(event) => update("question", event.target.value)} rows={4} required />
              <small>Frame mutually exclusive choices and a time boundary. Avoid “What is our AI strategy?”</small>
            </label>
            <div className="form-grid three">
              <label className="field"><span>Institution archetype</span><select value={form.organization} onChange={(event) => update("organization", event.target.value)}><option>Global bank</option><option>Quant trading firm</option><option>Insurance group</option></select></label>
              <label className="field"><span>Decision horizon</span><select value={form.horizon} onChange={(event) => update("horizon", event.target.value)}><option>12 months</option><option>24 months</option><option>36 months</option><option>5 years</option></select></label>
              <label className="field"><span>Market boundary</span><input value={form.geography} onChange={(event) => update("geography", event.target.value)} /></label>
            </div>
            <div className="form-grid two">
              <label className="field"><span>Completion target</span><select value={form.target} onChange={(event) => update("target", event.target.value as AnalysisFormState["target"])}><option value="draft">Machine-verified draft</option><option value="publishable">Publishable after four approvals</option></select></label>
              <label className="field"><span>Evidence cutoff</span><input type="date" value={form.evidenceCutoff} onChange={(event) => update("evidenceCutoff", event.target.value)} required/><small>Live capture supports today or a future cutoff. Historical as-of research requires an archive and is rejected.</small></label>
            </div>
          </Panel>

          <Panel eyebrow="02 / INTERNAL CONTEXT" title="What does the model not know?">
            <div className="form-grid two">
              <label className="field"><span>Institution facts and constraints</span><textarea rows={6} value={form.context} onChange={(event) => update("context", event.target.value)} /></label>
              <label className="field"><span>Hardest board objection</span><textarea rows={6} value={form.boardObjection} onChange={(event) => update("boardObjection", event.target.value)} /></label>
            </div>
            <div className="security-note"><Icon name="lock" size={15}/><span><b>Internal context remains local.</b> It can shape hypotheses but cannot masquerade as externally verified evidence.</span></div>
          </Panel>

          <Panel eyebrow="03 / RESEARCH POLICY" title="Evidence and execution boundaries">
            <label className="field public-research-field">
              <span>Public research context <em>Required for web research</em></span>
              <textarea rows={4} value={form.publicResearchContext} onChange={(event) => update("publicResearchContext", event.target.value)} required={form.webResearch} />
              <small>Sanitized and explicitly approved for DuckDuckGo. Never paste confidential facts here; internal context above is never used to construct a public query.</small>
            </label>
            <div className="toggle-grid">
              <Toggle checked={form.webResearch} onChange={(value) => update("webResearch", value)} label="Current public evidence" detail="Use DuckDuckGo for discovery, then capture underlying pages." />
            </div>
          </Panel>

          <Panel eyebrow="04 / FINANCE-OWNED INPUTS" title="Optional deterministic scenario">
            <Toggle checked={form.finance.enabled} onChange={(value) => updateFinance("enabled", value)} label="Include scenario economics" detail="Send only explicit Finance-owned ranges to the deterministic calculator. No values are inferred by the model." />
            {form.finance.enabled && <div className="finance-form">
              <div className="form-grid three">
                <label className="field"><span>Scenario name</span><input value={form.finance.scenarioName} onChange={(event) => updateFinance("scenarioName", event.target.value)} required/></label>
                <label className="field"><span>Currency</span><input value={form.finance.currency} onChange={(event) => updateFinance("currency", event.target.value.toUpperCase())} minLength={3} maxLength={3} required/></label>
                <label className="field"><span>Finance basis ID</span><input value={form.finance.basisId} onChange={(event) => updateFinance("basisId", event.target.value)} placeholder="e.g. A-FIN-001" required/><small>Evidence or assumption identifier supporting every range.</small></label>
                <label className="field"><span>Horizon · years</span><input type="number" min="1" max="10" value={form.finance.horizonYears} onChange={(event) => updateFinance("horizonYears", event.target.value)} required/></label>
                <label className="field"><span>Discount rate · %</span><input type="number" min="0" max="50" step="0.01" value={form.finance.discountRatePercent} onChange={(event) => updateFinance("discountRatePercent", event.target.value)} required/></label>
                <label className="field"><span>Benefit start · month</span><input type="number" min="1" max="120" value={form.finance.benefitStartMonth} onChange={(event) => updateFinance("benefitStartMonth", event.target.value)} required/></label>
              </div>
              <div className="finance-ranges">
                <FinanceRangeInput label="Up-front cost" range={form.finance.upfrontCost} onChange={(field, value) => updateRange("upfrontCost", field, value)}/>
                <FinanceRangeInput label="Annual gross benefit" range={form.finance.annualGrossBenefit} onChange={(field, value) => updateRange("annualGrossBenefit", field, value)}/>
                <FinanceRangeInput label="Benefit realization · %" range={form.finance.benefitRealizationRate} onChange={(field, value) => updateRange("benefitRealizationRate", field, value)} max="100"/>
                <FinanceRangeInput label="Annual run cost" range={form.finance.annualRunCost} onChange={(field, value) => updateRange("annualRunCost", field, value)}/>
                <FinanceRangeInput label="Annual control cost" range={form.finance.annualControlCost} onChange={(field, value) => updateRange("annualControlCost", field, value)}/>
                <FinanceRangeInput label="Annual expected loss" range={form.finance.annualExpectedLoss} onChange={(field, value) => updateRange("annualExpectedLoss", field, value)}/>
              </div>
              <div className="security-note"><Icon name="lock" size={15}/><span><b>Finance owns these inputs.</b> Monetary values use the selected currency unit. Realization values are percentages converted to ratios at submission.</span></div>
            </div>}
          </Panel>
        </div>

        <aside className="analysis-rail">
          <Panel className="contract-preview" eyebrow="DECISION CONTRACT" title="Ralph’s operating brief">
            <div className="contract-status"><span className="live-pip"/> Submission contract</div>
            <dl className="contract-list">
              <div><dt>Decision owner</dt><dd>Board / delegated committee</dd></div>
              <div><dt>Options required</dt><dd>Act · stage · delay / no action</dd></div>
              <div><dt>Framework</dt><dd>Porter Five Forces + FS overlays</dd></div>
              <div><dt>Evidence cutoff</dt><dd>Today · reproducible capture</dd></div>
              <div><dt>Output</dt><dd>Decision memo + challenge pack</dd></div>
              <div><dt>Publication gate</dt><dd>Strategy · Finance · Tech · Risk</dd></div>
            </dl>
            <div className="contract-scope">
              <span>RUN SHAPE</span>
              <div className="scope-flow"><i>Frame</i><b>→</b><i>5× research</i><b>→</b><i>Challenge</i><b>→</b><i>Brief</i></div>
            </div>
            <PrimaryButton type="submit" icon={submitting ? "refresh" : "play"} disabled={submitting}>{submitting ? "Commissioning…" : "Commission analysis"}</PrimaryButton>
            <p className="submit-note">The API reports run state. The interface never estimates progress or substitutes a simulated run.</p>
          </Panel>
        </aside>
      </form>
    </div>
  );
}

function FinanceRangeInput({
  label,
  range,
  onChange,
  max,
}: {
  label: string;
  range: FinanceRangeForm;
  onChange: (field: keyof FinanceRangeForm, value: string) => void;
  max?: string;
}) {
  return <fieldset className="finance-range"><legend>{label}</legend>{(["low", "base", "high"] as const).map((field) => <label key={field}><span>{field}</span><input type="number" min="0" max={max} step="0.01" value={range[field]} onChange={(event) => onChange(field, event.target.value)} required/></label>)}</fieldset>;
}

function Toggle({ checked, onChange, label, detail }: { checked: boolean; onChange: (next: boolean) => void; label: string; detail: string }) {
  return (
    <button type="button" className={`toggle-card ${checked ? "on" : ""}`} aria-pressed={checked} onClick={() => onChange(!checked)}>
      <span className="toggle-switch"><i /></span>
      <span><b>{label}</b><small>{detail}</small></span>
    </button>
  );
}

function RunMonitorView({
  run,
  analysis,
}: {
  run: RunState;
  analysis: AnalysisProjection;
}) {
  const phase = currentPhase(run.progress);
  const statusTone: Tone = run.status === "complete" ? "green" : run.status === "running" ? "cyan" : run.status === "illustrative" ? "violet" : "amber";
  const hasMatchingAnalysis = analysis.origin === "backend" && analysis.runId === run.id;
  const logs = hasMatchingAnalysis ? analysis.logs : [];
  const openFinding = hasMatchingAnalysis
    ? analysis.quality.findings.find((finding) => finding.severity === "error" || finding.severity === "warning")
    : undefined;
  return (
    <div className="view-stack">
      <ViewHeader
        code="03 / AGENT OPERATIONS"
        title="Ralph run"
        accent="monitor"
        description="Goal-directed analysis with bounded iteration, explicit gates, and a durable audit trail."
        actions={<StatusBadge tone={statusTone} dot>{run.status.replace("_", " ")}</StatusBadge>}
      />

      <div className="run-command panel">
        <div className="run-id"><span>RUN</span><b>{run.id}</b><small>{run.analysisId} · started {run.started} ET</small></div>
        <div className="goal-progress">
          <div className="goal-meta"><span>GOAL PROGRESS</span><b>{Math.round(run.progress)}%</b></div>
          <ScoreBar score={run.progress} tone={run.status === "blocked" || run.status === "failed" ? "amber" : "cyan"}/>
          <small>Backend phase · {run.current}{run.status === "running" ? ` · ${phase.label}` : ""}</small>
        </div>
        <div className="run-health"><span>GATE PASS RATE</span><b>{run.quality}<small>/100</small></b><em>{run.maxIterations ? `Attempt ${run.iteration}/${run.maxIterations}` : "No backend attempt"}</em></div>
      </div>

      {run.error && <div className="trust-banner danger"><Icon name="alert" size={15}/><div><b>Run failed</b><span>{run.error}</span></div></div>}

      <Panel className="ralph-map" eyebrow="EXECUTION GRAPH" title="Ralph’s bounded plan" actions={<span className="mono-note">{run.status === "running" ? "POLLING BACKEND · 4 SEC" : "PERSISTED STATUS"}</span>}>
        <div className="phase-track">
          {runPhases.map((item, index) => {
            const state = run.status === "illustrative" ? "queued" : run.progress >= item.end ? "complete" : run.progress >= item.start ? "active" : "queued";
            return (
              <div className={`phase-node ${state}`} key={item.id}>
                <div className="phase-orb">{state === "complete" ? <Icon name="check" size={15}/> : <span>{String(index + 1).padStart(2, "0")}</span>}</div>
                <div><b>{item.label}</b><small>{item.detail}</small></div>
                {index < runPhases.length - 1 && <span className="phase-link" />}
              </div>
            );
          })}
        </div>
        <div className="fanout-box">
          <div className="fanout-title"><span>FIVE-FORCE CELL</span><small>{hasMatchingAnalysis ? "hydrated from this run’s canonical assessments" : "workflow taxonomy · no result claims"}</small></div>
          <div className="agent-chips">{analysis.forces.map((force) => <span key={force.id}><i className={`tone-${force.tone}`}/>{force.shortName}{hasMatchingAnalysis && <b><Icon name="check" size={11}/></b>}</span>)}</div>
        </div>
      </Panel>

      <div className="run-grid">
        <Panel eyebrow="EVALUATOR RECORD" title="Persisted Ralph outcomes" actions={<span className="terminal-status"><i/> {run.status === "running" ? "RUNNING" : "SNAPSHOT"}</span>}>
          <div className="terminal" aria-live="polite">
            {logs.length === 0 && <div className="log-line"><time>—</time><span>[system]</span><p>No persisted evaluator events are available for this run yet.</p></div>}
            {logs.map((log, index) => (
              <div className="log-line" key={`${log.time}-${index}`}><time>{log.time}</time><span>[{log.agent}]</span><p>{log.text}</p></div>
            ))}
          </div>
        </Panel>
        <div className="telemetry-stack">
          <Panel eyebrow="RUN FACTS" title="Reported by the service">
            <div className="telemetry-grid">
              <div><span>Attempts</span><b>{run.iteration}</b><small>of {run.maxIterations || "—"}</small></div>
              <div><span>Force assessments</span><b>{hasMatchingAnalysis ? analysis.forces.length : "—"}</b><small>canonical outputs</small></div>
              <div><span>Evidence items</span><b>{hasMatchingAnalysis ? analysis.evidence.length : "—"}</b><small>ledger records</small></div>
              <div><span>Economics</span><b>{hasMatchingAnalysis ? analysis.scenarios.length : "—"}</b><small>deterministic scenarios</small></div>
              <div><span>Model</span><b className="small-value">{hasMatchingAnalysis ? analysis.model : "—"}</b><small>reported identifier</small></div>
              <div><span>Publication</span><b>{hasMatchingAnalysis ? analysis.quality.publishable ? "OPEN" : "LOCKED" : "—"}</b><small>quality report</small></div>
            </div>
          </Panel>
          <Panel className="constraint-panel" eyebrow="QUALITY SIGNAL" title={openFinding ? openFinding.code : hasMatchingAnalysis ? "No open machine finding" : "Result detail unavailable"}>
            <div className="constraint-icon"><Icon name="alert" size={18}/></div>
            <p>{openFinding?.message ?? (hasMatchingAnalysis ? "The latest quality report contains no warning or error finding." : "This run has no complete canonical analysis contract to project.")}</p>
            <div className="constraint-target"><span>Current status</span><b>{run.current}</b></div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function FiveForcesView({ analysis, selected, onSelect }: { analysis: AnalysisProjection; selected: string; onSelect: (id: string) => void }) {
  const active = analysis.forces.find((force) => force.id === selected) ?? analysis.forces[0];
  const pressure = Math.round(analysis.forces.reduce((sum, force) => sum + force.score, 0) / analysis.forces.length);
  return (
    <div className="view-stack">
      <ViewHeader code="04 / COMPETITIVE SYSTEM" title="Porter Five Forces" accent="assessment" description="Structural pressure translated into economic mechanisms, institutional exposure, and a board decision." actions={<StatusBadge tone="cyan">Mean pressure · {pressure}</StatusBadge>} />
      <div className="forces-layout">
        <Panel className="radar-panel" eyebrow="PRESSURE RADAR" title={analysis.marketBoundary}>
          <ForceRadar assessments={analysis.forces}/>
          <div className="radar-caption"><span>0 · benign</span><b>Pressure, not attractiveness</b><span>100 · severe</span></div>
        </Panel>
        <Panel className="force-ranking" eyebrow="FORCE RANKING" title="Where the structure is moving">
          {analysis.forces.slice().sort((a,b) => b.score - a.score).map((force, index) => (
            <button type="button" className={`rank-row ${selected === force.id ? "active" : ""}`} key={force.id} onClick={() => onSelect(force.id)}>
              <span>{String(index + 1).padStart(2,"0")}</span>
              <div><b>{force.name}</b><ScoreBar score={force.score} tone={force.tone} compact/></div>
              <strong className={`text-${force.tone}`}>{force.score}</strong>
              <small>{force.trend ?? "Assessed"}</small>
            </button>
          ))}
          <div className="method-note"><Icon name="alert" size={14}/><span>Scores are anchored ordinal assessments. They are not probabilities or forecasts.</span></div>
        </Panel>
      </div>

      <div className="force-tabs" role="tablist" aria-label="Force assessments">
        {analysis.forces.map((force) => <button type="button" role="tab" aria-selected={selected === force.id} className={selected === force.id ? "active" : ""} onClick={() => onSelect(force.id)} key={force.id}><span className={`tone-${force.tone}`}/>{force.shortName}<b>{force.score}</b></button>)}
      </div>

      <Panel className="force-detail">
        <div className="force-detail-head">
          <div><span className="panel-eyebrow">SELECTED FORCE</span><h2>{active.name}</h2><p>{active.thesis}</p></div>
          <div className={`force-score tone-${active.tone}`}><strong>{active.score}</strong><span>pressure</span><small>{active.confidence}% confidence</small></div>
        </div>
        <div className="mechanism-chain">
          <span className="chain-label">CAUSAL MECHANISM</span>
          <div>{active.mechanism.length ? active.mechanism.map((step, index) => <div key={step}><span>{String(index + 1).padStart(2,"0")}</span><b>{step}</b>{index < active.mechanism.length - 1 && <Icon name="arrow" size={15}/>}</div>) : <div><span>—</span><b>No mechanism steps were supplied.</b></div>}</div>
        </div>
        <div className="force-evidence-grid">
          <div className="evidence-case support"><span><Icon name="check" size={14}/> EVIDENCE FOR</span><p>{active.evidenceFor}</p></div>
          <div className="evidence-case counter"><span><Icon name="alert" size={14}/> COUNTEREVIDENCE</span><p>{active.evidenceAgainst}</p></div>
          <div className="evidence-case implication"><span><Icon name="spark" size={14}/> BOARD IMPLICATION</span><p>{active.implication}</p></div>
        </div>
      </Panel>

      <div className="gate-grid">
        <Panel eyebrow="LEADING INDICATORS" title="What to monitor"><ul className="quality-list">{(active.leadingIndicators?.length ? active.leadingIndicators : ["No leading indicators supplied."]).map((item) => <li key={item}><Icon name="pulse" size={13}/>{item}</li>)}</ul></Panel>
        <Panel eyebrow="REVERSAL CONDITIONS" title="What would change the conclusion"><ul className="quality-list">{(active.conditionsThatChange?.length ? active.conditionsThatChange : ["No reversal condition supplied."]).map((item) => <li key={item}><Icon name="alert" size={13}/>{item}</li>)}</ul></Panel>
      </div>
    </div>
  );
}

function EvidenceView({ analysis }: { analysis: AnalysisProjection }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All evidence");
  const filtered = useMemo(() => analysis.evidence.filter((item) => {
    const matchesQuery = `${item.title} ${item.publisher} ${item.force}`.toLowerCase().includes(query.toLowerCase());
    const matchesFilter = filter === "All evidence" || item.stance === filter || item.status === filter;
    return matchesQuery && matchesFilter;
  }), [analysis.evidence, query, filter]);
  const publishers = new Set(analysis.evidence.map((item) => item.publisher)).size;
  const counterevidence = analysis.evidence.filter((item) => item.stance === "Contradicts").length;
  const captured = analysis.evidence.filter((item) => item.status === "Captured").length;
  const qualityErrors = analysis.quality.findings.filter((item) => item.severity === "error").length;
  const utility = analysis.evidence.length
    ? Math.round(analysis.evidence.reduce((sum, item) => sum + item.authority, 0) / analysis.evidence.length)
    : 0;
  return (
    <div className="view-stack">
      <ViewHeader code="05 / SOURCE ROOM" title="Evidence" accent="ledger" description="Search discovers candidates. Only captured, attributable source content can support a board-visible fact." actions={analysis.artifactUrls.evidence_register ? <a className="button secondary" href={analysis.artifactUrls.evidence_register}><Icon name="download" size={15}/>Export ledger</a> : <StatusBadge>Export unavailable</StatusBadge>} />
      <div className="evidence-kpis">
        <div><span>Ledger items</span><b>{analysis.evidence.length}</b><small>canonical records</small></div>
        <div><span>Captured public sources</span><b className="cyan">{captured}</b><small>URL + content hash</small></div>
        <div><span>Independent publishers</span><b>{publishers}</b><small>named in ledger</small></div>
        <div><span>Counterevidence</span><b className="amber">{counterevidence}</b><small>explicitly linked</small></div>
        <div><span>Quality errors</span><b className={qualityErrors ? "amber" : "green"}>{qualityErrors}</b><small>current report</small></div>
      </div>
      <Panel className="evidence-ledger" eyebrow="CANONICAL REGISTER" title="Claim-level source lineage" actions={<StatusBadge tone={analysis.quality.draftValid ? "green" : "amber"}><Icon name="lock" size={11}/>{analysis.quality.draftValid ? "Draft gate valid" : "Gate review required"}</StatusBadge>}>
        <div className="table-toolbar">
          <label className="search-box"><Icon name="search" size={15}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search title, publisher or force…" aria-label="Search evidence"/></label>
          <div className="filter-chips">{["All evidence","Supports","Contradicts","Review"].map((item) => <button type="button" key={item} className={filter === item ? "active" : ""} onClick={() => setFilter(item)}>{item}</button>)}</div>
          <span className="result-count">{String(filtered.length).padStart(2,"0")} / {String(analysis.evidence.length).padStart(2,"0")}</span>
        </div>
        <div className="table-scroll">
          <table className="data-table evidence-table">
            <thead><tr><th>ID</th><th>Source / captured claim basis</th><th>Class</th><th>Force</th><th>Stance</th><th>Utility</th><th>Status</th><th><span className="sr-only">Open</span></th></tr></thead>
            <tbody>{filtered.map((item) => (
              <tr key={item.id}>
                <td className="mono-cell">{item.id}</td>
                <td>{item.url ? <a href={item.url} target="_blank" rel="noreferrer"><b>{item.title}</b><small>{item.publisher} · published {item.published} · captured {item.captured}</small></a> : <span><b>{item.title}</b><small>{item.publisher} · non-public basis · recorded {item.captured}</small></span>}</td>
                <td><StatusBadge>{item.sourceClass}</StatusBadge></td>
                <td>{item.force}</td>
                <td><span className={`stance ${item.stance.toLowerCase()}`}>{item.stance}</span></td>
                <td><div className="authority"><ScoreBar score={item.authority} tone={item.authority > 90 ? "green" : "cyan"} compact/><b>{item.authority}</b></div></td>
                <td><StatusBadge tone={item.status === "Captured" ? "green" : "amber"} dot>{item.status}</StatusBadge></td>
                <td>{item.url && <a href={item.url} target="_blank" rel="noreferrer" aria-label={`Open ${item.title}`} className="icon-link"><Icon name="external" size={14}/></a>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Panel>
      <div className="evidence-bottom-grid">
        <Panel eyebrow="PROVENANCE PIPELINE" title="Why a search result is not evidence">
          <div className="provenance-flow">
            <div><span>01</span><Icon name="search" size={18}/><b>Discover</b><small>DDG result</small></div><i>→</i>
            <div><span>02</span><Icon name="download" size={18}/><b>Capture</b><small>underlying page</small></div><i>→</i>
            <div><span>03</span><Icon name="shield" size={18}/><b>Seal</b><small>URL + content hash</small></div><i>→</i>
            <div><span>04</span><Icon name="sources" size={18}/><b>Link</b><small>claim relation</small></div>
          </div>
        </Panel>
        <Panel eyebrow="QUALITY SIGNAL" title="Evidence utility">
          <div className="utility-score"><strong>{utility}</strong><span>/100</span><small>mean deterministic utility</small></div>
          <ul className="quality-list">{analysis.quality.findings.length ? analysis.quality.findings.slice(0, 3).map((finding) => <li key={finding.code}><Icon name={finding.severity === "error" ? "alert" : "check"} size={13}/>{finding.code} · {finding.message}</li>) : <li><Icon name="check" size={13}/> No quality finding was reported.</li>}</ul>
        </Panel>
      </div>
    </div>
  );
}

function EconomicsView({ analysis }: { analysis: AnalysisProjection }) {
  const [scenarioId, setScenarioId] = useState(analysis.scenarios[0]?.id ?? "");
  const active = analysis.scenarios.find((scenario) => scenario.id === scenarioId) ?? analysis.scenarios[0];
  const delay = analysis.costOfDelay;
  return (
    <div className="view-stack">
      <ViewHeader code="06 / DECISION ECONOMICS" title="Scenario" accent="economics" description="Transparent deterministic ranges. Values are displayed in the exact units returned by the analysis contract." actions={<StatusBadge tone={analysis.origin === "backend" ? "cyan" : "amber"}>{analysis.origin === "backend" ? "Backend calculation" : "Illustrative · not a forecast"}</StatusBadge>} />
      {analysis.scenarios.length === 0 ? <Panel eyebrow="NO ECONOMIC INPUT" title="Scenario economics were not supplied"><p className="panel-copy">The completed analysis contains no deterministic scenario inputs. The interface will not invent investment, benefit, or payback values.</p></Panel> : <>
      <Panel className="scenario-compare" eyebrow="SCENARIOS" title="Ranges returned by the deterministic calculator">
        <div className="scenario-cards">
          {analysis.scenarios.map((scenario) => (
            <button type="button" className={`scenario-card tone-${scenario.tone} ${scenarioId === scenario.id ? "active" : ""}`} key={scenario.id} onClick={() => setScenarioId(scenario.id)}>
              <div><span>{scenario.currency}</span>{scenarioId === scenario.id && <Icon name="check" size={15}/>}</div>
              <h3>{scenario.name}</h3>
              <strong>{formatScenarioValue(scenario.npvLow, scenario)} <i>to</i> {formatScenarioValue(scenario.npvHigh, scenario)}</strong>
              <small>risk-adjusted NPV range</small>
              <dl><div><dt>Low</dt><dd>{formatScenarioValue(scenario.npvLow, scenario)}</dd></div><div><dt>Base</dt><dd>{formatScenarioValue(scenario.npvBase, scenario)}</dd></div><div><dt>Payback</dt><dd>{scenario.payback}</dd></div></dl>
            </button>
          ))}
        </div>
      </Panel>
      <div className="economics-grid">
        <Panel eyebrow="SELECTED SCENARIO" title={active?.name ?? "No scenario"} actions={<span className="mono-note">CONTRACT VALUES</span>}>
          {active && <><div className="sensitivity-result"><span>Base NPV</span><strong>{formatScenarioValue(active.npvBase, active)}</strong><small>Range · {formatScenarioValue(active.npvLow, active)} to {formatScenarioValue(active.npvHigh, active)}</small></div><dl className="runtime-list"><div><dt>Discounted payback</dt><dd>{active.payback}</dd></div><div><dt>Unit</dt><dd>{active.unitLabel}</dd></div><div><dt>Basis IDs</dt><dd>{active.basisIds.join(", ") || "None supplied"}</dd></div></dl>{active.warnings.map((warning) => <div className="policy-box" key={warning}><Icon name="alert" size={15}/><p>{warning}</p></div>)}</>}
        </Panel>
        <Panel eyebrow="NO-ACTION COMPARATOR" title="Cost of delay">
          {delay ? <><div className="sensitivity-result"><span>{delay.periodMonths}-month base</span><strong>{delay.currency} {delay.base}</strong><small>Range · {delay.currency} {delay.low} to {delay.currency} {delay.high}</small></div><dl className="runtime-list"><div><dt>Basis IDs</dt><dd>{delay.basisIds.join(", ") || "None supplied"}</dd></div></dl></> : <p className="panel-copy">No cost-of-delay calculation was supplied.</p>}
        </Panel>
      </div>
      <Panel className="scenario-table-panel" eyebrow="COMPARATOR TABLE" title="Board-visible economics">
        <div className="table-scroll"><table className="data-table scenario-table"><thead><tr><th>Scenario</th><th>Low NPV</th><th>Base NPV</th><th>High NPV</th><th>Payback</th><th>Basis</th><th>Warnings</th></tr></thead><tbody>{analysis.scenarios.map((scenario) => <tr key={scenario.id} className={scenario.id === active?.id ? "recommended-row" : ""}><td><b>{scenario.name}</b><small>{scenario.unitLabel}</small></td><td>{formatScenarioValue(scenario.npvLow, scenario)}</td><td className={scenario.npvBase < 0 ? "negative-text" : "positive-text"}>{formatScenarioValue(scenario.npvBase, scenario)}</td><td>{formatScenarioValue(scenario.npvHigh, scenario)}</td><td>{scenario.payback}</td><td>{scenario.basisIds.length}</td><td>{scenario.warnings.length}</td></tr>)}</tbody></table></div>
      </Panel>
      </>}
    </div>
  );
}

function BoardBriefView({ analysis }: { analysis: AnalysisProjection }) {
  const brief = analysis.boardBrief;
  const pending = analysis.approvals.filter((approval) => approval.status !== "Approved").length;
  const analogy = brief.analogy;
  return (
    <div className="view-stack">
      <ViewHeader code="07 / BOARD PRODUCT" title="Board decision" accent="brief" description="One decision, the economic logic, the strongest countercase, and the questions directors must resolve." actions={analysis.artifactUrls.board_memo ? <a className="button secondary" href={analysis.artifactUrls.board_memo}><Icon name="download" size={15}/>Export memo</a> : <StatusBadge>Memo unavailable</StatusBadge>} />
      <div className="brief-status-strip"><StatusBadge tone={analysis.quality.publishable ? "green" : "amber"} dot>{analysis.quality.publishable ? "Publication unlocked" : "Draft · publication locked"}</StatusBadge><span>Run · <b>{analysis.runId ?? "illustrative"}</b></span><span>As of · {new Date(brief.asOf).toLocaleDateString()}</span><span>{pending} approvals outstanding</span></div>
      <article className="board-paper">
        <header className="paper-header"><div><BrandMark/><span>PORTER FORCES AI</span></div><p>BOARD DECISION MEMORANDUM <b>CONFIDENTIAL</b></p></header>
        <div className="paper-title"><span>DECISION · {new Date(brief.asOf).toLocaleDateString()}</span><h2>{analysis.question}</h2><p>{analysis.organization} · {analysis.horizon} · {analysis.marketBoundary}</p></div>
        <section className="ask-box"><span>THE ASK</span><h3>{brief.decisionRequested}</h3><p>{brief.recommendation}</p></section>
        <div className="paper-columns">
          <section><span className="paper-label">WHY NOW</span><ul className="brief-bullets">{brief.whyNow.map((point) => <li key={point}>{point}</li>)}</ul></section>
          <section><span className="paper-label amber-label">IF WE DO NOTHING</span>{brief.noActionCase.map((point, index) => index === 0 ? <p className="lead-copy" key={point}>{point}</p> : <p key={point}>{point}</p>)}<div className="survival-line"><span>Survival probability</span><b>Not modeled</b><small>No probability exists in the analysis contract</small></div></section>
        </div>
        <section className="paper-recommendation"><span className="paper-label">RECOMMENDATION</span><div className="recommendation-grid"><div><b>Decision posture</b><p>{brief.recommendation}</p></div><div><b>Smallest sensible commitment</b><p>{brief.smallestCommitment}</p></div>{brief.uncertainties.slice(0, 2).map((point, index) => <div key={point}><b>Uncertainty {index + 1}</b><p>{point}</p></div>)}</div></section>
        <section className="countercase"><div><span className="paper-label">STRONGEST COUNTERARGUMENT</span><h3>{brief.dissentingView}</h3></div><p>{brief.uncertainties.join(" ")}</p></section>
        <footer className="paper-footer"><span>{analysis.evidence.length} canonical evidence items</span><span>{analysis.forces.length} force assessments</span><span>{pending} role approvals outstanding</span></footer>
      </article>

      <div className="brief-companion-grid">
        <Panel eyebrow="BOARDROOM TRANSLATION" title={analogy ? `Analogy: ${analogy.familiarMechanism}` : "No analogy supplied"}>
          <blockquote>{analogy ? `“${analogy.decisionImplication}”` : "The completed brief did not include an analogy."}</blockquote>
          {analogy && <><div className="analogy-map">{analogy.correspondences.map((item) => <div key={item}><span>Correspondence</span><b>{item}</b></div>)}</div><p className="panel-copy"><b>Where it breaks:</b> {analogy.whereItBreaks}</p></>}
        </Panel>
        <Panel eyebrow="DIRECTOR Q&A" title="Questions the brief must survive">
          <div className="qa-list">
            {brief.boardQuestions.map((question, index) => <details key={question} open={index === 0}><summary>{question}<Icon name="chevron" size={14}/></summary><p>This canonical brief records the question but does not claim a separate generated answer. Use the evidence-linked memo for the board response.</p></details>)}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ApprovalsView({
  analysis,
  approvals,
  act,
  busyRole,
  canApprove,
}: {
  analysis: AnalysisProjection;
  approvals: Approval[];
  act: (role: Approval["role"], status: Approval["status"]) => Promise<void>;
  busyRole: Approval["role"] | null;
  canApprove: boolean;
}) {
  const approved = approvals.filter((item) => item.status === "Approved").length;
  const fingerprint = approvals.find((item) => item.briefSha256)?.briefSha256;
  return (
    <div className="view-stack">
      <ViewHeader code="08 / GOVERNANCE GATE" title="Review &" accent="approvals" description="Approval is bound to the exact brief fingerprint. Any material edit invalidates every signature." actions={<StatusBadge tone={canApprove ? "cyan" : "slate"}>{canApprove ? "Review open" : "Review unavailable"}</StatusBadge>} />
      <div className="approval-banner panel">
        <div className="fingerprint-icon"><Icon name="lock" size={21}/></div>
        <div><span>CONTENT-BOUND FINGERPRINT</span><b>{fingerprint ? `sha256: ${fingerprint}` : "No approval fingerprint recorded"}</b><small>{analysis.runId ? `Board brief · ${analysis.runId}` : "Illustrative interface only"}</small></div>
        <div className="approval-meter"><span>{approved} / 4 APPROVED</span><div>{approvals.map((approval) => <i key={approval.role} className={approval.status === "Approved" ? "done" : approval.status === "Returned" ? "returned" : ""}/>)}</div><small>{analysis.quality.publishable ? "Publication unlocked" : "Publication locked"}</small></div>
      </div>
      <div className="approval-grid">
        {approvals.map((approval) => (
          <Panel key={approval.role} className={`approval-card status-${approval.status.toLowerCase()}`} eyebrow={`${approval.role.toUpperCase()} REVIEW`} title={approval.reviewer} actions={<StatusBadge tone={approval.status === "Approved" ? "green" : approval.status === "Returned" ? "red" : "amber"} dot>{approval.status}</StatusBadge>}>
            <div className="review-focus"><span>REVIEW FOCUS</span><p>{approval.role === "Strategy" ? "Decision framing · option completeness · strategic coherence" : approval.role === "Finance" ? "Assumption ownership · value realization · funding gates" : approval.role === "Technology" ? "Feasibility · portability · operating model" : "Risk appetite · controls · third-party concentration"}</p></div>
            <blockquote>{approval.note}</blockquote>
            <div className="approval-time"><Icon name="clock" size={13}/>{approval.time}</div>
            <div className="approval-actions">
              <button type="button" className="approve-button" disabled={!canApprove || busyRole !== null || approval.status === "Approved"} onClick={() => void act(approval.role, "Approved")}><Icon name="check" size={14}/>{busyRole === approval.role ? "Saving…" : "Approve revision"}</button>
              <button type="button" className="return-button" disabled={!canApprove || busyRole !== null || approval.status === "Returned"} onClick={() => void act(approval.role, "Returned")}><Icon name="alert" size={14}/>Reject revision</button>
            </div>
          </Panel>
        ))}
      </div>
      <div className="gate-grid">
        <Panel eyebrow="PUBLICATION GATE" title="Deterministic quality checks">
          <ul className="gate-list"><li><span className={analysis.quality.draftValid ? "" : "warn"}><Icon name={analysis.quality.draftValid ? "check" : "alert"} size={13}/></span><div><b>Draft integrity</b><small>Canonical machine-gate result</small></div><em className={analysis.quality.draftValid ? "" : "warn-text"}>{analysis.quality.draftValid ? "PASS" : "BLOCKED"}</em></li>{analysis.quality.findings.map((finding) => <li key={finding.code}><span className={finding.severity === "error" ? "warn" : ""}><Icon name={finding.severity === "error" ? "alert" : "check"} size={13}/></span><div><b>{finding.code}</b><small>{finding.message}</small></div><em className={finding.severity === "error" ? "warn-text" : ""}>{finding.severity.toUpperCase()}</em></li>)}</ul>
        </Panel>
        <Panel eyebrow="APPROVAL RECORD" title="Current role decisions">
          <div className="audit-list">{approvals.map((approval) => <div key={approval.role}><time>{approval.time}</time><span/><p><b>{approval.role} · {approval.status}</b><small>{approval.reviewer} · {approval.note}</small></p></div>)}</div>
        </Panel>
      </div>
    </div>
  );
}

function SettingsView({
  settings,
  setSettings,
  save,
  apiMode,
  saving,
}: {
  settings: SettingsState;
  setSettings: (next: SettingsState) => void;
  save: () => Promise<void>;
  apiMode: ApiMode;
  saving: boolean;
}) {
  const update = <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => setSettings({ ...settings, [key]: value });
  return (
    <div className="view-stack">
      <ViewHeader code="09 / SYSTEM CONTROL" title="Workspace" accent="settings" description="Session-scoped runtime fields accepted and enforced by the local API for subsequent analyses." actions={<PrimaryButton icon="check" onClick={() => void save()} disabled={saving || apiMode !== "connected"}>{saving ? "Applying…" : "Apply to session"}</PrimaryButton>} />
      <div className="settings-layout">
        <div className="settings-stack">
          <Panel eyebrow="LOCAL INFERENCE" title="oMLX model gateway" actions={<StatusBadge tone={apiMode === "connected" ? "green" : "amber"} dot>{apiMode === "connected" ? "Loaded from API" : "API unavailable"}</StatusBadge>}>
            <div className="form-grid two">
              <label className="field"><span>OpenAI-compatible base URL</span><input value={settings.endpoint} onChange={(event) => update("endpoint", event.target.value)}/><small>The API validates URL form and its local/remote confidentiality boundary.</small></label>
              <label className="field"><span>Model identifier</span><input value={settings.model} onChange={(event) => update("model", event.target.value)}/><small>Used by subsequent live analysis model calls.</small></label>
            </div>
          </Panel>
          <Panel eyebrow="PUBLIC RESEARCH" title="DuckDuckGo discovery">
            <div className="form-grid two"><label className="field"><span>Search region</span><select value={settings.searchRegion} onChange={(event) => update("searchRegion", event.target.value)}><option value="us-en">United States · English</option><option value="uk-en">United Kingdom · English</option><option value="de-de">Germany · German</option><option value="wt-wt">Global · no region</option></select></label><label className="field"><span>Unique capture-attempt limit</span><input type="number" min="5" max="50" value={settings.maxSources} onChange={(event) => update("maxSources", event.target.value)}/><small>Failures consume one of 5–50 per-run slots; uncovered forces receive bounded backfill.</small></label></div>
            <div className="policy-box"><Icon name="shield" size={17}/><div><b>Discovery is untrusted input</b><p>Search snippets are never promoted to evidence. The source page must be fetched, attributed, hashed and linked to a canonical claim.</p></div></div>
          </Panel>
        </div>
        <aside className="settings-rail">
          <Panel eyebrow="CONNECTION" title="Observed API state">
            <div className="connection-test"><div><span className={apiMode === "connected" ? "live-pip" : "status-dot"}/><b>{apiMode === "connected" ? "Local API responded" : apiMode === "checking" ? "Checking local API" : "Local API unavailable"}</b><small>{apiMode === "connected" ? "Settings were read from /api/settings" : "Edits cannot be applied until the service responds"}</small></div></div>
          </Panel>
          <Panel eyebrow="ACTIVE SESSION" title="API contract">
            <dl className="runtime-list"><div><dt>Endpoint</dt><dd>{settings.endpoint}</dd></div><div><dt>Model</dt><dd>{settings.model}</dd></div><div><dt>Search region</dt><dd>{settings.searchRegion}</dd></div><div><dt>Capture-attempt limit</dt><dd>{settings.maxSources}</dd></div></dl>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

function CanonicalResultUnavailable({ run, navigate }: { run: RunState; navigate: (id: ViewId) => void }) {
  return <div className="view-stack"><ViewHeader code="RESULT STATUS" title="Canonical analysis" accent="unavailable" description="This selected backend run does not expose a complete board-analysis contract. The interface will not replace it with fixture claims." actions={<SecondaryButton icon="pulse" onClick={() => navigate("run")}>Open run status</SecondaryButton>}/><Panel eyebrow="BACKEND RESPONSE" title={run.id}><dl className="runtime-list"><div><dt>Status</dt><dd>{run.status}</dd></div><div><dt>Phase</dt><dd>{run.current}</dd></div><div><dt>Progress</dt><dd>{run.progress}%</dd></div><div><dt>Error</dt><dd>{run.error ?? "No error detail reported"}</dd></div></dl></Panel></div>;
}

export function AdvisoryWorkspace() {
  const [activeView, setActiveView] = useState<ViewId>("overview");
  const [clock, setClock] = useState("14:32:08 ET");
  const [apiMode, setApiMode] = useState<ApiMode>("checking");
  const [run, setRun] = useState<RunState>(defaultRun);
  const [analysis, setAnalysis] = useState<AnalysisProjection>(illustrativeProjection);
  const [form, setForm] = useState(defaultForm);
  const [submitting, setSubmitting] = useState(false);
  const [selectedForce, setSelectedForce] = useState("suppliers");
  const [approvals, setApprovals] = useState<Approval[]>(illustrativeProjection.approvals);
  const [busyApprovalRole, setBusyApprovalRole] = useState<Approval["role"] | null>(null);
  const [settings, setSettings] = useState(defaultSettings);
  const [savingSettings, setSavingSettings] = useState(false);
  const [runHistory, setRunHistory] = useState<RunSummary[]>([]);
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const pendingApprovals = approvals.filter((approval) => approval.status !== "Approved").length;
  const toastIsError = /not |unavailable|failed|could not/i.test(toast);

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3200);
  };

  const hydrateRunPayload = useCallback((payload: unknown, updateRun = true) => {
    if (updateRun) setRun((previous) => remoteRunState(payload, previous) ?? previous);
    if (updateRun) {
      const root = apiRecord(payload);
      const details = apiRecord(root?.details);
      setApprovals(projectApprovals(details?.approvals));
    }
    const projected = projectCompletedAnalysis(payload);
    if (!projected) return false;
    setAnalysis(projected);
    setApprovals(projected.approvals);
    setSelectedForce((current) => projected.forces.some((force) => force.id === current) ? current : projected.forces[0].id);
    return true;
  }, []);

  useEffect(() => {
    const formatter = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZone: "America/New_York",
    });
    const update = () => setClock(`${formatter.format(new Date())} ET`);
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    async function loadWorkspace() {
      let activeRunId: string | null = null;
      let loadedCompletedAnalysis = false;
      let history: RunSummary[] = [];
      for (const endpoint of ["/api/workspace", "/api/dashboard"]) {
        try {
          const response = await fetch(endpoint, { signal: controller.signal, headers: { Accept: "application/json" } });
          if (!response.ok) continue;
          const payload: unknown = await response.json();
          if (!active) return;
          setApiMode("connected");
          const projectedRun = remoteRunState(payload, defaultRun);
          if (projectedRun) {
            setRun(projectedRun);
            activeRunId = projectedRun.id;
          }
          break;
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") return;
        }
      }
      if (activeRunId) {
        try {
          const response = await fetch(`/api/runs/${encodeURIComponent(activeRunId)}`, { signal: controller.signal, headers: { Accept: "application/json" } });
          if (response.ok) {
            const payload: unknown = await response.json();
            if (active) loadedCompletedAnalysis = hydrateRunPayload(payload);
          }
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") return;
        }
      }
      try {
        const response = await fetch("/api/runs?limit=50", { signal: controller.signal, headers: { Accept: "application/json" } });
        if (response.ok) {
          const payload = apiRecord(await response.json());
          const rows = Array.isArray(payload?.runs) ? payload.runs : [];
          history = rows.map(apiRecord).filter((item): item is Record<string, unknown> => item !== null).flatMap((item) => {
            const id = typeof item.run_id === "string" ? item.run_id : typeof item.id === "string" ? item.id : "";
            if (!id) return [];
            return [{
              id,
              status: typeof item.application_status === "string" ? item.application_status : typeof item.status === "string" ? item.status : "unknown",
              updatedAt: typeof item.updated_at === "string" ? item.updated_at : typeof item.completed_at === "string" ? item.completed_at : "",
            }];
          });
          if (active) setRunHistory(history);
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
      }
      if (!loadedCompletedAnalysis) {
        const completed = history.find((item) => ["achieved_draft", "human_required", "publishable", "blocked", "complete", "awaiting_approval"].includes(item.status));
        if (completed && completed.id !== activeRunId) {
          try {
            const response = await fetch(`/api/runs/${encodeURIComponent(completed.id)}`, { signal: controller.signal, headers: { Accept: "application/json" } });
            if (response.ok) {
              const payload: unknown = await response.json();
              if (active) hydrateRunPayload(payload, false);
            }
          } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") return;
          }
        }
      }
      try {
        const response = await fetch("/api/settings", { signal: controller.signal, headers: { Accept: "application/json" } });
        if (response.ok) {
          const payload = apiRecord(await response.json());
          if (active && payload) {
            setSettings((previous) => ({
              ...previous,
              endpoint: typeof payload.endpoint === "string" ? payload.endpoint : previous.endpoint,
              model: typeof payload.model === "string" ? payload.model : previous.model,
              searchRegion: typeof payload.searchRegion === "string" ? payload.searchRegion : previous.searchRegion,
              maxSources: typeof payload.maxSources === "string" || typeof payload.maxSources === "number" ? String(payload.maxSources) : previous.maxSources,
            }));
            setApiMode("connected");
          }
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
      }
      if (active) setApiMode((previous) => previous === "connected" ? previous : "offline");
    }
    void loadWorkspace();
    return () => { active = false; controller.abort(); };
  }, [hydrateRunPayload]);

  useEffect(() => {
    if (run.status !== "running") return;
    let active = true;
    const poll = async () => {
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(run.id)}`, { headers: { Accept: "application/json" } });
        if (response.ok) {
          const payload: unknown = await response.json();
          if (!active) return;
          hydrateRunPayload(payload);
          setApiMode("connected");
        }
      } catch {
        if (active) setApiMode("offline");
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 4000);
    return () => { active = false; window.clearInterval(timer); };
  }, [hydrateRunPayload, run.id, run.status]);

  const navigate = (id: ViewId) => {
    setActiveView(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const loadHistoryRun = async (runId: string) => {
    if (!runId) return;
    setLoadingRunId(runId);
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(await responseError(response));
      const payload: unknown = await response.json();
      hydrateRunPayload(payload);
      setApiMode("connected");
      notify(`Loaded ${runId}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "The run could not be loaded.";
      notify(`Run not loaded · ${message}`);
    } finally {
      setLoadingRunId(null);
    }
  };

  const submitAnalysis = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      const response = await fetch("/api/analyses", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          target: form.target,
          question: form.question,
          organization_type: form.organization,
          horizon: form.horizon,
          evidence_cutoff: form.evidenceCutoff,
          market_boundary: form.geography,
          internal_context: form.context,
          public_research_context: form.publicResearchContext,
          board_objection: form.boardObjection,
          research_policy: { web: form.webResearch },
          scenario_economics: form.finance.enabled ? [canonicalScenario(form.finance)] : [],
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const payload: unknown = await response.json();
      const projectedRun = remoteRunState(payload, defaultRun);
      if (!projectedRun || projectedRun.id === defaultRun.id) throw new Error("The API accepted the request but returned no run identifier.");
      setRun(projectedRun);
      setApprovals(projectApprovals(undefined));
      setRunHistory((previous) => [{ id: projectedRun.id, status: "running", updatedAt: new Date().toISOString() }, ...previous.filter((item) => item.id !== projectedRun.id)]);
      setApiMode("connected");
      navigate("run");
      notify(`Analysis ${projectedRun.analysisId} commissioned`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "The analysis request could not be submitted.";
      notify(`Analysis not commissioned · ${message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const actApproval = async (role: Approval["role"], status: Approval["status"]) => {
    if (!analysis.runId || analysis.origin !== "backend") {
      notify("Approval not saved · select a completed backend analysis first");
      return;
    }
    setBusyApprovalRole(role);
    const recordedReviewer = approvals.find((approval) => approval.role === role)?.reviewer;
    const reviewer = recordedReviewer && recordedReviewer !== "Unassigned" ? recordedReviewer : `${role} local reviewer`;
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(analysis.runId)}/approvals`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ role: role.toLowerCase(), reviewer, decision: status === "Approved" ? "approve" : "reject" }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const payload: unknown = await response.json();
      const projected = projectCompletedAnalysis(payload);
      if (!projected) throw new Error("The approval response did not contain a canonical completed analysis.");
      setAnalysis(projected);
      setApprovals(projected.approvals);
      if (run.id === projected.runId) setRun((previous) => remoteRunState(payload, previous) ?? previous);
      notify(`${role} decision persisted by the API`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "The approval could not be saved.";
      notify(`Approval not saved · ${message}`);
    } finally {
      setBusyApprovalRole(null);
    }
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          endpoint: settings.endpoint,
          model: settings.model,
          searchRegion: settings.searchRegion,
          maxSources: Number(settings.maxSources),
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const payload = apiRecord(await response.json());
      if (!payload) throw new Error("The API returned an invalid settings document.");
      setSettings({
        endpoint: typeof payload.endpoint === "string" ? payload.endpoint : settings.endpoint,
        model: typeof payload.model === "string" ? payload.model : settings.model,
        searchRegion: typeof payload.searchRegion === "string" ? payload.searchRegion : settings.searchRegion,
        maxSources: typeof payload.maxSources === "number" || typeof payload.maxSources === "string" ? String(payload.maxSources) : settings.maxSources,
      });
      notify("Configuration applied to this local API process");
    } catch (error) {
      const message = error instanceof Error ? error.message : "The configuration could not be applied.";
      notify(`Configuration not applied · ${message}`);
    } finally {
      setSavingSettings(false);
    }
  };

  const canonicalUnavailable = run.status !== "running"
    && run.status !== "illustrative"
    && (analysis.origin !== "backend" || analysis.runId !== run.id);

  const renderView = () => {
    if (canonicalUnavailable && ["overview", "forces", "evidence", "economics", "brief", "approvals"].includes(activeView)) {
      return <CanonicalResultUnavailable run={run} navigate={navigate}/>;
    }
    switch (activeView) {
      case "overview": return <OverviewView navigate={navigate} analysis={analysis} run={run}/>;
      case "new-analysis": return <NewAnalysisView form={form} setForm={setForm} onSubmit={submitAnalysis} submitting={submitting}/>;
      case "run": return <RunMonitorView run={run} analysis={analysis}/>;
      case "forces": return <FiveForcesView analysis={analysis} selected={selectedForce} onSelect={setSelectedForce}/>;
      case "evidence": return <EvidenceView analysis={analysis}/>;
      case "economics": return <EconomicsView analysis={analysis}/>;
      case "brief": return <BoardBriefView analysis={analysis}/>;
      case "approvals": return <ApprovalsView analysis={analysis} approvals={approvals} act={actApproval} busyRole={busyApprovalRole} canApprove={analysis.origin === "backend" && analysis.runId === run.id && analysis.applicationStatus === "human_required"}/>;
      case "settings": return <SettingsView settings={settings} setSettings={setSettings} save={saveSettings} apiMode={apiMode} saving={savingSettings}/>;
    }
  };

  return (
    <div className="console-shell">
      <div className="ambient-grid" aria-hidden="true" />
      <header className="topbar">
        <button className="brand-button" type="button" onClick={() => navigate("overview")} aria-label="Porter Forces AI overview">
          <BrandMark />
          <span className="brand-copy"><b>PORTER FORCES <em>AI</em></b><small>BOARD DECISION INTELLIGENCE</small></span>
        </button>
        <div className="top-context"><span>MANDATE</span><b>{analysis.organization}</b><small>{analysis.runId ?? "ILLUSTRATIVE"}</small></div>
        <div className="top-spacer" />
        <StatusBadge tone="cyan">{settings.model}</StatusBadge>
        <StatusBadge tone={apiMode === "connected" ? "green" : "amber"} dot>{apiMode === "connected" ? "API ONLINE" : apiMode === "checking" ? "CHECKING API" : "API OFFLINE"}</StatusBadge>
        <div className="top-clock"><Icon name="clock" size={14}/><span>{clock}</span></div>
        <button className="avatar" type="button" onClick={() => navigate("settings")} aria-label="Open settings">TA</button>
      </header>
      <div className="app-layout">
        <aside className="sidebar">
          {runHistory.length > 0 && <label className="run-history"><span>RUN HISTORY</span><select aria-label="Load persisted run" value={runHistory.some((item) => item.id === run.id) ? run.id : ""} onChange={(event) => void loadHistoryRun(event.target.value)} disabled={loadingRunId !== null}><option value="">Select a run…</option>{runHistory.map((item) => <option value={item.id} key={item.id}>{item.id} · {item.status}</option>)}</select></label>}
          <nav aria-label="Primary navigation">
            <span className="nav-section-label">WORKSPACE</span>
            {navItems.filter((item) => item.section === "workspace").map((item) => <NavButton key={item.id} item={item} active={activeView === item.id} onClick={() => navigate(item.id)} badge={item.id === "evidence" ? String(analysis.evidence.length) : item.id === "run" && run.status === "running" ? "LIVE" : item.badge}/>) }
            <span className="nav-section-label governance-label">GOVERNANCE</span>
            {navItems.filter((item) => item.section === "governance").map((item) => <NavButton key={item.id} item={item} active={activeView === item.id} onClick={() => navigate(item.id)} badge={item.id === "approvals" ? String(pendingApprovals) : item.badge}/>) }
          </nav>
          <div className="sidebar-status">
            <div className="mini-run"><div><span className={run.status === "running" ? "live-pip" : "status-dot"}/><b>{run.status === "running" ? "RALPH ACTIVE" : run.status.toUpperCase()}</b><small>{run.id}</small></div><strong>{Math.round(run.progress)}%</strong></div>
            <ScoreBar score={run.progress} tone="cyan" compact/>
            <small>{run.current}</small>
          </div>
          <footer><span>LANGGRAPH · DEEP AGENTS</span><span>oMLX LOCAL INFERENCE</span><span>CONTROLLED EGRESS</span><b>v0.1.0 · LOCAL MVP</b></footer>
        </aside>
        <main className="main-content" id="main-content">
          {analysis.origin === "illustrative" && <div className="trust-banner"><Icon name="alert" size={16}/><div><b>ILLUSTRATIVE WORKSPACE</b><span>No completed backend analysis is loaded. Every claim, source, score, approval, and financial value below is demonstration data.</span></div></div>}
          {analysis.origin === "backend" && analysis.runId !== run.id && <div className="trust-banner"><Icon name="clock" size={16}/><div><b>SHOWING LAST COMPLETED RUN · {analysis.runId}</b><span>The selected run {run.id} is {run.status}; no completed analysis contract is available for it yet.</span></div></div>}
          {renderView()}
        </main>
      </div>
      <div className={`toast ${toast ? "visible" : ""}${toastIsError ? " error" : ""}`} role="status" aria-live="polite"><Icon name={toastIsError ? "alert" : "check"} size={15}/>{toast}</div>
    </div>
  );
}

function NavButton({ item, active, onClick, badge }: { item: (typeof navItems)[number]; active: boolean; onClick: () => void; badge?: string }) {
  return (
    <button type="button" className={`nav-button ${active ? "active" : ""}`} onClick={onClick} aria-current={active ? "page" : undefined}>
      <Icon name={item.icon} size={16}/><span>{item.label}</span>{badge && <small className={badge === "LIVE" ? "live" : ""}>{badge}</small>}
    </button>
  );
}
