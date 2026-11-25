import fixture from "../public/fixtures/run.fixture.json";
import type { RunArtifact, ScenarioRun } from "../src/types";

export const fixtureArtifact = (): RunArtifact => structuredClone(fixture) as unknown as RunArtifact;

/** Recompute summary so trimmed artifacts stay self-consistent (the page verifies summary vs scenarios). */
import { deriveHeadline, deriveScoreboard, deriveT0 } from "../src/derive";

export function withScenarios(scenarios: ScenarioRun[]): RunArtifact {
  const a = fixtureArtifact();
  a.scenarios = structuredClone(scenarios);
  const board = deriveScoreboard(a);
  const h = deriveHeadline(a);
  const t0 = deriveT0(a);
  const summary = a.summary as unknown as {
    by_arm: Record<string, Record<string, unknown>>;
    regressions: Record<string, number>;
    t0_eval_passed: number;
    t0_eval_total: number;
  };
  for (const arm of ["A", "B", "C", "D"] as const) Object.assign(summary.by_arm[arm], board[arm]);
  Object.assign(summary.regressions, h);
  summary.t0_eval_passed = t0.passed;
  summary.t0_eval_total = t0.total;
  return a;
}
