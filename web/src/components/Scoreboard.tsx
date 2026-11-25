import type { ArmId, RunArtifact } from "../types";
import { ARM_IDS } from "../types";
import { deriveReadout, deriveScoreboard, isSimulatedLatency, type ArmCaution, type ArmTally } from "../derive";
import { HARM_NOTE, formatInt, formatMoney, plural } from "../format";
import { ArmMark } from "./ui";

const SEGMENTS = [
  { key: "correct_payments", label: "Correct payments", tone: "pay" },
  { key: "correct_stops", label: "Correct stops", tone: "stop" },
  { key: "false_stops", label: "False stops", tone: "false" },
  { key: "wrong_payments", label: "Wrong payments", tone: "wrong" },
] as const;

export function Scoreboard({ artifact }: { artifact: RunArtifact }) {
  const board = deriveScoreboard(artifact);
  const ro = deriveReadout(artifact);
  const names = (arms: ArmId[]) => arms.map((a) => `${a} (${spec(a)?.name ?? a})`).join(" and ");
  const cur = artifact.run.currency;
  // Recorded LLM latency is shown whenever any arm made calls. It is recomputed from the calls, so a
  // tampered summary percentile surfaces as a summary mismatch instead of being displayed.
  // `run.timings_included` governs optional gate timing only.
  const showLatency = ARM_IDS.some((id) => board[id].llm_latency_ms_p50 !== null);
  // A scripted provider makes no round trips: its latencies are simulated, never shown as measured.
  const simulated = isSimulatedLatency(artifact);
  const spec = (id: ArmId) => artifact.arms.find((a) => a.id === id);
  const armName = (a: ArmId) => `${a} (${spec(a)?.name ?? a})`;
  const tallies = (ts: ArmTally[]) =>
    ts.map((t) => `${armName(t.arm)}: ${plural(t.wrongPayments, "wrong payment")}, ${plural(t.llmCalls, "LLM call")} at T1`).join("; ");
  const cautions = (cs: ArmCaution[]) =>
    cs.map((c) => `${armName(c.arm)}: ${plural(c.falseStops, "false stop")} and ${c.reviewLoad} sent to review`).join("; ");

  const rows: { label: string; cell: (id: ArmId) => string; strong?: boolean }[] = [
    { label: "Wrong payments", cell: (id) => formatInt(board[id].wrong_payments), strong: true },
    { label: "Harm from wrong payments", cell: (id) => formatMoney(board[id].wrong_payment_minor, cur), strong: true },
    { label: "False stops", cell: (id) => formatInt(board[id].false_stops) },
    { label: "Correct stops", cell: (id) => formatInt(board[id].correct_stops) },
    { label: "Correct payments", cell: (id) => formatInt(board[id].correct_payments) },
    { label: "Review load", cell: (id) => formatInt(board[id].review_load) },
    { label: "Decisions changed vs T0", cell: (id) => formatInt(board[id].decisions_changed_vs_t0) },
    { label: "LLM calls at T1", cell: (id) => formatInt(board[id].llm_calls_at_t1) },
    { label: "Prompt tokens at T1", cell: (id) => formatInt(board[id].prompt_tokens_at_t1) },
    { label: "Completion tokens at T1", cell: (id) => formatInt(board[id].completion_tokens_at_t1) },
  ];
  if (showLatency) {
    rows.push({
      label: "LLM latency p50 / p95",
      cell: (id) =>
        board[id].llm_latency_ms_p50 === null ? "n/a" : simulated ? "simulated" : `${formatInt(board[id].llm_latency_ms_p50)} / ${formatInt(board[id].llm_latency_ms_p95)} ms`,
    });
  }

  return (
    <section id="scoreboard" className="section" aria-labelledby="score-title">
      <h2 id="score-title" className="section__title">Four strategies, one ledger.</h2>
      <p className="section__lede">
        Outcomes are computed from what each arm actually did against a simulated ledger, then compared with the scenario's ground truth. Nothing here is typed in by hand.
      </p>

      <ul className="readout" aria-label="What the numbers say">
        <li data-testid="readout-fewest">
          <b>Fewest wrong payments</b>
          <span>{names(ro.fewestWrong.arms)}: {plural(ro.fewestWrong.count, "wrong payment")}{ro.fewestWrong.arms.length > 1 ? " each" : ""}.</span>
        </li>
        <li data-testid="readout-money">
          <b>Least harm from wrong payments</b>
          <span>{names(ro.leastMoney.arms)}: {formatMoney(ro.leastMoney.minor, cur)}{ro.leastMoney.arms.length > 1 ? " each" : ""}.</span>
        </li>
        {ro.liveReaders.length > 0 && ro.gated.length > 0 && (
          <li>
            <b>Reading live state at action time</b>
            <span>
              Re-asking the model on the live state: {tallies(ro.liveReaders)}.
              {" "}Checking the same live state with rules: {tallies(ro.gated)}.
            </span>
          </li>
        )}
        {ro.mostCautious && (
          <li data-testid="readout-cautious">
            <b>Most cautious</b>
            <span>{cautions(ro.mostCautious)}.</span>
          </li>
        )}
      </ul>

      <div className="outcome-bars">
        {ARM_IDS.map((id) => {
          const total = board[id].scenarios || 1;
          return (
            <div key={id} className="outcome-bar" data-arm={id}>
              <div className="outcome-bar__label"><ArmMark arm={id} size="sm" /> {spec(id)?.name}</div>
              <div className="outcome-bar__track" role="img" aria-label={SEGMENTS.map((s) => `${board[id][s.key]} ${s.label.toLowerCase()}`).join(", ")}>
                {SEGMENTS.map((s) =>
                  board[id][s.key] > 0 ? (
                    <span key={s.key} className={`seg seg--${s.tone}`} style={{ flexGrow: board[id][s.key] / total }} title={`${s.label}: ${board[id][s.key]}`}>
                      {board[id][s.key]}
                    </span>
                  ) : null,
                )}
              </div>
            </div>
          );
        })}
        <ul className="legend" aria-label="Legend">
          {SEGMENTS.map((s) => (
            <li key={s.key}><span className={`legend__sw seg--${s.tone}`} /> {s.label}</li>
          ))}
        </ul>
      </div>

      <div className="tablewrap" tabIndex={0} role="region" aria-label="Scoreboard table, scrollable">
        <table className="score">
          <caption className="sr-only">Per-arm results across {artifact.scenarios.length} synthetic scenarios</caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {ARM_IDS.map((id) => (
                <th scope="col" key={id}><ArmMark arm={id} size="sm" /> <span className="score__arm">{spec(id)?.name}</span></th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} data-strong={r.strong ? true : undefined}>
                <th scope="row">{r.label}</th>
                {ARM_IDS.map((id) => <td key={id}>{r.cell(id)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="fineprint">
        {HARM_NOTE}{" "}
        {showLatency && simulated
          ? "These decisions came from a scripted fixture, not a model, so no provider round trip was made and latency is simulated."
          : showLatency
          ? "LLM latency is the recorded provider round trip per call (nearest-rank percentiles); in replay it is the latency captured when the call was recorded, not a fresh measurement."
          : "No LLM latency was recorded in this run, so none is shown."}
        {" "}Token totals read n/a unless every call in that arm reported its usage.
      </p>
    </section>
  );
}
