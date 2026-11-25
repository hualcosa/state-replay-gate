import { useCallback, useEffect, useRef, useState } from "react";
import type { ArmRun, ArmSpec, RunArtifact, ScenarioRun } from "../types";
import { compactJson, formatInstant, formatMoney, humanize } from "../format";
import { amountMismatch, stateFacts } from "../derive";
import { useCountUp, useInViewOnce, usePrefersReducedMotion } from "../hooks";
import { AmountFlag, ArmMark, CONTEXT_LABEL, Chip, ClassBadge, Icon, classTone, dispositionLabel } from "./ui";

const STAGE_DELAY_MS = [1700, 2100, 1250, 1250, 1250, 1250];

function MoneyCounter({ minor, currency, active, animate }: { minor: number; currency: string; active: boolean; animate: boolean }) {
  const v = useCountUp(minor, active, animate);
  return <span className="money-counter">{formatMoney(v, currency)}</span>;
}

function tokenTarget(run: ArmRun): "ledger" | "gate" | "none" {
  if (run.outcome.disposition === "paid") return "ledger";
  if (run.outcome.disposition === "blocked" || run.outcome.disposition === "held_for_review") return "gate";
  return "none";
}

function ArmLane({
  spec,
  run,
  revealed,
  animate,
  currency,
  rules,
  scenario,
}: {
  spec: ArmSpec;
  run: ArmRun;
  scenario: ScenarioRun["scenario"];
  revealed: boolean;
  animate: boolean;
  currency: string;
  rules: RunArtifact["rules"];
}) {
  const cls = run.outcome.classification;
  const target = tokenTarget(run);
  const decision = run.decision;
  const failed = run.gate?.results.filter((r) => !r.passed) ?? [];
  const wrong = cls === "wrong_payment";
  const gateBlocked = run.gate && run.gate.verdict !== "allow";
  const mismatch = amountMismatch(run, scenario);
  void rules;
  return (
    <article
      className="lane"
      data-arm={spec.id}
      data-revealed={revealed}
      data-class={cls}
      data-disposition={run.outcome.disposition}
      aria-label={`Arm ${spec.id}: ${spec.name}`}
    >
      <header className="lane__head">
        <ArmMark arm={spec.id} />
        <div>
          <h4 className="lane__name">{spec.name}</h4>
          <p className="lane__sees">{CONTEXT_LABEL[spec.t1_context] ?? humanize(spec.t1_context)}</p>
        </div>
      </header>

      <div className="pipe" data-token={revealed ? target : "idle"} data-gate={spec.has_gate} aria-hidden="true">
        <span className="pipe__label pipe__label--start">decision</span>
        <span className="pipe__rail" />
        {spec.has_gate && <span className="pipe__gate"><Icon name="shield" size={14} /></span>}
        <span className="pipe__label pipe__label--end">ledger</span>
        <span className="pipe__token">$</span>
      </div>

      {revealed ? (
        <div className="lane__body">
          <p className="lane__decision">
            <code>{decision.action}</code>
            {decision.amount_minor !== null && <strong>{formatMoney(decision.amount_minor, decision.currency ?? currency)}</strong>}
            {run.decision_changed_vs_t0 ? <Chip tone="t1">changed its mind</Chip> : <Chip>same as T0</Chip>}
          </p>
          <p className="lane__stamp" data-tone={classTone(cls)}>
            <span className="stamp">{dispositionLabel(run.outcome.disposition)}</span>
          </p>
          <div className="lane__verdict">
            <ClassBadge value={cls} />
            {mismatch && <AmountFlag mismatch={mismatch} />}
            {wrong ? (
              <p className="lane__harm">
                <span className="lane__harm-amt">
                  <span aria-hidden="true">−</span>
                  <MoneyCounter minor={run.outcome.harm.amount_minor} currency={currency} active={revealed} animate={animate} />
                </span>
                <span className="lane__harm-type">{humanize(run.outcome.harm.type)}</span>
              </p>
            ) : mismatch ? (
              <p className="lane__mismatch">
                {formatMoney(mismatch.paidMinor, mismatch.paidCurrency)} paid; the ticket requested{" "}
                {formatMoney(mismatch.requestedMinor, mismatch.requestedCurrency)}
              </p>
            ) : run.outcome.disposition === "paid" ? (
              <p className="lane__ok">{formatMoney(run.execution.refund?.amount_minor ?? 0, currency)} paid, as it should be</p>
            ) : null}
          </div>
          {wrong && run.outcome.harm.explanation && <p className="lane__why">{run.outcome.harm.explanation}</p>}
          {spec.has_gate && run.gate && (
            <div className="lane__gate" data-verdict={run.gate.verdict}>
              <p className="lane__gate-head">
                <Icon name={gateBlocked ? "shield" : "check"} size={14} />
                Gate on live T1 state: <strong>{run.gate.verdict}</strong>
                {run.gate.failed_rules.length > 0 && <> · {run.gate.failed_rules.join(", ")}</>}
              </p>
              {failed.map((r) => (
                <div key={r.rule_id} className="lane__rule">
                  <p className="lane__rule-msg">{r.message}</p>
                  {Object.keys(r.observed).length > 0 && (
                    <p className="kv">
                      {Object.entries(r.observed).map(([k, v]) => (
                        <code key={k} className="kv__item">
                          {k} = {compactJson(v)}
                        </code>
                      ))}
                    </p>
                  )}
                </div>
              ))}
              {failed.length === 0 && <p className="lane__rule-msg">All {run.gate.results.length} rules passed on the live state.</p>}
            </div>
          )}
          {spec.has_gate && !run.gate && (
            <p className="lane__gate lane__gate--none">No refund to gate: this decision issued none.</p>
          )}
          {!spec.has_gate && <p className="lane__rationale">{decision.rationale}</p>}
        </div>
      ) : (
        <div className="lane__body lane__body--pending">
          <p>Waiting for T1…</p>
        </div>
      )}
    </article>
  );
}

function FactsList({ facts, other, side }: { facts: ReturnType<typeof stateFacts>; other?: ReturnType<typeof stateFacts>; side: "t0" | "t1" }) {
  return (
    <dl className="facts" data-side={side}>
      {facts.map((f, i) => {
        const changed = other ? other[i].value !== f.value : false;
        return (
          <div key={f.key} className="facts__row" data-changed={changed}>
            <dt>{f.label}</dt>
            <dd>
              {f.value}
              {changed && <span className="facts__flag"> changed</span>}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function Hero({ artifact, run }: { artifact: RunArtifact; run: ScenarioRun }) {
  const reduced = usePrefersReducedMotion();
  const arms = artifact.arms;
  const maxStage = 1 + arms.length;
  const [stage, setStage] = useState(reduced ? maxStage : 0);
  const [playing, setPlaying] = useState(false);
  const [ref, inView] = useInViewOnce<HTMLElement>(0.35);
  const started = useRef(false);

  // Honour the preference as it changes: reduced motion shows the finished story, statically.
  useEffect(() => {
    if (reduced) {
      setPlaying(false);
      setStage(maxStage);
    }
  }, [reduced, maxStage]);

  // Autoplay once, when the stage first scrolls into view.
  useEffect(() => {
    if (!reduced && inView && !started.current) {
      started.current = true;
      setPlaying(true);
    }
  }, [reduced, inView]);

  useEffect(() => {
    if (!playing || reduced) return;
    if (stage >= maxStage) {
      setPlaying(false);
      return;
    }
    const id = window.setTimeout(() => setStage((s) => s + 1), STAGE_DELAY_MS[stage] ?? 1250);
    return () => window.clearTimeout(id);
  }, [playing, stage, maxStage, reduced]);

  const goto = useCallback((n: number) => {
    setPlaying(false);
    setStage(Math.max(0, Math.min(maxStage, n)));
  }, [maxStage]);
  const replay = () => {
    setStage(0);
    setPlaying(!reduced);
    if (reduced) setStage(0);
  };

  const sc = run.scenario;
  // Loading rejects an unresolved ticket_id; never substitute another ticket if one slips through.
  const ticket = sc.t0.state.tickets.find((t) => t.ticket_id === sc.ticket_id);
  if (!ticket) throw new Error(`${sc.id}: ticket_id ${sc.ticket_id} not found in t0.state.tickets`);
  const moved = run.state_diff.length > 0;
  const factsT0 = stateFacts(sc.t0.state, artifact.run.currency);
  const factsT1 = stateFacts(run.t1_state, artifact.run.currency);
  const money = (m: number | null, c?: string | null) => formatMoney(m, c ?? artifact.run.currency);
  const t0 = run.t0;
  const labels = ["T0 eval", moved ? "T1 world moves" : "T1 state unchanged", ...arms.map((a) => `Arm ${a.id}`)];

  return (
    <section
      ref={ref}
      id="hero"
      className="stage"
      aria-labelledby="hero-title"
      data-testid="hero"
      data-scenario={sc.id}
      data-stage={stage}
      data-motion={reduced ? "reduced" : "full"}
      data-playing={playing}
    >
      <div className="stage__top">
        <div>
          <h2 id="hero-title" className="stage__title"><span className="idchip">{sc.id}</span> {sc.title}</h2>
          <p className="stage__story">{sc.story.setup}</p>
        </div>
        <div className="controls" role="group" aria-label="Playback">
          <button type="button" className="btn" onClick={() => goto(stage - 1)} disabled={stage === 0} aria-label="Previous step">
            <Icon name="prev" />
          </button>
          {playing ? (
            <button type="button" className="btn btn--primary" onClick={() => setPlaying(false)}>
              <Icon name="pause" /> Pause
            </button>
          ) : stage >= maxStage ? (
            <button type="button" className="btn btn--primary" onClick={replay}>
              <Icon name="replay" /> Replay
            </button>
          ) : (
            <button type="button" className="btn btn--primary" onClick={() => { started.current = true; setPlaying(!reduced); if (reduced) goto(stage + 1); }}>
              <Icon name="play" /> {reduced ? "Next" : "Play"}
            </button>
          )}
          <button type="button" className="btn" onClick={() => goto(stage + 1)} disabled={stage >= maxStage} aria-label="Next step">
            <Icon name="next" />
          </button>
        </div>
      </div>

      <ol className="steps" aria-label="Story steps">
        {labels.map((l, i) => (
          <li key={l}>
            <button type="button" className="steps__btn" data-active={stage === i} data-done={stage > i} aria-current={stage === i ? "step" : undefined} onClick={() => goto(i)}>
              <span className="steps__n">{i + 1}</span>
              {l}
            </button>
          </li>
        ))}
      </ol>

      <div className="timeline" data-t1={stage >= 1}>
        <article className="moment moment--t0" data-on>
          <header className="moment__head">
            <span className="moment__tag moment__tag--t0">T0</span>
            <div>
              <h3>Eval time</h3>
              <time dateTime={sc.t0.at}>{formatInstant(sc.t0.at)}</time>
            </div>
          </header>
          <blockquote className="ticket">
            <p className="ticket__meta">
              {ticket.ticket_id} · {humanize(ticket.reason_code)} · requests <strong>{money(ticket.requested_minor, ticket.currency)}</strong>
            </p>
            <p className="ticket__msg">“{ticket.message}”</p>
          </blockquote>
          <p className="decision-line">
            Agent decides <code>{t0.decision.action}</code>
            {t0.decision.amount_minor !== null && <strong> {money(t0.decision.amount_minor, t0.decision.currency)}</strong>}
          </p>
          <p className={`evalpass evalpass--${t0.eval.passed ? "ok" : "fail"}`}>
            <Icon name={t0.eval.passed ? "check" : "x"} />
            T0 eval {t0.eval.passed ? "passed" : "failed"}
            <span> · expected <code>{t0.eval.expected_action}</code></span>
          </p>
          <FactsList facts={factsT0} side="t0" />
        </article>

        <div className="rail" data-on={stage >= 1} aria-live="polite">
          <div className="rail__line" aria-hidden="true" />
          <div className="rail__card">
            <span className="rail__kicker"><Icon name="clock" size={14} /> {moved ? "The world moves" : "Time passes, state unchanged"}</span>
            <h3 className="rail__label">{sc.mutation.label}</h3>
            <p className="rail__actor">{sc.mutation.actor} · <time dateTime={sc.mutation.at}>{formatInstant(sc.mutation.at)}</time></p>
            <p className="rail__narr">{sc.mutation.narrative}</p>
          </div>
        </div>

        <article className="moment moment--t1" data-on={stage >= 1} aria-hidden={stage < 1}>
          <header className="moment__head">
            <span className="moment__tag moment__tag--t1">T1</span>
            <div>
              <h3>Action time</h3>
              <time dateTime={sc.mutation.at}>{formatInstant(sc.mutation.at)}</time>
            </div>
          </header>
          <blockquote className="ticket ticket--same">
            <p className="ticket__meta">
              {ticket.ticket_id} · {humanize(ticket.reason_code)} · requests <strong>{money(ticket.requested_minor, ticket.currency)}</strong>
              <Chip>unchanged</Chip>
            </p>
            <p className="ticket__msg">“{ticket.message}”</p>
          </blockquote>
          <p className="decision-line">The agent is about to act on the T0 request. The world it reads is below.</p>
          <FactsList facts={factsT1} other={factsT0} side="t1" />
          <details className="diff" open>
            <summary>{run.state_diff.length} raw state change{run.state_diff.length === 1 ? "" : "s"}</summary>
            <ul>
              {run.state_diff.map((d) => (
                <li key={d.path}>
                  <code>{d.path}</code>
                  <span className="diff__from">{compactJson(d.before)}</span>
                  <Icon name="arrow" size={12} />
                  <span className="diff__to">{compactJson(d.after)}</span>
                </li>
              ))}
            </ul>
          </details>
        </article>
      </div>

      <div className="lanes">
        {arms.map((spec, i) => (
          <ArmLane
            key={spec.id}
            spec={spec}
            run={run.arms[spec.id]}
            revealed={stage >= 2 + i}
            animate={!reduced}
            currency={artifact.run.currency}
            rules={artifact.rules}
            scenario={run.scenario}
          />
        ))}
      </div>
      {stage >= maxStage && (
        <p className="stage__why">
          <strong>Ground truth at T1.</strong> {run.scenario.expected.why}
        </p>
      )}
    </section>
  );
}
