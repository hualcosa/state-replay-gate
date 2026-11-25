// Regression tests for review round 1 findings 2, 3, 4, 6 and 7 (web scope).
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App, { Report } from "../src/App";
import { deriveControlVariance, deriveHeadline, deriveReadout, deriveScoreboard, nearestRank, verifySummary } from "../src/derive";
import { loadArtifact, validateArtifact } from "../src/load";
import type { ArmId, ArmRun, Classification, Disposition, LoadedArtifact, RunArtifact, ScenarioRun } from "../src/types";
import { fixtureArtifact, withScenarios } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const loaded = (artifact: RunArtifact, source: LoadedArtifact["source"] = "run"): LoadedArtifact => ({ artifact, source });
const ARMS = ["A", "B", "C", "D"] as const;

function setOutcome(run: ArmRun, classification: Classification, disposition: Disposition, harmMinor = 0) {
  run.outcome.classification = classification;
  run.outcome.disposition = disposition;
  run.outcome.harm = classification === "wrong_payment"
    ? { type: "overpayment", amount_minor: harmMinor, explanation: "test" } as ArmRun["outcome"]["harm"]
    : { type: "none", amount_minor: 0, explanation: "" } as ArmRun["outcome"]["harm"];
}

/** A model name for calls that stand in for a recorded provider session (not the scripted fixture). */
export const RECORDED_MODEL = "recorded-model";

/** Gives every LLM call a recorded latency and a non-scripted model so it looks like a replay of a real session. */
function withLatencies(scenarios: ScenarioRun[]): ScenarioRun[] {
  const s = structuredClone(scenarios);
  s.forEach((sc, i) => {
    sc.t0.llm.model = RECORDED_MODEL;
    ARMS.forEach((arm, j) => { const llm = sc.arms[arm].llm; if (llm) { llm.latency_ms = 1000 * (j + 1) + 100 * i; llm.model = RECORDED_MODEL; } });
  });
  return s;
}

describe("finding 2: recorded LLM latency", () => {
  it("nearest-rank matches the Python definition", () => {
    const xs = Array.from({ length: 20 }, (_, i) => 20 - i);
    expect(nearestRank(xs, 50)).toBe(10);
    expect(nearestRank(xs, 95)).toBe(19);
    expect(nearestRank([7], 95)).toBe(7);
    expect(nearestRank([], 50)).toBeNull();
  });

  it("is shown for a default replay (timings_included false) and recomputed from the calls", () => {
    const a = withScenarios(withLatencies(fixtureArtifact().scenarios));
    a.run.model = RECORDED_MODEL;
    expect(a.run.timings_included).toBe(false);
    const board = deriveScoreboard(a);
    expect(board.A.llm_latency_ms_p50).toBe(1200); // A latencies 1000..1400, nearest rank 50% → 3rd
    expect(board.A.llm_latency_ms_p95).toBe(1400);
    expect(board.D.llm_latency_ms_p50).toBeNull();
    render(<Report loaded={loaded(a)} />);
    const row = within(screen.getByRole("table", { name: /per-arm results/i })).getByRole("row", { name: /LLM latency/ });
    expect(row).toHaveTextContent("1,200 / 1,400 ms");
    expect(row).toHaveTextContent("n/a");
    expect(screen.queryByText(/Latency was not recorded/)).toBeNull();
    expect(screen.getByTestId("verify")).toHaveAttribute("data-ok", "true");
  });

  it("flags a tampered summary percentile instead of displaying it as verified", () => {
    const a = withScenarios(withLatencies(fixtureArtifact().scenarios));
    a.run.timings_included = true;
    (a.summary.by_arm.A as { llm_latency_ms_p50: number | null }).llm_latency_ms_p50 = 999999;
    expect(verifySummary(a).join(" ")).toMatch(/arm A llm_latency_ms_p50: summary 999999, recomputed 1200/);
    render(<Report loaded={loaded(a)} />);
    expect(screen.getByTestId("verify")).toHaveAttribute("data-ok", "false");
    expect(screen.queryByText(/999,999/)).toBeNull();
  });
});

describe("finding 3: regression split population", () => {
  it("an S09-style control flip alone renders no zero-denominator ratio and is labelled as control variance", () => {
    const control = fixtureArtifact().scenarios.find((s) => s.scenario.mutation.ops.length === 0)!;
    control.arms.A.decision_changed_vs_t0 = true;
    const a = withScenarios([control]);
    expect(deriveHeadline(a)).toEqual({ scenarios_with_state_change: 0, changed_on_live_state: 0, changed_on_frozen_state: 0 });
    expect(deriveControlVariance(a)).toEqual([{ scenario: control.scenario.id, arms: ["A"] }]);
    render(<Report loaded={loaded(a)} />);
    const headline = document.getElementById("headline")!;
    expect(headline).not.toHaveTextContent(/\/ 0\b/);
    expect(headline).not.toHaveTextContent(/reaction to the world, so/);
    expect(headline).toHaveTextContent("No scenario in this run changed state");
    expect(screen.getByTestId("control-variance")).toHaveTextContent(`${control.scenario.id} (arm A)`);
    expect(screen.getByTestId("control-variance")).toHaveTextContent(/sampling variance/);
  });

  it("keeps control flips out of the changed-state ratio when both exist", () => {
    const fx = fixtureArtifact();
    const control = fx.scenarios.find((s) => s.scenario.mutation.ops.length === 0)!;
    control.arms.C.decision_changed_vs_t0 = true;
    const a = withScenarios(fx.scenarios);
    const h = deriveHeadline(a);
    expect(h.changed_on_frozen_state).toBe(0);
    expect(h.scenarios_with_state_change).toBe(4);
    render(<Report loaded={loaded(a)} />);
    expect(screen.getByTestId("control-variance")).toHaveTextContent(`${control.scenario.id} (arm C)`);
  });
});

describe("finding 6: scoreboard readout", () => {
  it("prints each tied arm's own breakdown (S06+S10 subset shape)", () => {
    const fx = fixtureArtifact();
    const [s1, s2] = [fx.scenarios[0], fx.scenarios[2]];
    setOutcome(s1.arms.A, "correct_stop", "escalated");
    setOutcome(s1.arms.B, "false_stop", "denied");
    setOutcome(s1.arms.C, "correct_payment", "paid");
    setOutcome(s1.arms.D, "correct_stop", "held_for_review");
    for (const arm of ARMS) setOutcome(s2.arms[arm], "correct_stop", "denied");
    const a = withScenarios([s1, s2]);
    const ro = deriveReadout(a);
    expect(ro.mostCautious).toEqual([
      { arm: "A", falseStops: 0, reviewLoad: 1 },
      { arm: "B", falseStops: 1, reviewLoad: 0 },
      { arm: "D", falseStops: 0, reviewLoad: 1 },
    ]);
    render(<Report loaded={loaded(a)} />);
    const text = screen.getByTestId("readout-cautious").textContent!;
    expect(text).toMatch(/A \([^)]*\): 0 false stops and 1 sent to review/);
    expect(text).toMatch(/B \([^)]*\): 1 false stop and 0 sent to review/);
    expect(text).toMatch(/D \([^)]*\): 0 false stops and 1 sent to review/);
  });

  it("ranks fewest wrong payments by count and least money separately when they disagree", () => {
    const fx = fixtureArtifact();
    const sc = fx.scenarios.slice(0, 3);
    for (const s of sc) for (const arm of ARMS) setOutcome(s.arms[arm], "correct_stop", "denied");
    const pay = (arm: ArmId, n: number, each: number) => { for (let i = 0; i < n; i++) setOutcome(sc[i].arms[arm], "wrong_payment", "paid", each); };
    pay("A", 1, 50000); // 1 payment, $500
    pay("B", 2, 100); // 2 payments, $2
    pay("C", 3, 30000);
    pay("D", 3, 30000);
    const a = withScenarios(sc);
    const ro = deriveReadout(a);
    expect(ro.fewestWrong).toEqual({ arms: ["A"], count: 1 });
    expect(ro.leastMoney).toEqual({ arms: ["B"], minor: 200 });
    render(<Report loaded={loaded(a)} />);
    expect(screen.getByTestId("readout-fewest")).toHaveTextContent(/^Fewest wrong payments\s*A \([^)]*\): 1 wrong payment\.$/);
    expect(screen.getByTestId("readout-money")).toHaveTextContent(/^Least harm from wrong payments\s*B \([^)]*\): \$2\.00\.$/);
  });
});

describe("finding 7: token totals", () => {
  const setUsage = (a: RunArtifact, arm: ArmId, prompt: number | null, completion: number | null) =>
    a.scenarios.forEach((s) => { const l = s.arms[arm].llm!; l.prompt_tokens = prompt; l.completion_tokens = completion; });

  it("sums when every call reported usage and is null when any call did not", () => {
    const a = fixtureArtifact();
    setUsage(a, "A", 100, 10);
    expect(deriveScoreboard(a).A.prompt_tokens_at_t1).toBe(500);
    expect(deriveScoreboard(a).A.completion_tokens_at_t1).toBe(50);
    a.scenarios[2].arms.A.llm!.prompt_tokens = null;
    expect(deriveScoreboard(a).A.prompt_tokens_at_t1).toBeNull();
    expect(deriveScoreboard(a).A.completion_tokens_at_t1).toBeNull();
    expect(deriveScoreboard(a).D.prompt_tokens_at_t1).toBeNull(); // no calls at all
  });

  it("does not warn about a valid summary that reports null for mixed usage", () => {
    const a = fixtureArtifact();
    setUsage(a, "A", 100, 10);
    a.scenarios[2].arms.A.llm!.prompt_tokens = null;
    const b = withScenarios(a.scenarios);
    expect(b.summary.by_arm.A.prompt_tokens_at_t1).toBeNull();
    expect(verifySummary(b)).toEqual([]);
  });
});

describe("finding 4: robust loading", () => {
  const missingD = () => {
    const a = fixtureArtifact() as unknown as { scenarios: { arms: Record<string, unknown> }[] };
    delete a.scenarios[0].arms.D;
    return a;
  };

  it("rejects an artifact with a missing arm in a scenario or in summary.by_arm", () => {
    const v = validateArtifact(missingD());
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.errors.join(" ")).toMatch(/\/scenarios\/0\/arms (missing D|must have required property 'D')/);
    const b = fixtureArtifact() as unknown as { summary: { by_arm: Record<string, unknown> } };
    delete b.summary.by_arm.B;
    expect(validateArtifact(b).ok).toBe(false);
  });

  it("missing arms.D in data/run.json falls back to the fixture with a visible reason", async () => {
    const run = JSON.stringify(missingD());
    const fx = JSON.stringify(fixtureArtifact());
    const fetchFn = vi.fn(async (u: string) => new Response(String(u).includes("data/run.json") ? run : fx));
    const l = await loadArtifact(fetchFn as unknown as typeof fetch);
    expect(l.source).toBe("fixture-fallback");
    expect(l.reason).toBe("invalid");
    vi.stubGlobal("fetch", fetchFn);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("hero")).toBeInTheDocument());
    expect(screen.getByTestId("fixture-badge")).toHaveTextContent("data invalid, showing fixture");
  });

  it("a missing data file says so", async () => {
    const fx = JSON.stringify(fixtureArtifact());
    vi.stubGlobal("fetch", vi.fn(async (u: string) => (String(u).includes("data/run.json") ? new Response("", { status: 404 }) : new Response(fx))));
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("hero")).toBeInTheDocument());
    expect(screen.getByTestId("fixture-badge")).toHaveTextContent("no run data, showing fixture");
  });
});
