import { useEffect, useState } from "react";
import type { ArmId, ArmRun, RunArtifact, ScenarioRun } from "../types";
import { ARM_IDS } from "../types";
import { compactJson, formatInstant, formatMoney, humanize } from "../format";
import { amountMismatch, AMOUNT_MISMATCH_LABEL } from "../derive";
import { AmountFlag, ArmMark, CONTEXT_LABEL, Chip, ClassBadge, Icon, classIcon, classLabel, classShort, classTone, dispositionLabel } from "./ui";

const KIND_LABEL: Record<string, string> = {
  mutation: "State mutation",
  control: "Control",
  stale_denial: "Stale denial",
  gate_blind_spot: "Gate blind spot",
};

function Matrix({ artifact, onPick }: { artifact: RunArtifact; onPick: (id: string) => void }) {
  return (
    <div className="tablewrap" tabIndex={0} role="region" aria-label="Scenario by arm outcome matrix, scrollable">
      <table className="matrix">
        <caption className="sr-only">Outcome of each arm in each scenario. Select a scenario to open its details.</caption>
        <thead>
          <tr>
            <th scope="col">Scenario</th>
            {ARM_IDS.map((id) => <th scope="col" key={id}><ArmMark arm={id} size="sm" /></th>)}
          </tr>
        </thead>
        <tbody>
          {artifact.scenarios.map((s) => (
            <tr key={s.scenario.id}>
              <th scope="row">
                <button type="button" className="linklike" onClick={() => onPick(s.scenario.id)}>
                  <span className="matrix__id">{s.scenario.id}</span> {s.scenario.title}
                </button>
              </th>
              {ARM_IDS.map((id) => {
                const c = s.arms[id].outcome.classification;
                const odd = amountMismatch(s.arms[id], s.scenario);
                return (
                  <td key={id} className={`cell cell--${classTone(c)}`} data-class={c} data-amount-mismatch={odd ? "true" : undefined}>
                    <span className="cell__in" title={`${classLabel(c)} (${dispositionLabel(s.arms[id].outcome.disposition)})${odd ? `, ${AMOUNT_MISMATCH_LABEL}` : ""}`}>
                      <Icon name={classIcon(c)} size={14} />
                      <span className="cell__txt">{classShort(c)}</span>
                      {odd && <span className="cell__flag" data-testid="amount-mismatch">{AMOUNT_MISMATCH_LABEL}</span>}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ArmDetail({ id, run, currency, name, context, scenario }: { id: ArmId; run: ArmRun; currency: string; name: string; context: string; scenario: ScenarioRun["scenario"] }) {
  const d = run.decision;
  const mismatch = amountMismatch(run, scenario);
  return (
    <article className="armcard" data-arm={id} data-class={run.outcome.classification}>
      <header>
        <ArmMark arm={id} size="sm" />
        <div>
          <h5>{name}</h5>
          <p className="armcard__ctx">{CONTEXT_LABEL[context] ?? humanize(context)}</p>
        </div>
      </header>
      <p className="armcard__line">
        <code>{d.action}</code>
        {d.amount_minor !== null && <strong>{formatMoney(d.amount_minor, d.currency ?? currency)}</strong>}
      </p>
      <p className="armcard__line">
        <Chip tone={run.outcome.disposition === "paid" ? "plain" : "neutral"}>{dispositionLabel(run.outcome.disposition)}</Chip>
        <ClassBadge value={run.outcome.classification} compact />
        {run.decision_changed_vs_t0 && <Chip tone="t1">changed vs T0</Chip>}
        {mismatch && <AmountFlag mismatch={mismatch} />}
      </p>
      {mismatch && (
        <p className="armcard__mismatch">
          paid {formatMoney(mismatch.paidMinor, mismatch.paidCurrency)} · requested {formatMoney(mismatch.requestedMinor, mismatch.requestedCurrency)}
        </p>
      )}
      {run.outcome.classification === "wrong_payment" && (
        <p className="armcard__harm">
          −{formatMoney(run.outcome.harm.amount_minor, currency)} · {humanize(run.outcome.harm.type)}
          {run.execution.refund && run.execution.refund.amount_minor !== run.outcome.harm.amount_minor && (
            <> · paid {formatMoney(run.execution.refund.amount_minor, currency)}</>
          )}
        </p>
      )}
      <p className="armcard__label">Rationale</p>
      <p className="armcard__text">{d.rationale}</p>
      {run.memory_note && (
        <>
          <p className="armcard__label">Memory note it relied on</p>
          <p className="armcard__text">{run.memory_note}</p>
        </>
      )}
      <p className="armcard__meta">
        {run.llm ? (
          <>
            sample {run.llm.sample} · {run.llm.prompt_tokens ?? "n/a"} prompt / {run.llm.completion_tokens ?? "n/a"} completion tokens
          </>
        ) : (
          <>no LLM call at T1</>
        )}
      </p>
    </article>
  );
}

function GatePanel({ run, artifact }: { run: ScenarioRun; artifact: RunArtifact }) {
  const gate = run.arms.D.gate;
  if (!gate) {
    return (
      <div className="gate gate--none">
        <h4>Gate</h4>
        <p>
          The gate did not run: arm D's decision was <code>{run.arms.D.decision.action}</code>, so there is no refund to check.
          A gate is a brake, not a steering wheel.
        </p>
      </div>
    );
  }
  const desc = new Map(artifact.rules.map((r) => [r.id, r.description]));
  return (
    <div className="gate" data-verdict={gate.verdict}>
      <h4>
        Gate on live T1 state <span className={`verdict verdict--${gate.verdict}`}>{gate.verdict}</span>
      </h4>
      <p className="gate__meta">
        checked <time dateTime={gate.checked_at}>{formatInstant(gate.checked_at)}</time> · state digest <code title={gate.state_digest}>{gate.state_digest.slice(0, 19)}…</code>
      </p>
      <ul className="rules">
        {gate.results.map((r) => (
          <li key={r.rule_id} className="rule" data-passed={r.passed} data-severity={r.severity}>
            <span className="rule__mark">
              <Icon name={r.passed ? "check" : r.severity === "review" ? "alert" : "x"} size={14} />
              <span className="sr-only">{r.passed ? "passed" : "failed"}</span>
            </span>
            <div>
              <p className="rule__head">
                <code>{r.rule_id}</code>
                <Chip tone={r.severity === "block" ? "plain" : "warn"}>{r.severity}</Chip>
              </p>
              <p className="rule__desc">{desc.get(r.rule_id)}</p>
              <p className="rule__msg">{r.message}</p>
              {Object.keys(r.observed).length > 0 && (
                <p className="kv">
                  {Object.entries(r.observed).map(([k, v]) => (
                    <code key={k} className="kv__item">{k} = {compactJson(v)}</code>
                  ))}
                </p>
              )}
              {r.reads.length > 0 && <p className="rule__reads">reads {r.reads.join(", ")}</p>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Entry({ run, artifact, open, onToggle }: { run: ScenarioRun; artifact: RunArtifact; open: boolean; onToggle: (o: boolean) => void }) {
  const sc = run.scenario;
  const cur = artifact.run.currency;
  const t0 = run.t0;
  return (
    <details
      id={`scenario-${sc.id}`}
      className="entry"
      data-testid="drilldown-entry"
      data-kind={sc.kind}
      open={open}
      onToggle={(e) => onToggle((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        <span className="entry__id">{sc.id}</span>
        <span className="entry__title">{sc.title}</span>
        <span className="entry__kind">{KIND_LABEL[sc.kind] ?? humanize(sc.kind)}</span>
        {sc.hero && <Chip tone="t1">hero</Chip>}
        <span className="entry__dots" aria-hidden="true">
          {ARM_IDS.map((id) => <span key={id} className={`dot dot--${classTone(run.arms[id].outcome.classification)}`} />)}
        </span>
      </summary>
      <div className="entry__body">
        <div className="story">
          <p><b>Setup</b>{sc.story.setup}</p>
          <p><b>Twist</b>{sc.story.twist}</p>
          <p><b>Stakes</b>{sc.story.stakes}</p>
        </div>

        <h4>What changed between T0 and T1</h4>
        <p className="entry__mut">
          <Chip tone="t1">{sc.mutation.label}</Chip> {sc.mutation.actor}, <time dateTime={sc.mutation.at}>{formatInstant(sc.mutation.at)}</time>. {sc.mutation.narrative}
        </p>
        {run.state_diff.length === 0 ? (
          <p className="muted">No fields changed.</p>
        ) : (
          <div className="tablewrap" tabIndex={0} role="region" aria-label={`State diff for ${sc.id}, scrollable`}>
            <table className="diffTable">
              <thead><tr><th scope="col">Path</th><th scope="col">T0</th><th scope="col">T1</th></tr></thead>
              <tbody>
                {run.state_diff.map((d) => (
                  <tr key={d.path}>
                    <th scope="row"><code>{d.path}</code></th>
                    <td className="diffTable__from"><code>{compactJson(d.before)}</code></td>
                    <td className="diffTable__to"><code>{compactJson(d.after)}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h4>T0 eval</h4>
        <p>
          <code>{t0.decision.action}</code>
          {t0.decision.amount_minor !== null && <> <strong>{formatMoney(t0.decision.amount_minor, t0.decision.currency ?? cur)}</strong></>}
          {" "}against expected <code>{t0.eval.expected_action}</code>
          {t0.eval.expected_amount_minor !== null && <> {formatMoney(t0.eval.expected_amount_minor, cur)}</>}: {" "}
          <b>{t0.eval.passed ? "passed" : "failed"}</b>. Ground truth at T1: {sc.expected.t1_should_pay ? "pay" : "do not pay"}. {sc.expected.why}
        </p>

        <h4>Each arm at T1</h4>
        <div className="armgrid">
          {artifact.arms.map((a) => (
            <ArmDetail key={a.id} id={a.id} run={run.arms[a.id]} currency={cur} name={a.name} context={a.t1_context} scenario={run.scenario} />
          ))}
        </div>

        <GatePanel run={run} artifact={artifact} />
      </div>
    </details>
  );
}

export function Scenarios({ artifact }: { artifact: RunArtifact }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  const pick = (id: string) => {
    setOpened((s) => new Set(s).add(id));
    setOpenId(id);
  };
  useEffect(() => {
    if (!openId) return;
    const el = document.getElementById(`scenario-${openId}`);
    el?.scrollIntoView?.({ block: "start", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    (el?.querySelector("summary") as HTMLElement | null)?.focus?.({ preventScroll: true });
  }, [openId]);

  return (
    <section id="scenarios" className="section" aria-labelledby="scen-title">
      <h2 id="scen-title" className="section__title">Check the numbers yourself.</h2>
      <p className="section__lede">
        {artifact.scenarios.length === 0
          ? "This run contains no scenarios."
          : "One row per synthetic scenario, one column per arm. Open any entry for the state diff, each arm's rationale and the gate's full rule list."}
      </p>
      {artifact.scenarios.length > 0 && <Matrix artifact={artifact} onPick={pick} />}
      <div className="entries">
        {artifact.scenarios.map((s) => (
          <Entry
            key={s.scenario.id}
            run={s}
            artifact={artifact}
            open={opened.has(s.scenario.id)}
            onToggle={(o) =>
              setOpened((prev) => {
                const next = new Set(prev);
                if (o) next.add(s.scenario.id);
                else next.delete(s.scenario.id);
                return next;
              })
            }
          />
        ))}
      </div>
    </section>
  );
}
