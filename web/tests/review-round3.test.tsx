// Regression tests for review round 3 findings 3, 5, 7 and 9 (web scope).
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App, { Report } from "../src/App";
import { amountMismatch } from "../src/derive";
import { loadArtifact, validateArtifact } from "../src/load";
import type { RunArtifact } from "../src/types";
import { fixtureArtifact, withScenarios } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

// The real replay artifact (gitignored) is used when present, so the review's exact specimens run.
const realRun = Object.values(import.meta.glob("../public/data/run.json", { eager: true, import: "default" }))[0] as RunArtifact | undefined;

const serve = (run: unknown) => {
  const fx = JSON.stringify(fixtureArtifact());
  return vi.fn(async (u: string) => new Response(String(u).includes("data/run.json") ? JSON.stringify(run) : fx));
};

/** The artifact is rejected before rendering and the page shows the visible invalid-data fallback. */
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
  expect(document.body).not.toHaveTextContent(/\$0\.01 paid, as it should be/);
}

const scenarioOf = (a: RunArtifact, id: string) => a.scenarios.find((s) => s.scenario.id === id)!;
const onlyS09 = (base: RunArtifact) => {
  const a = structuredClone(base);
  const s09 = structuredClone(scenarioOf(a, "S09"));
  s09.scenario.hero = true;
  return withScenarios([s09]);
};

describe("finding 3: the page claims no provenance", () => {
  it("the success line scopes srg verify to internal consistency and points provenance at offline replay", () => {
    render(<Report loaded={{ artifact: fixtureArtifact(), source: "run" }} />);
    const v = screen.getByTestId("verify");
    expect(v).toHaveAttribute("data-ok", "true");
    expect(v).not.toHaveTextContent(/recordings are verified/i);
    expect(v).not.toHaveTextContent(/outputs (and recordings )?are verified by srg verify/i);
    expect(v).toHaveTextContent(/srg verify checks the artifact's internal consistency/);
    expect(v).toHaveTextContent(/replaying the committed recordings offline with make verify/);
  });

  it.runIf(realRun)("the review specimen (S01/A rationale replaced) never meets a recording-verified claim", () => {
    const a = structuredClone(realRun!);
    scenarioOf(a, "S01").arms.A.decision.rationale = "Text that is not in any recording.";
    expect(validateArtifact(a).ok).toBe(true); // the page cannot detect it, so it must not claim to
    render(<Report loaded={{ artifact: a, source: "run" }} />);
    expect(document.body).not.toHaveTextContent(/verified by srg verify/);
  });
});

describe("finding 5: the payment warning checks the executed payment", () => {
  it.runIf(realRun)("S09/A executed refund 1 while the decision says 5800 → fallback, never 'paid, as it should be'", async () => {
    const a = onlyS09(realRun!);
    expect(validateArtifact(a).ok).toBe(true);
    const arm = scenarioOf(a, "S09").arms.A;
    expect(arm.decision.amount_minor).toBe(5800);
    arm.execution.refund!.amount_minor = 1;
    await expectFallback(a, /arms\/A\/execution refund 1 USD contradicts the decision 5800 USD/);
  });

  it("rejects a currency contradiction, executed without a refund, and a refund without executed", async () => {
    const paidArm = (a: RunArtifact) => Object.values(a.scenarios[0].arms).find((r) => r.outcome.disposition === "paid")!;
    const cur = fixtureArtifact();
    paidArm(cur).execution.refund!.currency = "CAD";
    expect(validateArtifact(cur).ok).toBe(false);

    const noRefund = fixtureArtifact();
    paidArm(noRefund).execution.refund = null;
    const v1 = validateArtifact(noRefund);
    expect(v1.ok).toBe(false);
    if (!v1.ok) expect(v1.errors.join(" ")).toMatch(/executed=true without a refund/);

    const notExecuted = fixtureArtifact();
    paidArm(notExecuted).execution.executed = false;
    const v2 = validateArtifact(notExecuted);
    expect(v2.ok).toBe(false);
    if (!v2.ok) expect(v2.errors.join(" ")).toMatch(/has a refund but executed=false/);
    await expectFallback(notExecuted, /has a refund but executed=false/);
  });

  it("the warning reads the ledger, not the decision", () => {
    const a = fixtureArtifact();
    const f01 = a.scenarios[0];
    const paid = Object.values(f01.arms).find((r) => r.outcome.disposition === "paid")!;
    paid.decision.amount_minor = 1; // decision alone changes: what was paid still matches the ticket
    expect(amountMismatch(paid, f01.scenario)).toBeNull();
  });
});

describe("finding 7: the ticket reference must resolve", () => {
  it.runIf(realRun)("S01 ticket_id 'not-the-ticket' → fallback, no substituted ticket", async () => {
    const a = structuredClone(realRun!);
    scenarioOf(a, "S01").scenario.ticket_id = "not-the-ticket";
    await expectFallback(a, /scenarios\/0\/scenario\/ticket_id not-the-ticket not found in t0\.state\.tickets/);
    expect(screen.queryByText(/TCK-90101/)).toBeNull();
  });

  it("a ticket missing only from t1_state is rejected too", () => {
    const a = fixtureArtifact();
    const s = a.scenarios[0];
    s.t1_state.tickets = s.t1_state.tickets.filter((t) => t.ticket_id !== s.scenario.ticket_id);
    const v = validateArtifact(a);
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.errors.join(" ")).toMatch(/not found in t1_state\.tickets/);
  });

  it("the hero throws (render fallback) instead of substituting tickets[0]", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const a = fixtureArtifact();
    const hero = a.scenarios.find((s) => s.scenario.hero) ?? a.scenarios[0];
    hero.scenario.ticket_id = "not-the-ticket";
    expect(() => render(<Report loaded={{ artifact: a, source: "run" }} />)).toThrow(/not-the-ticket/);
  });
});

describe("finding 9: control-only runs get a neutral intro", () => {
  it.runIf(realRun)("an S09-only artifact shows the neutral intro and hero copy", () => {
    const a = onlyS09(realRun!);
    expect(scenarioOf(a, "S09").state_diff).toEqual([]);
    expect(validateArtifact(a).ok).toBe(true);
    render(<Report loaded={{ artifact: a, source: "run" }} />);
    expect(document.body).not.toHaveTextContent(/world moved/i);
    expect(document.body).not.toHaveTextContent(/The world moves/);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Then it acted, later.");
    expect(document.body).toHaveTextContent(/no scenario’s state changed between the two/);
    expect(within(screen.getByTestId("hero")).getByText("Time passes, state unchanged")).toBeInTheDocument();
  });

  it("a run with a changed state keeps the moved-world headline", () => {
    render(<Report loaded={{ artifact: fixtureArtifact(), source: "run" }} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Then the world moved.");
  });
});
