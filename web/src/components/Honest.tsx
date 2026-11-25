import type { RunArtifact, ScenarioRun } from "../types";
import { ARM_IDS } from "../types";
import { amountMismatch, honestCases, isGood, whereABeatsD } from "../derive";
import { formatMoney, plural } from "../format";
import { AmountFlag, ArmMark, ClassBadge } from "./ui";

const COPY: Record<string, { head: string; body: string }> = {
  stale_denial: {
    head: "A gate is a brake, not a steering wheel",
    body: "The agent denied at T0, and by T1 the denial was stale. A denial is not an action, so there is nothing for the gate to check. Only a fresh read of the world would fix this.",
  },
  gate_blind_spot: {
    head: "The gate only sees structured state",
    body: "The change landed somewhere the rules cannot read, such as a free-text note or a dashboard that has not reached the ledger. A deterministic gate is exactly as good as the state it can query.",
  },
};

function Case({ run, artifact }: { run: ScenarioRun; artifact: RunArtifact }) {
  const sc = run.scenario;
  const cur = artifact.run.currency;
  const copy = COPY[sc.kind];
  return (
    <article className="honest__card" data-testid="honest-case" data-kind={sc.kind}>
      <h3>{copy.head}</h3>
      <p className="honest__kind"><span className="idchip">{sc.id}</span> {sc.kind === "stale_denial" ? "stale denial" : "off-ledger blind spot"}</p>
      <p>{copy.body}</p>
      <p className="honest__scn"><strong>{sc.title}.</strong> {sc.mutation.narrative}</p>
      <ul className="honest__arms">
        {ARM_IDS.map((id) => {
          const r = run.arms[id];
          const mismatch = amountMismatch(r, sc);
          return (
            <li key={id} data-class={r.outcome.classification}>
              <ArmMark arm={id} size="sm" />
              <ClassBadge value={r.outcome.classification} compact />
              {r.outcome.classification === "wrong_payment" && <span className="honest__amt">−{formatMoney(r.outcome.harm.amount_minor, cur)}</span>}
              {mismatch && <AmountFlag mismatch={mismatch} />}
            </li>
          );
        })}
      </ul>
    </article>
  );
}

export function Honest({ artifact }: { artifact: RunArtifact }) {
  const cases = honestCases(artifact.scenarios);
  const aWins = whereABeatsD(artifact.scenarios);
  const dMisses = artifact.scenarios.filter((s) => !isGood(s.arms.D.outcome.classification));
  if (cases.length === 0 && aWins.length === 0) return null;
  return (
    <section id="honest" className="section section--honest" aria-labelledby="honest-title">
      <h2 id="honest-title" className="section__title">{dMisses.length > 0 ? "Where the gate does not save you." : "The cases built to test the gate's limits."}</h2>
      <p className="section__lede">
        Measured with the same pipeline as everything else, and shown rather than hidden.
        {" "}Arm D ended badly in {plural(dMisses.length, "scenario")}.
        {aWins.length > 0 ? ` Arm A did better than arm D in ${plural(aWins.length, "scenario")}: ${aWins.map((s) => s.scenario.id).join(", ")}.` : " Arm A did not beat arm D in any scenario."}
      </p>
      <div className="honest__grid">
        {cases.map((c) => <Case key={c.scenario.id} run={c} artifact={artifact} />)}
      </div>
    </section>
  );
}
