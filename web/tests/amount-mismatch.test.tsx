// Review round 2, finding 1 (web scope): a refund for the wrong amount is never shown as a correct
// refund. Specimen: S09/C pays 1 minor unit instead of the requested 5800 (C1 classification is
// unchanged); D reuses C's decision and the gate blocks it with amount_matches_request.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Report } from "../src/App";
import { AMOUNT_MISMATCH_LABEL, amountMismatch, deriveHeadline, deriveScoreboard, deriveT0, verifySummary } from "../src/derive";
import { validateArtifact } from "../src/load";
import type { RunArtifact, ScenarioRun } from "../src/types";
import { fixtureArtifact } from "./helpers";

const realRun = Object.values(import.meta.glob("../public/data/run.json", { eager: true, import: "default" }))[0] as RunArtifact | undefined;

const byId = (a: RunArtifact, id: string): ScenarioRun => a.scenarios.find((s) => s.scenario.id === id)!;

/** Recomputes the summary so the specimen stays self-consistent. */
function resummarize(a: RunArtifact): RunArtifact {
  const summary = a.summary as unknown as {
    by_arm: Record<string, Record<string, unknown>>;
    regressions: Record<string, number>;
    t0_eval_passed: number;
    t0_eval_total: number;
  };
  const board = deriveScoreboard(a);
  for (const arm of ["A", "B", "C", "D"] as const) Object.assign(summary.by_arm[arm], board[arm]);
  Object.assign(summary.regressions, deriveHeadline(a));
  const t0 = deriveT0(a);
  summary.t0_eval_passed = t0.passed;
  summary.t0_eval_total = t0.total;
  return a;
}

/** S09/C pays $0.01 on a $58.00 request; D (same decision) is blocked by amount_matches_request. */
function specimen(base: RunArtifact): RunArtifact {
  const a = structuredClone(base);
  for (const s of a.scenarios) s.scenario.hero = s.scenario.id === "S09";
  const s09 = byId(a, "S09");
  const ticketId = s09.scenario.ticket_id;
  const c = s09.arms.C;
  expect(c.decision.amount_minor).toBe(5800);
  c.decision.amount_minor = 1;
  c.decision_changed_vs_t0 = true;
  c.execution.refund!.amount_minor = 1;
  c.execution.total_refunded_minor = 1;
  const d = s09.arms.D;
  d.decision = structuredClone(c.decision);
  d.decision_changed_vs_t0 = true;
  const gate = d.gate!;
  gate.verdict = "block";
  gate.failed_rules = ["amount_matches_request"];
  const rule = gate.results.find((r) => r.rule_id === "amount_matches_request")!;
  rule.passed = false;
  rule.observed = { ...rule.observed, action_amount_minor: 1 };
  rule.message = `refund 0.01 USD ≠ requested 58.00 USD (${ticketId})`;
  d.execution = { executed: false, refund: null, total_refunded_minor: 0 };
  d.outcome = { disposition: "blocked", classification: "false_stop", harm: d.outcome.harm };
  return resummarize(a);
}

describe("finding 1: nonconforming refund amounts", () => {
  it("amountMismatch compares the executed refund with the scenario's ticket", () => {
    const a = fixtureArtifact();
    for (const s of a.scenarios) for (const id of ["A", "B", "C", "D"] as const) expect(amountMismatch(s.arms[id], s.scenario)).toBeNull();
    const f01 = a.scenarios[0];
    const paid = Object.values(f01.arms).find((r) => r.outcome.disposition === "paid")!;
    paid.execution.refund!.amount_minor += 1;
    expect(amountMismatch(paid, f01.scenario)).not.toBeNull();
    paid.execution.refund!.amount_minor -= 1;
    paid.execution.refund!.currency = "CAD";
    expect(amountMismatch(paid, f01.scenario)).not.toBeNull();
  });

  it.runIf(realRun)("the real run has nine rules, D checks all of them, and nothing is flagged", () => {
    const a = structuredClone(realRun!);
    expect(a.rules.map((r) => r.id)).toContain("amount_matches_request");
    expect(a.rules).toHaveLength(9);
    for (const s of a.scenarios) {
      if (s.arms.D.gate) expect(s.arms.D.gate.results.map((r) => r.rule_id)).toEqual(a.rules.map((r) => r.id));
      for (const id of ["A", "B", "C", "D"] as const) expect(amountMismatch(s.arms[id], s.scenario)).toBeNull();
    }
    render(<Report loaded={{ artifact: a, source: "run" }} />);
    expect(screen.queryAllByTestId("amount-mismatch")).toHaveLength(0);
  });

  it.runIf(realRun)("S09/C amount=1: C is marked amount ≠ requested, never 'as it should be'; D is blocked", async () => {
    const a = specimen(realRun!);
    expect(validateArtifact(a).ok).toBe(true);
    expect(verifySummary(a)).toEqual([]);
    const s09 = byId(a, "S09");
    expect(s09.arms.C.outcome.classification).toBe("correct_payment"); // §5.6 is unchanged
    expect(amountMismatch(s09.arms.C, s09.scenario)).toEqual({
      paidMinor: 1,
      paidCurrency: "USD",
      requestedMinor: 5800,
      requestedCurrency: "USD",
    });
    expect(amountMismatch(s09.arms.D, s09.scenario)).toBeNull();

    render(<Report loaded={{ artifact: a, source: "run" }} />);
    const hero = screen.getByTestId("hero");
    const laneC = within(hero).getByRole("article", { name: /Arm C/ });
    expect(within(laneC).getByTestId("amount-mismatch")).toHaveTextContent(AMOUNT_MISMATCH_LABEL);
    expect(laneC).toHaveTextContent("$0.01 paid; the ticket requested $58.00");
    expect(laneC).not.toHaveTextContent(/as it should be/);
    const laneD = within(hero).getByRole("article", { name: /Arm D/ });
    expect(within(laneD).queryByTestId("amount-mismatch")).toBeNull();
    expect(laneD).toHaveTextContent("amount_matches_request");
    expect(laneD).toHaveTextContent("refund 0.01 USD ≠ requested 58.00 USD");
    // No lane anywhere pairs the marker with the "as it should be" copy.
    for (const flag of screen.getAllByTestId("amount-mismatch")) {
      expect(flag.closest("article, td, li")).not.toHaveTextContent(/as it should be/);
    }

    // The scenario matrix marks the S09/C cell, and only it.
    const matrix = screen.getByRole("table", { name: /Outcome of each arm/ });
    const flagged = within(matrix).getAllByTestId("amount-mismatch");
    expect(flagged).toHaveLength(1);
    const row = flagged[0].closest("tr")!;
    expect(row).toHaveTextContent("S09");

    // The scenario detail shows the marker on C and the ninth rule in D's gate panel.
    await userEvent.click(within(row).getByRole("button"));
    const detailC = document.querySelector('.armcard[data-arm="C"]') as HTMLElement | null;
    const openCards = Array.from(document.querySelectorAll<HTMLElement>('.armcard[data-arm="C"]')).filter((el) =>
      within(el).queryByTestId("amount-mismatch"),
    );
    expect(detailC).not.toBeNull();
    expect(openCards.length).toBeGreaterThan(0);
    expect(openCards[0]).toHaveTextContent("paid $0.01 · requested $58.00");
    const failedRule = Array.from(document.querySelectorAll<HTMLElement>('.gate .rule[data-passed="false"]')).find((el) =>
      el.textContent?.includes("amount_matches_request"),
    );
    expect(failedRule).toBeDefined();
    expect(failedRule).toHaveTextContent("The amount and currency equal the customer's request on the ticket.");
  });
});
