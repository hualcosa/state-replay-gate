import type { FallbackReason, LoadedArtifact } from "../types";
import { isFixtureData } from "../derive";
import { formatInstant, plural } from "../format";
import { Icon } from "./ui";

const REASON_LABEL: Record<FallbackReason, string> = {
  missing: "no run data, showing fixture",
  invalid: "data invalid, showing fixture",
  unreachable: "data unreachable, showing fixture",
  render: "data failed to render, showing fixture",
};

export function FixtureBadge({ source, model, reason, notes }: { source: string; model: string; reason?: FallbackReason; notes?: string[] }) {
  if (!isFixtureData(source, model)) return null;
  const label = source === "fixture-fallback" && reason ? REASON_LABEL[reason] : "fixture data";
  const title = ["This page is showing the built-in scripted fixture, not a recorded model run.", ...(notes ?? []).slice(0, 3)].join("\n");
  return (
    <span className="fixture-badge" data-testid="fixture-badge" data-reason={reason} title={title}>
      <Icon name="alert" size={13} /> {label}
    </span>
  );
}

export function TopBar({ loaded }: { loaded?: LoadedArtifact }) {
  return (
    <header className="topbar">
      <a className="topbar__brand" href="#top">
        <span className="topbar__logo" aria-hidden="true"><i /><i /></span>
        State Replay Gate
      </a>
      <nav aria-label="Sections" className="topbar__nav">
        <a href="#hero">Case</a>
        <a href="#headline">Headline</a>
        <a href="#scoreboard">Scoreboard</a>
        <a href="#scenarios">Scenarios</a>
        <a href="#honest">Honest cases</a>
      </nav>
      {loaded && <FixtureBadge source={loaded.source} model={loaded.artifact.run.model} reason={loaded.reason} notes={loaded.notes} />}
    </header>
  );
}

/**
 * The headline promises a moved world only when at least one scenario's state changed between T0
 * and T1; a control-only run (every state_diff empty) gets neutral copy instead.
 */
export function Intro({ n, arms, moved }: { n: number; arms: number; moved: boolean }) {
  return (
    <div className="intro" id="top" data-moved={moved}>
      <h1 className="intro__title">
        It passed the eval.<br />
        <span className="intro__t1">{moved ? "Then the world moved." : "Then it acted, later."}</span>
      </h1>
      {moved ? (
        <p className="intro__lede">
          A refund agent is tested at <b className="t0">T0</b> on a snapshot. The money moves at <b className="t1">T1</b>, after the business state has changed under an unchanged ticket.
          {" "}Here is what {arms} strategies would have paid across {plural(n, "synthetic scenario")}, and how a deterministic state-replay gate checks the live state before the refund runs.
        </p>
      ) : (
        <p className="intro__lede">
          A refund agent is tested at <b className="t0">T0</b> on a snapshot and acts at <b className="t1">T1</b>.
          {" "}In this run no scenario&rsquo;s state changed between the two, so it shows only unchanged controls: what {arms} strategies would have paid across {plural(n, "synthetic scenario")}, and what a deterministic state-replay gate decides on the live state before the refund runs.
        </p>
      )}
    </div>
  );
}

export function Footer({ loaded }: { loaded: LoadedArtifact }) {
  const r = loaded.artifact.run;
  const n = loaded.artifact.scenarios.length;
  return (
    <footer className="footer">
      <p className="footer__claim">
        {plural(n, "synthetic scenario")}, one model (<code>{r.model}</code>). Nothing on this page is a claim beyond that.
      </p>
      <dl className="footer__meta">
        <div><dt>Run</dt><dd><code>{r.run_id}</code></dd></div>
        <div><dt>Mode</dt><dd>{r.llm_mode}</dd></div>
        <div><dt>Model</dt><dd><code>{r.model}</code></dd></div>
        <div><dt>Recorded</dt><dd>{r.recorded_at ? formatInstant(r.recorded_at) : "not recorded"}</dd></div>
        <div><dt>Data</dt><dd>{loaded.source === "run" ? "data/run.json" : "fixtures/run.fixture.json (fallback)"}</dd></div>
      </dl>
    </footer>
  );
}

export function LoadingState() {
  return (
    <div className="state" role="status">
      <span className="state__pulse" aria-hidden="true" />
      Loading the run artifact…
    </div>
  );
}

export function ErrorState({ message, details }: { message: string; details: string[] }) {
  return (
    <div className="state state--error" role="alert" data-testid="error-state">
      <h1>Could not load a run artifact</h1>
      <p>{message}</p>
      <ul>
        {details.map((d, i) => <li key={i}><code>{d}</code></li>)}
      </ul>
      <p>
        Generate one with <code>uv run srg run --out web/public/data/run.json</code>, or run <code>pnpm --dir web run sync</code> to restore the fixture copy.
      </p>
    </div>
  );
}
