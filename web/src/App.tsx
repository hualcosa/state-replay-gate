import { Component, useCallback, useEffect, useState, type ReactNode } from "react";
import type { LoadedArtifact } from "./types";
import { LoadError, loadArtifact, loadFixture } from "./load";
import { heroOf, verifySummary } from "./derive";
import { Footer, ErrorState, Intro, LoadingState, TopBar } from "./components/Shell";
import { Hero } from "./components/Hero";
import { Headline } from "./components/Headline";
import { Scoreboard } from "./components/Scoreboard";
import { Scenarios } from "./components/Scenarios";
import { Honest } from "./components/Honest";

export function Report({ loaded }: { loaded: LoadedArtifact }) {
  const { artifact } = loaded;
  const hero = heroOf(artifact.scenarios);
  const mismatches = verifySummary(artifact);
  return (
    <>
      <TopBar loaded={loaded} />
      <main id="main">
        <Intro n={artifact.scenarios.length} arms={artifact.arms.length} moved={artifact.scenarios.some((s) => s.state_diff.length > 0)} />
        {hero ? <Hero key={hero.scenario.id} artifact={artifact} run={hero} /> : <p className="state">This run contains no scenarios.</p>}
        {artifact.scenarios.length > 0 && <Headline artifact={artifact} />}
        {artifact.scenarios.length > 0 && <Scoreboard artifact={artifact} />}
        <Scenarios artifact={artifact} />
        <Honest artifact={artifact} />
        <p className="verify" data-ok={mismatches.length === 0} data-testid="verify">
          {mismatches.length === 0
            ? "Checked on this page: the scoreboard, the headline split and the T0 eval count were recomputed from the per-scenario results and match the artifact's summary. Before rendering, every decision's amount and currency, every ledger entry against its decision, every ticket reference, every stored T0 eval and the reporting currency were checked against the run. This page does not check provenance: srg verify checks the artifact's internal consistency (it recomputes outcomes, gate verdicts and the summary from the artifact itself), and provenance comes from replaying the committed recordings offline with make verify."
            : `Warning: recomputed figures differ from the artifact summary (${mismatches.slice(0, 3).join("; ")}).`}
        </p>
      </main>
      <Footer loaded={loaded} />
    </>
  );
}

/** Catches render-time throws so a malformed run never leaves an empty page. */
export class RenderBoundary extends Component<{ onError: (e: Error) => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error) {
    this.props.onError(error);
  }
  render() {
    return this.state.failed ? <LoadingState /> : this.props.children;
  }
}

const asLoadError = (e: unknown) => (e instanceof LoadError ? e : new LoadError(String(e), []));

export default function App() {
  const [loaded, setLoaded] = useState<LoadedArtifact | null>(null);
  const [error, setError] = useState<LoadError | null>(null);
  useEffect(() => {
    let live = true;
    loadArtifact()
      .then((l) => live && setLoaded(l))
      .catch((e) => live && setError(asLoadError(e)));
    return () => {
      live = false;
    };
  }, []);
  const onRenderError = useCallback(
    (e: Error) => {
      const note = `render failed: ${e.message}`;
      // The fixture itself failing to render is terminal; otherwise swap in the fixture.
      if (loaded?.source === "fixture-fallback") {
        setError(new LoadError("The run artifact could not be rendered.", [...(loaded.notes ?? []), note]));
        return;
      }
      loadFixture("render", undefined, undefined, [note]).then(setLoaded, (err) => setError(asLoadError(err)));
    },
    [loaded],
  );
  if (error) return <ErrorState message={error.message} details={error.details} />;
  if (!loaded) return (<><a className="skip" href="#main">Skip to content</a><LoadingState /></>);
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <RenderBoundary key={`${loaded.source}:${loaded.reason ?? ""}`} onError={onRenderError}>
        <Report loaded={loaded} />
      </RenderBoundary>
    </>
  );
}
