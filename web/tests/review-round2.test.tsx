// Regression tests for review round 2 findings 2, 3, 6 and 7 (web scope).
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App, { Report } from "../src/App";
import { deriveControlVariance, deriveHeadline, isSimulatedLatency } from "../src/derive";
import { loadArtifact, validateArtifact } from "../src/load";
import type { LoadedArtifact, RunArtifact } from "../src/types";
import { fixtureArtifact, withScenarios } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

// The real replay artifact (gitignored) is used when present, so the review's exact specimens run.
const realRun = Object.values(import.meta.glob("../public/data/run.json", { eager: true, import: "default" }))[0] as RunArtifact | undefined;

/** Serves `run` as data/run.json and the committed fixture otherwise. */
const serve = (run: unknown) => {
  const fx = JSON.stringify(fixtureArtifact());
  return vi.fn(async (u: string) => new Response(String(u).includes("data/run.json") ? JSON.stringify(run) : fx));
};

async function expectFallback(run: unknown, error: RegExp) {
  const v = validateArtifact(run);
  expect(v.ok).toBe(false);
  if (!v.ok) expect(v.errors.join(" ")).toMatch(error);
  const fetchFn = serve(run);
  const l = await loadArtifact(fetchFn as unknown as typeof fetch);
  expect(l.source).toBe("fixture-fallback");
  expect(l.reason).toBe("invalid");
  vi.stubGlobal("fetch", fetchFn);
  render(<App />);
  await waitFor(() => expect(screen.getByTestId("fixture-badge")).toHaveTextContent("data invalid, showing fixture"));
}

const scenarioOf = (a: RunArtifact, id: string) => a.scenarios.find((s) => s.scenario.id === id)!;

describe("finding 2: §5.3 action-dependent amount and currency, enforced before rendering", () => {
  it("rejects issue_refund with a null amount (fixture F01/C)", async () => {
    const a = fixtureArtifact();
    scenarioOf(a, "F01").arms.C.decision.amount_minor = null;
    await expectFallback(a, /scenarios\/0\/arms\/C\/decision( issue_refund requires|\/amount_minor must be integer)/);
  });

  it.runIf(realRun)("rejects the review specimen: S01/C amount_minor null", async () => {
    const a = structuredClone(realRun!);
    expect(validateArtifact(a).ok).toBe(true);
    const c = scenarioOf(a, "S01").arms.C.decision;
    expect(c.action).toBe("issue_refund");
    c.amount_minor = null;
    await expectFallback(a, /arms\/C\/decision( issue_refund requires integer amount_minor >= 1 and a 3-letter currency|\/amount_minor must be integer)/);
  });

  it("rejects a non-refund decision that carries an amount, a zero amount and a bad currency", () => {
    const deny = fixtureArtifact();
    const f04 = scenarioOf(deny, "F04").arms.C.decision;
    f04.amount_minor = 100;
    f04.currency = "USD";
    expect(validateArtifact(deny).ok).toBe(false);
    const zero = fixtureArtifact();
    scenarioOf(zero, "F03").arms.A.decision.amount_minor = 0;
    expect(validateArtifact(zero).ok).toBe(false);
    const cur = fixtureArtifact();
    scenarioOf(cur, "F03").t0.decision.currency = "usd";
    expect(validateArtifact(cur).ok).toBe(false);
  });

  it("accepts the committed fixture and the real artifact unchanged", () => {
    expect(validateArtifact(fixtureArtifact()).ok).toBe(true);
    if (realRun) expect(validateArtifact(structuredClone(realRun)).ok).toBe(true);
  });
});

describe("finding 3: contradictions the success message used to ignore", () => {
  it("(a) run.currency JPY on a USD run falls back instead of formatting harm in yen", async () => {
    const a = structuredClone(realRun ?? fixtureArtifact());
    a.run.currency = "JPY";
    await expectFallback(a, /paid in USD but run\.currency is JPY/);
    expect(screen.queryByText(/¥/)).toBeNull();
  });

  it.runIf(realRun)("(b) S01 t0.eval.expected_action changed to deny_refund falls back", async () => {
    const a = structuredClone(realRun!);
    scenarioOf(a, "S01").t0.eval.expected_action = "deny_refund";
    await expectFallback(a, /t0\/eval expected deny_refund .* differs from scenario\.expected issue_refund/);
  });

  it("(b) fixture: expected_action, expected amount or passed flag that contradict the run fall back", async () => {
    const action = fixtureArtifact();
    scenarioOf(action, "F01").t0.eval.expected_action = "deny_refund";
    expect(validateArtifact(action).ok).toBe(false);
    const amount = fixtureArtifact();
    scenarioOf(amount, "F01").t0.eval.expected_amount_minor = 24999;
    expect(validateArtifact(amount).ok).toBe(false);
    const passed = fixtureArtifact();
    scenarioOf(passed, "F01").t0.eval.passed = false;
    await expectFallback(passed, /t0\/eval passed=false contradicts the T0 decision/);
  });

  it("(c) the success line names exactly what the page checked", () => {
    render(<Report loaded={{ artifact: fixtureArtifact(), source: "run" } as LoadedArtifact} />);
    const v = screen.getByTestId("verify");
    expect(v).toHaveAttribute("data-ok", "true");
    expect(v).toHaveTextContent(/scoreboard, the headline split and the T0 eval count were recomputed/);
    expect(v).toHaveTextContent(/does not check provenance/);
    expect(v).not.toHaveTextContent(/Every figure/);
  });
});

describe("finding 6: changed world means a non-empty net state diff", () => {
  it("a scenario with patch ops but an empty state_diff counts as an unchanged control", () => {
    const scenarios = fixtureArtifact().scenarios;
    const f03 = scenarios.find((s) => s.scenario.id === "F03")!;
    expect(f03.state_diff).toHaveLength(0);
    // A benign no-op patch: the op exists, the net state did not change.
    f03.scenario.mutation.ops = [{ op: "replace", path: "/order/payment/status", value: "captured" }] as unknown as typeof f03.scenario.mutation.ops;
    const before = deriveHeadline(fixtureArtifact());
    const a = withScenarios(scenarios);
    expect(deriveHeadline(a).scenarios_with_state_change).toBe(before.scenarios_with_state_change);
    // If arm A then flips on it, that is control variance, not a decision changed with live state.
    const flipped = structuredClone(scenarios);
    flipped.find((s) => s.scenario.id === "F03")!.arms.A.decision_changed_vs_t0 = true;
    const b = withScenarios(flipped);
    expect(deriveHeadline(b).changed_on_live_state).toBe(before.changed_on_live_state);
    expect(deriveControlVariance(b)).toEqual([{ scenario: "F03", arms: ["A"] }]);
  });
});

describe("finding 7: honest labels", () => {
  it("the money row is harm, with a note, and no copy calls it a payment amount", () => {
    render(<Report loaded={{ artifact: fixtureArtifact(), source: "run" } as LoadedArtifact} />);
    const table = screen.getByRole("table", { name: /per-arm results/i });
    expect(within(table).getByRole("row", { name: /Harm from wrong payments/ })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: /harm from wrong payments per arm/i })).toBeInTheDocument();
    expect(screen.getByTestId("harm-note")).toHaveTextContent(/not the amount paid/);
    expect(document.body).not.toHaveTextContent(/wrong payment amount/i);
    expect(document.body).not.toHaveTextContent(/paid out wrongly/i);
  });

  it("scripted-fixture latency reads simulated, never a recorded round trip", () => {
    const a = fixtureArtifact();
    expect(isSimulatedLatency(a)).toBe(true);
    render(<Report loaded={{ artifact: a, source: "run" } as LoadedArtifact} />);
    const row = within(screen.getByRole("table", { name: /per-arm results/i })).getByRole("row", { name: /LLM latency/ });
    expect(row).toHaveTextContent("simulated");
    expect(row).not.toHaveTextContent(/\d+ \/ \d+ ms/);
    expect(document.body).toHaveTextContent(/latency is simulated/);
    expect(document.body).not.toHaveTextContent(/recorded provider round trip/);
  });

  it("a scripted call inside an otherwise real-looking run still counts as simulated", () => {
    const a = fixtureArtifact();
    a.run.model = "recorded-model";
    for (const s of a.scenarios) {
      s.t0.llm.model = "recorded-model";
      for (const id of ["A", "B", "C"] as const) if (s.arms[id].llm) s.arms[id].llm!.model = "recorded-model";
    }
    expect(isSimulatedLatency(a)).toBe(false);
    a.scenarios[0].arms.B.llm!.model = "fixture-scripted";
    expect(isSimulatedLatency(a)).toBe(true);
  });

  it.runIf(realRun)("the real replay shows recorded latency in ms", () => {
    const a = structuredClone(realRun!);
    expect(isSimulatedLatency(a)).toBe(false);
    render(<Report loaded={{ artifact: a, source: "run" }} />);
    const row = within(screen.getByRole("table", { name: /per-arm results/i })).getByRole("row", { name: /LLM latency/ });
    expect(row).toHaveTextContent(/ ms/);
    expect(row).not.toHaveTextContent("simulated");
  });
});
