import type { ArmId, RunArtifact } from "../types";
import { ARM_IDS } from "../types";
import { deriveControlVariance, deriveHeadline, deriveScoreboard, deriveT0 } from "../derive";
import { HARM_NOTE, formatMoney, plural } from "../format";
import { useInViewOnce, usePrefersReducedMotion } from "../hooks";
import { ArmMark } from "./ui";

function Pips({ total, filled, tone }: { total: number; filled: number; tone: string }) {
  return (
    <span className={`pips pips--${tone}`} role="img" aria-label={`${filled} of ${total}`}>
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className="pips__pip" data-on={i < filled} style={{ ["--i" as string]: i }} />
      ))}
    </span>
  );
}

export function Headline({ artifact }: { artifact: RunArtifact }) {
  const h = deriveHeadline(artifact);
  const board = deriveScoreboard(artifact);
  const t0 = deriveT0(artifact);
  const cur = artifact.run.currency;
  const reduced = usePrefersReducedMotion();
  const [ref, seen] = useInViewOnce<HTMLElement>(0.25);
  const maxMinor = Math.max(0, ...ARM_IDS.map((a) => board[a].wrong_payment_minor));
  const N = h.scenarios_with_state_change;
  const unchanged = t0.total - N;
  const variance = deriveControlVariance(artifact);
  const specOf = (id: ArmId) => artifact.arms.find((a) => a.id === id);

  return (
    <section ref={ref} id="headline" className="section" aria-labelledby="headline-title" data-reveal={reduced || seen ? "on" : "off"}>
      <h2 id="headline-title" className="section__title">
        {N === 0 ? (
          <>No scenario in this run changed state between T0 and T1.</>
        ) : (
          <>
            Frozen context changed <em className="num num--good">{h.changed_on_frozen_state}</em> of {plural(N, "decision")}.
            <br />
            With live state, <em className="num num--t1">{h.changed_on_live_state}</em> changed.
          </>
        )}
      </h2>
      <p className="section__lede">
        {plural(t0.total, "synthetic scenario")}, one model, each judged at T0 on a snapshot and re-run at T1 ({t0.passed} of {t0.total} passed the T0 eval).
        {" "}{N === t0.total ? "In every one" : `In ${N} of ${t0.total}`} the business state changed in between{unchanged > 0 ? `; ${unchanged === 1 ? "the other kept" : `the other ${unchanged} kept`} the same state` : ""}.
        {" "}Arm A re-asks the model on the live state; arm C re-asks it on the frozen snapshot, so any decision that differs there is a true regression. Both counts below cover only the {plural(N, "scenario")} whose state changed.
      </p>

      {N > 0 && (
        <div className="split">
          <div className="split__card">
            <h3>Decision changed with <span className="nowrap">live state</span> <span className="armtag">arm A</span></h3>
            <p className="split__big"><span className="num num--t1">{h.changed_on_live_state}</span><span className="split__of"> / {N}</span></p>
            <Pips total={N} filled={h.changed_on_live_state} tone="t1" />
            <p className="split__cap">Arm A changed its decision when it read the live state. This is observed alongside the state change, not proof the change caused it, and it is not counted as a model regression.</p>
          </div>
          <div className="split__card">
            <h3>Decision changed on <span className="nowrap">frozen context</span> <span className="armtag">arm C</span></h3>
            <p className="split__big"><span className="num num--good">{h.changed_on_frozen_state}</span><span className="split__of"> / {N}</span></p>
            <Pips total={N} filled={h.changed_on_frozen_state} tone="bad" />
            <p className="split__cap">Same request as T0, second sample. A change here is a true regression, and it would show up in red.</p>
          </div>
        </div>
      )}

      {variance.length > 0 && (
        <div className="variance" data-testid="control-variance">
          <h3>Variance on unchanged controls</h3>
          <p>
            The state did not change in these scenarios, yet a re-asked arm decided differently from T0. That is sampling variance, not a reaction to the world, and it is kept out of the counts above:
            {" "}{variance.map((v) => `${v.scenario} (arm ${v.arms.join(" and ")})`).join(", ")}.
          </p>
        </div>
      )}

      <h3 className="subhead">Harm from wrong payments</h3>
      <p className="fineprint" data-testid="harm-note">{HARM_NOTE}</p>
      <ul className="bars" aria-label="Harm from wrong payments per arm">
        {ARM_IDS.map((id) => {
          const s = board[id];
          const w = maxMinor === 0 ? 0 : Math.max(2, Math.round((s.wrong_payment_minor / maxMinor) * 100));
          return (
            <li key={id} className="bars__row" data-arm={id}>
              <span className="bars__who"><ArmMark arm={id} size="sm" /> <span>{specOf(id)?.name ?? `Arm ${id}`}</span></span>
              <span className="bars__track" aria-hidden="true">
                <span className="bars__fill" style={{ ["--w" as string]: `${w}%` }} data-zero={s.wrong_payment_minor === 0} />
              </span>
              <span className="bars__amt">
                <strong>{formatMoney(s.wrong_payment_minor, cur)}</strong>
                <small>{plural(s.wrong_payments, "wrong payment")}</small>
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
