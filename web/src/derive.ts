import { formatMoney } from "./format";
import type { ArmId, ArmRun, RunArtifact, ScenarioRun, WorldState } from "./types";
import { ARM_IDS } from "./types";

export interface ArmScore {
  scenarios: number;
  correct_payments: number;
  wrong_payments: number;
  wrong_payment_minor: number;
  correct_stops: number;
  false_stops: number;
  review_load: number;
  decisions_changed_vs_t0: number;
  llm_calls_at_t1: number;
  prompt_tokens_at_t1: number | null;
  completion_tokens_at_t1: number | null;
  llm_latency_ms_p50: number | null;
  llm_latency_ms_p95: number | null;
}

export interface Headline {
  scenarios_with_state_change: number;
  changed_on_live_state: number;
  changed_on_frozen_state: number;
}

export type Scoreboard = Record<ArmId, ArmScore>;

type LlmRef = NonNullable<ArmRun["llm"]>;

/** Nearest-rank percentile, identical to Python `srg.outcome.nearest_rank`; null for no values. */
export function nearestRank(values: number[], pct: number): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.max(1, Math.ceil((pct / 100) * ordered.length)) - 1];
}

/** Same rule as Python: a total is null unless the arm made calls and every one reported both usages. */
function tokenTotals(calls: LlmRef[]): { prompt: number | null; completion: number | null } {
  const known = (v: unknown): v is number => typeof v === "number";
  const hasUsage = calls.length > 0 && calls.every((c) => known(c.prompt_tokens) && known(c.completion_tokens));
  if (!hasUsage) return { prompt: null, completion: null };
  return {
    prompt: calls.reduce((a, c) => a + (c.prompt_tokens as number), 0),
    completion: calls.reduce((a, c) => a + (c.completion_tokens as number), 0),
  };
}

export function deriveScoreboard(artifact: Pick<RunArtifact, "scenarios">): Scoreboard {
  const out = {} as Scoreboard;
  for (const arm of ARM_IDS) {
    const runs = artifact.scenarios.map((s) => s.arms[arm]);
    const count = (pred: (r: ArmRun) => boolean) => runs.filter(pred).length;
    const calls = runs.map((r) => r.llm).filter((c): c is LlmRef => c !== null && c !== undefined);
    const tokens = tokenTotals(calls);
    const latencies = calls.map((c) => c.latency_ms);
    out[arm] = {
      scenarios: runs.length,
      correct_payments: count((r) => r.outcome.classification === "correct_payment"),
      wrong_payments: count((r) => r.outcome.classification === "wrong_payment"),
      wrong_payment_minor: runs
        .filter((r) => r.outcome.classification === "wrong_payment")
        .reduce((a, r) => a + r.outcome.harm.amount_minor, 0),
      correct_stops: count((r) => r.outcome.classification === "correct_stop"),
      false_stops: count((r) => r.outcome.classification === "false_stop"),
      review_load: count(
        (r) => r.outcome.disposition === "escalated" || r.outcome.disposition === "held_for_review",
      ),
      decisions_changed_vs_t0: count((r) => r.decision_changed_vs_t0),
      llm_calls_at_t1: calls.length,
      prompt_tokens_at_t1: tokens.prompt,
      completion_tokens_at_t1: tokens.completion,
      llm_latency_ms_p50: nearestRank(latencies, 50),
      llm_latency_ms_p95: nearestRank(latencies, 95),
    };
  }
  return out;
}

/**
 * A scenario's world changed iff the net T0→T1 state diff is non-empty, matching Python. Patch ops
 * that leave the state as it was (e.g. replacing `captured` with `captured`) are an unchanged control.
 */
const hasStateChange = (s: ScenarioRun): boolean => s.state_diff.length > 0;

/** Both ratios use one population: scenarios whose net state changed between T0 and T1. */
export function deriveHeadline(artifact: Pick<RunArtifact, "scenarios">): Headline {
  const changed = artifact.scenarios.filter(hasStateChange);
  return {
    scenarios_with_state_change: changed.length,
    changed_on_live_state: changed.filter((x) => x.arms.A.decision_changed_vs_t0).length,
    changed_on_frozen_state: changed.filter((x) => x.arms.C.decision_changed_vs_t0).length,
  };
}

export interface ControlVariance {
  scenario: string;
  arms: ArmId[];
}

/** Scenarios with no state change where arm A or C still decided differently from T0: sampling variance. */
export function deriveControlVariance(artifact: Pick<RunArtifact, "scenarios">): ControlVariance[] {
  return artifact.scenarios
    .filter((s) => !hasStateChange(s))
    .map((s) => ({
      scenario: s.scenario.id,
      arms: (["A", "C"] as const).filter((a) => s.arms[a].decision_changed_vs_t0),
    }))
    .filter((v) => v.arms.length > 0);
}

export function deriveT0(artifact: Pick<RunArtifact, "scenarios">): { passed: number; total: number } {
  return {
    passed: artifact.scenarios.filter((s) => s.t0.eval.passed).length,
    total: artifact.scenarios.length,
  };
}

/** Compares recomputed figures with the artifact's own summary; returns human-readable mismatches. */
export function verifySummary(artifact: RunArtifact): string[] {
  const issues: string[] = [];
  const board = deriveScoreboard(artifact);
  const sb = artifact.summary.by_arm as unknown as Record<ArmId, Record<string, unknown>>;
  for (const arm of ARM_IDS) {
    for (const [key, value] of Object.entries(board[arm])) {
      if (sb[arm]?.[key] !== value) {
        issues.push(`arm ${arm} ${key}: summary ${String(sb[arm]?.[key])}, recomputed ${String(value)}`);
      }
    }
  }
  const h = deriveHeadline(artifact);
  const r = artifact.summary.regressions as unknown as Record<string, number>;
  for (const [key, value] of Object.entries(h)) {
    if (r[key] !== value) issues.push(`regressions ${key}: summary ${r[key]}, recomputed ${value}`);
  }
  const t0 = deriveT0(artifact);
  if (artifact.summary.t0_eval_passed !== t0.passed) issues.push("t0_eval_passed differs");
  if (artifact.summary.t0_eval_total !== t0.total) issues.push("t0_eval_total differs");
  return issues;
}

export function heroOf(scenarios: ScenarioRun[]): ScenarioRun | undefined {
  return scenarios.find((s) => s.scenario.hero) ?? scenarios[0];
}

export function honestCases(scenarios: ScenarioRun[]): ScenarioRun[] {
  return scenarios.filter(
    (s) => s.scenario.kind === "stale_denial" || s.scenario.kind === "gate_blind_spot",
  );
}

const GOOD = new Set(["correct_payment", "correct_stop"]);
export const isGood = (c: string): boolean => GOOD.has(c);

/** Scenarios where arm A ended well and arm D did not. Measured, never asserted. */
export function whereABeatsD(scenarios: ScenarioRun[]): ScenarioRun[] {
  return scenarios.filter(
    (s) => isGood(s.arms.A.outcome.classification) && !isGood(s.arms.D.outcome.classification),
  );
}

export interface AmountMismatch {
  paidMinor: number;
  paidCurrency: string;
  requestedMinor: number;
  requestedCurrency: string;
}

export const AMOUNT_MISMATCH_LABEL = "amount ≠ requested";

/**
 * An executed refund whose amount or currency differs from the ticket's request, computed from the
 * arm's ledger entry (`execution.refund`, what was actually paid) and the scenario's ticket. The §5.6
 * classification only asks whether the arm paid when it should have; this reports what it paid. In
 * arm D the gate rule `amount_matches_request` blocks it. Loading rejects a ledger that contradicts
 * the decision, so this never disagrees with the decided amount on a rendered page.
 */
export function amountMismatch(run: ArmRun, scenario: ScenarioRun["scenario"]): AmountMismatch | null {
  const paid = run.execution.executed ? run.execution.refund : null;
  if (!paid) return null;
  // The scenario's ticket as it is in the T0 snapshot (Python `Scenario.ticket`).
  const ticket = scenario.t0.state.tickets.find((t) => t.ticket_id === scenario.ticket_id);
  if (!ticket) return null;
  if (paid.amount_minor === ticket.requested_minor && paid.currency === ticket.currency) return null;
  return {
    paidMinor: paid.amount_minor,
    paidCurrency: paid.currency,
    requestedMinor: ticket.requested_minor,
    requestedCurrency: ticket.currency,
  };
}

export const SCRIPTED_MODEL = "fixture-scripted";

/**
 * True when latency figures cannot be recorded provider round trips: the run's model is the scripted
 * fixture, or any LLM call came from it. Such latencies are simulated and are not shown as measurements.
 */
export function isSimulatedLatency(artifact: Pick<RunArtifact, "run" | "scenarios">): boolean {
  if (artifact.run.model === SCRIPTED_MODEL) return true;
  return artifact.scenarios.some(
    (s) => s.t0.llm.model === SCRIPTED_MODEL || ARM_IDS.some((id) => s.arms[id].llm?.model === SCRIPTED_MODEL),
  );
}

export function isFixtureData(source: string, model: string): boolean {
  return source === "fixture-fallback" || model === "fixture-scripted";
}

export interface StateFact {
  key: string;
  label: string;
  value: string;
}

/** A handful of refund-relevant facts read straight from a world state, for the T0 vs T1 comparison. */
export function stateFacts(state: WorldState, orderCurrency: string): StateFact[] {
  const orderId = state.order.order_id;
  const refunded = state.refunds
    .filter((r) => r.order_id === orderId)
    .reduce((a, r) => a + r.amount_minor, 0);
  const openDisputes = state.disputes.filter((d) => d.status === "open");
  return [
    { key: "payment", label: "Payment", value: state.order.payment.status },
    { key: "currency", label: "Payment currency", value: state.order.payment.currency },
    { key: "return", label: "Return", value: state.return_info.status.replace(/_/g, " ") },
    { key: "fraud", label: "Fraud flag", value: state.customer.fraud_flag ? "flagged" : "clear" },
    {
      key: "disputes",
      label: "Open disputes",
      value: openDisputes.length === 0 ? "none" : `${openDisputes.length} (${openDisputes.map((d) => d.type).join(", ")})`,
    },
    { key: "refunded", label: "Already refunded", value: formatMoney(refunded, state.order.payment.currency || orderCurrency) },
    {
      key: "limit",
      label: "Auto-approve limit",
      value: `${formatMoney(state.policy.auto_approve_limit_minor, state.policy.currency)} (policy v${state.policy.version})`,
    },
    { key: "notes", label: "Free-text notes", value: String(state.notes.length) },
  ];
}

export interface ArmCaution {
  arm: ArmId;
  falseStops: number;
  reviewLoad: number;
}
export interface ArmTally {
  arm: ArmId;
  wrongPayments: number;
  llmCalls: number;
}

export interface Readout {
  /** Ranked by count of wrong payments only. */
  fewestWrong: { arms: ArmId[]; count: number };
  /** Ranked separately by money paid out wrongly. */
  leastMoney: { arms: ArmId[]; minor: number };
  /** Each tied arm carries its own breakdown. */
  mostCautious: ArmCaution[] | null;
  noLlmAtT1: ArmId[];
  liveReaders: ArmTally[];
  gated: ArmTally[];
}

/** Plain-language comparisons computed from the data; nothing about who "wins" is assumed. */
export function deriveReadout(artifact: RunArtifact): Readout {
  const board = deriveScoreboard(artifact);
  const ids = ARM_IDS.filter((id) => artifact.arms.some((a) => a.id === id));
  const minCount = Math.min(...ids.map((i) => board[i].wrong_payments));
  const minMinor = Math.min(...ids.map((i) => board[i].wrong_payment_minor));
  const caution = (i: ArmId) => board[i].false_stops + board[i].review_load;
  const maxCaution = Math.max(...ids.map(caution));
  const cautious = ids.filter((i) => caution(i) === maxCaution);
  const specs = (pred: (a: RunArtifact["arms"][number]) => boolean) =>
    artifact.arms.filter(pred).map((a) => a.id as ArmId).filter((id) => ids.includes(id));
  const tally = (arms: ArmId[]): ArmTally[] =>
    arms.map((arm) => ({ arm, wrongPayments: board[arm].wrong_payments, llmCalls: board[arm].llm_calls_at_t1 }));
  return {
    fewestWrong: { arms: ids.filter((i) => board[i].wrong_payments === minCount), count: minCount },
    leastMoney: { arms: ids.filter((i) => board[i].wrong_payment_minor === minMinor), minor: minMinor },
    mostCautious:
      maxCaution > 0 && cautious.length < ids.length
        ? cautious.map((arm) => ({ arm, falseStops: board[arm].false_stops, reviewLoad: board[arm].review_load }))
        : null,
    noLlmAtT1: ids.filter((i) => board[i].llm_calls_at_t1 === 0),
    liveReaders: tally(specs((a) => a.t1_context === "live_state")),
    gated: tally(specs((a) => a.has_gate)),
  };
}
