// Finding 4: a render-time throw on the run artifact falls back to the fixture instead of an empty root.
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { fixtureArtifact } from "./helpers";

vi.mock("../src/components/Hero", () => ({
  Hero: ({ artifact }: { artifact: { run: { model: string } } }) => {
    if (artifact.run.model === "boom") throw new Error("boom while rendering");
    return <div data-testid="hero" />;
  },
}));

afterEach(() => vi.restoreAllMocks());

describe("render error boundary", () => {
  it("a render throw on data/run.json shows the fixture with a visible reason", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const run = fixtureArtifact();
    run.run.model = "boom";
    const fx = JSON.stringify(fixtureArtifact());
    vi.stubGlobal("fetch", vi.fn(async (u: string) => new Response(String(u).includes("data/run.json") ? JSON.stringify(run) : fx)));
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("fixture-badge")).toHaveTextContent("data failed to render, showing fixture"));
    expect(screen.getByTestId("hero")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("a render throw on the fixture itself shows the error state, not an empty page", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fx = fixtureArtifact();
    fx.run.model = "boom";
    vi.stubGlobal("fetch", vi.fn(async (u: string) => (String(u).includes("data/run.json") ? new Response("", { status: 404 }) : new Response(JSON.stringify(fx)))));
    render(<App />);
    expect(await screen.findByTestId("error-state")).toHaveTextContent(/could not be rendered/);
    vi.unstubAllGlobals();
  });
});
