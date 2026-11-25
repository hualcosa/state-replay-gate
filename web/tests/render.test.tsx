import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App, { Report } from "../src/App";
import { deriveScoreboard } from "../src/derive";
import { formatMoney } from "../src/format";
import type { LoadedArtifact } from "../src/types";
import { stubMatchMedia } from "./setup";
import { fixtureArtifact, withScenarios } from "./helpers";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  stubMatchMedia(true);
});

const loaded = (artifact = fixtureArtifact(), source: LoadedArtifact["source"] = "fixture-fallback"): LoadedArtifact => ({ artifact, source });

describe("App loading", () => {
  it("falls back to the fixture on 404, shows the fixture badge and one drilldown entry per scenario", async () => {
    const fx = JSON.stringify(fixtureArtifact());
    vi.stubGlobal("fetch", vi.fn(async (u: string) => (String(u).includes("data/run.json") ? new Response("", { status: 404 }) : new Response(fx))));
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("hero")).toBeInTheDocument());
    expect(screen.getAllByTestId("fixture-badge").length).toBeGreaterThan(0);
    expect(screen.getAllByTestId("drilldown-entry")).toHaveLength(5);
  });

  it("shows a clear error state when no artifact is valid", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
    render(<App />);
    expect(await screen.findByTestId("error-state")).toHaveTextContent(/Could not load/);
  });

  it("shows no fixture badge for a valid real run on a non-fixture model", async () => {
    const a = fixtureArtifact();
    a.run.model = "real-model-1";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(a))));
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("hero")).toBeInTheDocument());
    expect(screen.queryByTestId("fixture-badge")).toBeNull();
  });
});

describe("Report rendering from data", () => {
  it("renders a 5-scenario artifact and a 3-scenario artifact and a 1-scenario artifact", () => {
    const a = fixtureArtifact();
    for (const n of [5, 3, 1]) {
      const { unmount } = render(<Report loaded={loaded(withScenarios(a.scenarios.slice(0, n)))} />);
      expect(screen.getAllByTestId("drilldown-entry")).toHaveLength(n);
      expect(screen.getByTestId("verify")).toHaveAttribute("data-ok", "true");
      unmount();
    }
  });

  it("renders with zero scenarios without crashing", () => {
    render(<Report loaded={loaded(withScenarios([]))} />);
    expect(screen.queryAllByTestId("drilldown-entry")).toHaveLength(0);
    expect(screen.getByText(/contains no scenarios/i, { selector: "p.state" })).toBeInTheDocument();
  });

  it("chooses the hero by the hero flag, not by position or id", () => {
    const a = fixtureArtifact();
    render(<Report loaded={loaded(withScenarios([...a.scenarios].reverse()))} />);
    expect(screen.getByTestId("hero")).toHaveAttribute("data-scenario", a.scenarios.find((s) => s.scenario.hero)!.scenario.id);
  });

  it("selects honest cases by kind and shows their measured results", () => {
    const a = fixtureArtifact();
    render(<Report loaded={loaded(a)} />);
    const cases = screen.getAllByTestId("honest-case");
    expect(cases.map((c) => c.getAttribute("data-kind")).sort()).toEqual(["gate_blind_spot", "stale_denial"]);
    const blind = cases.find((c) => c.getAttribute("data-kind") === "gate_blind_spot")!;
    expect(within(blind).getAllByText("Wrong payment")).toHaveLength(4);
    const withoutHonest = withScenarios(a.scenarios.filter((s) => s.scenario.kind === "mutation" || s.scenario.kind === "control"));
    const { container } = render(<Report loaded={loaded(withoutHonest)} />);
    expect(container.querySelectorAll('[data-testid="honest-case"]').length).toBe(0);
  });

  it("renders the headline and scoreboard figures that match summary", () => {
    const a = fixtureArtifact();
    render(<Report loaded={loaded(a)} />);
    const board = deriveScoreboard(a);
    const bars = screen.getByRole("list", { name: /harm from wrong payments per arm/i });
    for (const id of ["A", "B", "C", "D"] as const) {
      expect(bars).toHaveTextContent(formatMoney(board[id].wrong_payment_minor, a.run.currency));
    }
    const headline = document.getElementById("headline")!;
    expect(headline).toHaveTextContent(`${a.summary.regressions.changed_on_frozen_state} of ${a.summary.regressions.scenarios_with_state_change} decisions`);
    expect(headline).toHaveTextContent(`With live state, ${a.summary.regressions.changed_on_live_state} changed.`);
    expect(headline).toHaveTextContent("In 4 of 5 the business state changed in between; the other kept the same state.");
    expect(headline).not.toHaveTextContent(/Every one/);
    const table = screen.getByRole("table", { name: /per-arm results/i });
    const row = within(table).getByRole("row", { name: /^Wrong payments/ });
    expect(row).toHaveTextContent(String(a.summary.by_arm.A.wrong_payments));
  });

  it("shows the gate's failed rule with message and observed values on the hero", () => {
    const a = fixtureArtifact();
    const hero = a.scenarios.find((s) => s.scenario.hero)!;
    const rule = hero.arms.D.gate!.results.find((r) => !r.passed)!;
    rule.message = "chargeback_open = true (DSP-7781)";
    rule.observed = { chargeback_open: true, dispute_ids: ["DSP-7781"] };
    render(<Report loaded={loaded(a)} />);
    const heroEl = screen.getByTestId("hero");
    expect(within(heroEl).getByText("chargeback_open = true (DSP-7781)")).toBeInTheDocument();
    expect(within(heroEl).getByText('chargeback_open = true')).toBeInTheDocument();
  });
});

describe("motion", () => {
  it("under prefers-reduced-motion nothing autoplays and the finished story is shown statically", () => {
    stubMatchMedia(true);
    render(<Report loaded={loaded()} />);
    const hero = screen.getByTestId("hero");
    expect(hero).toHaveAttribute("data-motion", "reduced");
    expect(hero).toHaveAttribute("data-playing", "false");
    expect(hero).toHaveAttribute("data-stage", "5");
    expect(within(hero).queryByText(/Waiting for T1/)).toBeNull();
  });

  it("with motion allowed the hero starts at T0 and autoplays forward, and can be paused", async () => {
    stubMatchMedia(false);
    vi.useFakeTimers();
    render(<Report loaded={loaded()} />);
    const hero = screen.getByTestId("hero");
    expect(hero).toHaveAttribute("data-motion", "full");
    expect(hero).toHaveAttribute("data-stage", "0");
    await act(async () => { await vi.advanceTimersByTimeAsync(1800); });
    expect(hero).toHaveAttribute("data-stage", "1");
    await act(async () => { await vi.advanceTimersByTimeAsync(2200); });
    expect(hero).toHaveAttribute("data-stage", "2");
    vi.useRealTimers();
    await userEvent.click(within(hero).getByRole("button", { name: /pause/i }));
    expect(hero).toHaveAttribute("data-playing", "false");
  });

  it("step buttons are keyboard reachable and jump to a stage", async () => {
    stubMatchMedia(true);
    render(<Report loaded={loaded()} />);
    const hero = screen.getByTestId("hero");
    await userEvent.click(within(hero).getByRole("button", { name: /T0 eval/ }));
    expect(hero).toHaveAttribute("data-stage", "0");
    expect(within(hero).getAllByText(/Waiting for T1/)).toHaveLength(4);
  });
});
