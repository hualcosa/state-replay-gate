import Ajv2020 from "ajv/dist/2020";
import schema from "./generated/run-artifact.schema.json";
import type { FallbackReason, LoadedArtifact, RunArtifact } from "./types";
import { ARM_IDS } from "./types";

const ajv = new Ajv2020({ allErrors: true, strict: false });
const validate = ajv.compile(schema);

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

const CURRENCY_RE = /^[A-Z]{3}$/;

/**
 * §5.3's action-dependent rule, mirroring Python `AgentDecision._check`: issue_refund carries an
 * integer amount_minor >= 1 and a 3-letter currency; every other action carries both as null.
 */
function decisionErrors(d: unknown, path: string): string[] {
  if (!isObject(d)) return [`${path} is not an object`];
  const { action, amount_minor: amount, currency } = d;
  if (action === "issue_refund") {
    const okAmount = typeof amount === "number" && Number.isInteger(amount) && amount >= 1;
    const okCurrency = typeof currency === "string" && CURRENCY_RE.test(currency);
    return okAmount && okCurrency ? [] : [`${path} issue_refund requires integer amount_minor >= 1 and a 3-letter currency`];
  }
  return amount === null && currency === null ? [] : [`${path} ${String(action)} must have amount_minor and currency set to null`];
}

/** Same rule as Python `arms.py`: the action matches, and for a refund the amount matches too. */
function t0Passes(decision: Record<string, unknown>, action: unknown, amount: unknown): boolean {
  return decision.action === action && (action !== "issue_refund" || decision.amount_minor === amount);
}

/**
 * Mirrors Python `Scenario._check` and extends it to T1: the scenario's ticket_id names a ticket in
 * the T0 snapshot and in the T1 state. The page never substitutes another ticket.
 */
function ticketErrors(s: Record<string, unknown>, at: string): string[] {
  const sc = isObject(s.scenario) ? s.scenario : {};
  const id = sc.ticket_id;
  const has = (state: unknown) =>
    isObject(state) && Array.isArray(state.tickets) && state.tickets.some((t) => isObject(t) && t.ticket_id === id);
  const errors: string[] = [];
  if (!has(isObject(sc.t0) ? sc.t0.state : undefined)) errors.push(`${at}/scenario/ticket_id ${String(id)} not found in t0.state.tickets`);
  if (!has(s.t1_state)) errors.push(`${at}/scenario/ticket_id ${String(id)} not found in t1_state.tickets`);
  return errors;
}

/**
 * The ledger agrees with the decision: `executed` is true exactly when a refund was recorded and the
 * disposition is "paid", and an executed refund is an issue_refund paid at the decided amount and
 * currency. A contradiction means the artifact cannot say what was paid, so it is not rendered.
 */
function ledgerErrors(run: Record<string, unknown>, at: string): string[] {
  const ex = isObject(run.execution) ? run.execution : {};
  const refund = isObject(ex.refund) ? ex.refund : null;
  const d = isObject(run.decision) ? run.decision : {};
  const disposition = isObject(run.outcome) ? run.outcome.disposition : undefined;
  const errors: string[] = [];
  if (ex.executed === true && !refund) errors.push(`${at}/execution executed=true without a refund`);
  if (ex.executed !== true && refund) errors.push(`${at}/execution has a refund but executed=${String(ex.executed)}`);
  if ((ex.executed === true) !== (disposition === "paid")) {
    errors.push(`${at} executed=${String(ex.executed)} contradicts disposition ${String(disposition)}`);
  }
  if (refund) {
    if (d.action !== "issue_refund") errors.push(`${at}/execution refunded but the decision is ${String(d.action)}`);
    else if (refund.amount_minor !== d.amount_minor || refund.currency !== d.currency) {
      errors.push(`${at}/execution refund ${String(refund.amount_minor)} ${String(refund.currency)} contradicts the decision ${String(d.amount_minor)} ${String(d.currency)}`);
    }
  }
  return errors;
}

/**
 * Contract checks the page relies on beyond the JSON Schema: every arm map holds all four arms,
 * every decision obeys §5.3, the ticket reference resolves, the ledger agrees with the decision, the
 * stored T0 eval agrees with the scenario and the T0 decision, and the reporting currency is the currency every executed refund (and so every harm) was paid in.
 */
function contractErrors(data: Record<string, unknown>): string[] {
  const errors: string[] = [];
  const runCurrency = isObject(data.run) ? data.run.currency : undefined;
  const scenarios = Array.isArray(data.scenarios) ? data.scenarios : [];
  scenarios.forEach((s, i) => {
    const at = `/scenarios/${i}`;
    const arms = isObject(s) && isObject(s.arms) ? s.arms : {};
    const missing = ARM_IDS.filter((id) => !isObject(arms[id]));
    if (missing.length > 0) errors.push(`${at}/arms missing ${missing.join(", ")}`);
    if (!isObject(s)) return;

    const t0 = isObject(s.t0) ? s.t0 : {};
    errors.push(...decisionErrors(t0.decision, `${at}/t0/decision`));
    const expected = isObject(s.scenario) && isObject(s.scenario.expected) ? s.scenario.expected : {};
    const ev = isObject(t0.eval) ? t0.eval : {};
    if (ev.expected_action !== expected.t0_action || ev.expected_amount_minor !== expected.t0_amount_minor) {
      errors.push(`${at}/t0/eval expected ${String(ev.expected_action)} ${String(ev.expected_amount_minor)} differs from scenario.expected ${String(expected.t0_action)} ${String(expected.t0_amount_minor)}`);
    }
    if (isObject(t0.decision) && ev.passed !== t0Passes(t0.decision, expected.t0_action, expected.t0_amount_minor)) {
      errors.push(`${at}/t0/eval passed=${String(ev.passed)} contradicts the T0 decision`);
    }

    errors.push(...ticketErrors(s, at));

    for (const id of ARM_IDS) {
      const run = arms[id];
      if (!isObject(run)) continue;
      errors.push(...decisionErrors(run.decision, `${at}/arms/${id}/decision`));
      errors.push(...ledgerErrors(run, `${at}/arms/${id}`));
      const refund = isObject(run.execution) && isObject(run.execution.refund) ? run.execution.refund : null;
      if (refund && refund.currency !== runCurrency) {
        errors.push(`${at}/arms/${id} paid in ${String(refund.currency)} but run.currency is ${String(runCurrency)}`);
      }
      const harm = isObject(run.outcome) && isObject(run.outcome.harm) ? run.outcome.harm.amount_minor : 0;
      if (typeof harm === "number" && harm > 0 && !refund) {
        errors.push(`${at}/arms/${id} reports harm without an executed refund, so its currency is unknown`);
      }
    }
  });
  const summary = isObject(data.summary) ? data.summary : {};
  const byArm = isObject(summary.by_arm) ? summary.by_arm : {};
  const missing = ARM_IDS.filter((id) => !isObject(byArm[id]));
  if (missing.length > 0) errors.push(`/summary/by_arm missing ${missing.join(", ")}`);
  return errors;
}

export function validateArtifact(data: unknown): { ok: true; artifact: RunArtifact } | { ok: false; errors: string[] } {
  if (!validate(data)) {
    const errors = (validate.errors ?? []).slice(0, 8).map((e) => `${e.instancePath || "/"} ${e.message ?? "invalid"}`);
    return { ok: false, errors };
  }
  const errors = contractErrors(data as Record<string, unknown>);
  if (errors.length > 0) return { ok: false, errors: errors.slice(0, 8) };
  return { ok: true, artifact: data as unknown as RunArtifact };
}

async function fetchJson(url: string, fetchFn: typeof fetch): Promise<unknown> {
  const res = await fetchFn(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  // A dev/preview server may answer a missing file with index.html; JSON.parse then throws.
  return JSON.parse(await res.text());
}

export class LoadError extends Error {
  details: string[];
  constructor(message: string, details: string[]) {
    super(message);
    this.details = details;
  }
}

export const DEFAULT_URLS = { run: "./data/run.json", fixture: "./fixtures/run.fixture.json" };

class NotFound extends Error {}

/** Loads the built-in fixture. Throws LoadError if it is unusable. */
export async function loadFixture(
  reason: FallbackReason,
  fetchFn: typeof fetch = (...a) => fetch(...a),
  url = DEFAULT_URLS.fixture,
  notes: string[] = [],
): Promise<LoadedArtifact> {
  try {
    const v = validateArtifact(await fetchJson(url, fetchFn));
    if (v.ok) return { artifact: v.artifact, source: "fixture-fallback", reason, notes };
    notes.push(`${url} failed validation: ${v.errors.join("; ")}`);
  } catch (e) {
    notes.push(`${url}: ${e instanceof Error ? e.message : String(e)}`);
  }
  throw new LoadError("No valid run artifact could be loaded.", notes);
}

/**
 * Loads ./data/run.json; when it is missing or invalid falls back to the fixture and records why.
 * Throws LoadError if neither is usable.
 */
export async function loadArtifact(
  fetchFn: typeof fetch = (...a) => fetch(...a),
  urls = DEFAULT_URLS,
): Promise<LoadedArtifact> {
  const notes: string[] = [];
  let reason: FallbackReason = "invalid";
  try {
    const res = await fetchFn(urls.run, { cache: "no-store" });
    if (res.status === 404) throw new NotFound(`${urls.run}: HTTP 404`);
    if (!res.ok) throw new Error(`${urls.run}: HTTP ${res.status}`);
    const text = await res.text();
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      // A dev/preview server may answer a missing file with index.html.
      if (/^\s*</.test(text)) throw new NotFound(`${urls.run}: not found (server returned HTML)`);
      throw new Error(`${urls.run}: not valid JSON`);
    }
    const v = validateArtifact(data);
    if (v.ok) return { artifact: v.artifact, source: "run", notes: [] };
    notes.push(`${urls.run} failed validation: ${v.errors.join("; ")}`);
  } catch (e) {
    if (e instanceof NotFound) reason = "missing";
    else if (e instanceof TypeError) reason = "unreachable";
    notes.push(e instanceof Error ? e.message : String(e));
  }
  return loadFixture(reason, fetchFn, urls.fixture, notes);
}
