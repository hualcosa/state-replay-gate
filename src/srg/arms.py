"""Four-arm runner.

Per scenario: S1 = apply_patch(S0, mutation.ops). The provider is called in the order T0
(sample 0), C (sample 1, same request as T0), A (live S1; sample 2 when S1 == S0, else 0), B
(T0's memory note). D makes no call: it reuses C's decision and, for ``issue_refund`` only,
passes it through the gate against a reader over S1. Each arm executes on its own copy of S1.
"""

from __future__ import annotations

from collections.abc import Sequence

from srg.canonical import digest
from srg.contracts import (
    ARM_SPECS,
    RULE_SPECS,
    SCHEMA_VERSION,
    AgentDecision,
    ArmRun,
    DecisionProvider,
    DecisionRequest,
    DecisionResult,
    Expected,
    LlmMode,
    RunArtifact,
    RunInfo,
    Scenario,
    ScenarioRun,
    T0Eval,
    T0Run,
    WorldState,
    compute_run_id,
    same_decision,
)
from srg.gate import evaluate_gate
from srg.ledger import execute
from srg.outcome import outcome_for, summarize
from srg.scenarios import check_p8_stable
from srg.state import StateReader, apply_patch, diff_states

T1_CONTEXT = {spec.id: spec.t1_context for spec in ARM_SPECS}
DEFAULT_CURRENCY = "USD"


def arm_a_sample(s0: WorldState, s1: WorldState) -> int:
    """C2: when S1 equals S0, A's request equals T0's, so it must use a fresh sample (2)."""
    return 2 if digest(s1) == digest(s0) else 0


def t0_eval(decision: AgentDecision, exp: Expected) -> T0Eval:
    """T0 passes on the expected action and, for ``issue_refund``, the expected amount."""
    passed = decision.action == exp.t0_action and (
        exp.t0_action != "issue_refund" or decision.amount_minor == exp.t0_amount_minor
    )
    return T0Eval(
        passed=passed,
        expected_action=exp.t0_action,
        expected_amount_minor=exp.t0_amount_minor,
    )


def run_scenario(
    scenario: Scenario, provider: DecisionProvider, *, timed: bool = False
) -> ScenarioRun:
    s0 = scenario.t0.state
    s1 = apply_patch(s0, scenario.mutation.ops)
    check_p8_stable(scenario, s1)  # review round 3, finding 2: the gate has no P8 rule
    ticket = scenario.ticket
    at = scenario.mutation.at

    def ask(
        arm: str, sample: int, *, state: WorldState | None = None, note: str | None = None
    ) -> DecisionResult:
        req = DecisionRequest(
            label=f"{scenario.id}/{arm}",
            ticket=ticket,
            context_kind="state" if state is not None else "memory_note",
            state=state,
            memory_note=note,
            sample=sample,
        )
        return provider.decide(req)

    t0 = ask("t0", 0, state=s0)
    c = ask("C", 1, state=s0)
    a = ask("A", arm_a_sample(s0, s1), state=s1)
    b = ask("B", 0, note=t0.decision.memory_note)

    exp = scenario.expected
    t0_run = T0Run(llm=t0.llm, decision=t0.decision, eval=t0_eval(t0.decision, exp))

    arms: dict[str, ArmRun] = {}
    for arm, result in (("A", a), ("B", b), ("C", c), ("D", None)):
        decision = (result or c).decision
        gate = None
        if arm == "D" and decision.action == "issue_refund":
            gate = evaluate_gate(decision, StateReader(s1, at), timed=timed)
        allowed = gate is None or gate.verdict == "allow"
        execution = execute(decision, s1, arm=arm, at=at, allowed=allowed)
        arms[arm] = ArmRun(
            arm=arm,
            t1_context=T1_CONTEXT[arm],
            memory_note=t0.decision.memory_note if arm == "B" else None,
            llm=result.llm if result is not None else None,
            decision=decision,
            decision_changed_vs_t0=not same_decision(decision, t0.decision),
            gate=gate,
            execution=execution,
            outcome=outcome_for(decision, execution, gate, exp),
        )
    return ScenarioRun(
        scenario=scenario,
        t1_state=s1,
        state_diff=diff_states(s0, s1),
        t0=t0_run,
        arms=arms,
    )


def nonconforming_arms(run: ScenarioRun) -> list[str]:
    """Arms that executed a refund whose amount or currency differs from the ticket's request.

    The §5.6 classification only asks whether an arm paid when it should have; this reports what
    it paid. Gate rule ``amount_matches_request`` stops such a payment in arm D.
    """
    t = run.scenario.ticket
    return [
        arm_id
        for arm_id, arm in run.arms.items()
        if arm.execution.refund is not None
        and (arm.execution.refund.amount_minor, arm.execution.refund.currency)
        != (t.requested_minor, t.currency)
    ]


_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def format_money(amount_minor: int, currency: str) -> str:
    """Minor units as currency for operators: ``1, USD`` -> ``$0.01``; ``5800, EUR`` -> ``€58.00``.

    Currencies without a symbol here print as ``58.00 CHF`` (two decimal places assumed, as in
    every scenario; the run rejects mixed currencies).
    """
    sign = "-" if amount_minor < 0 else ""
    value = f"{abs(amount_minor) // 100:,}.{abs(amount_minor) % 100:02d}"
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{sign}{symbol}{value}" if symbol else f"{sign}{value} {currency}"


def nonconforming_notes(runs: Sequence[ScenarioRun]) -> list[str]:
    """One line per executed refund that does not match the ticket's request."""
    notes: list[str] = []
    for run in runs:
        t = run.scenario.ticket
        for arm_id in nonconforming_arms(run):
            refund = run.arms[arm_id].execution.refund
            assert refund is not None
            notes.append(
                f"{run.scenario.id}/{arm_id}: paid "
                f"{format_money(refund.amount_minor, refund.currency)}, ticket {t.ticket_id} "
                f"requested {format_money(t.requested_minor, t.currency)} (nonconforming amount)"
            )
    return notes


def request_hashes(runs: Sequence[ScenarioRun]) -> list[str]:
    """Every request hash used by the run (T0 and arms with a call)."""
    hashes = [r.t0.llm.request_hash for r in runs]
    hashes += [arm.llm.request_hash for r in runs for arm in r.arms.values() if arm.llm]
    return hashes


def decision_currencies(runs: Sequence[ScenarioRun]) -> set[str]:
    """Every currency named by a refund decision in the run (T0 and all arms)."""
    return {
        d.currency
        for r in runs
        for d in [r.t0.decision, *(arm.decision for arm in r.arms.values())]
        if d.currency is not None
    }


def run_currency(runs: Sequence[ScenarioRun]) -> str:
    """The one currency all refund actions share; USD when there are none or they disagree.

    ``verify_artifact`` fails a run whose refund currencies disagree, so the USD fallback never
    labels a mixed-currency run as verified.
    """
    currencies = decision_currencies(runs)
    return currencies.pop() if len(currencies) == 1 else DEFAULT_CURRENCY


def run_scenarios(
    scenarios: Sequence[Scenario],
    provider: DecisionProvider,
    *,
    llm_mode: LlmMode = "replay",
    model: str,
    timed: bool = False,
) -> RunArtifact:
    runs = [run_scenario(s, provider, timed=timed) for s in scenarios]
    latest = getattr(provider, "latest_recorded_at", None)  # C4
    recorded_at = latest() if callable(latest) else None
    return RunArtifact(
        schema_version=SCHEMA_VERSION,
        run=RunInfo(
            run_id=compute_run_id(scenarios, request_hashes(runs), model),
            llm_mode=llm_mode,
            model=model,
            recorded_at=recorded_at,
            currency=run_currency(runs),
            timings_included=timed,
        ),
        arms=ARM_SPECS,
        rules=RULE_SPECS,
        scenarios=runs,
        summary=summarize(runs),
    )


# ---------------------------------------------------------------------------
# verify (C9): recompute everything derivable and compare with the artifact
# ---------------------------------------------------------------------------


def _gate_core(gate) -> dict:
    """A gate verdict without its timing, for recompute comparisons."""
    return gate.model_dump(mode="json", exclude={"latency_us"})


def _arm_input_problems(sid: str, run: ScenarioRun, *, unchanged: bool) -> list[str]:
    """C2/C8 arm inputs: which request each arm sent, and which memory note it carried.

    Hashes are checked structurally (they depend on the provider's request body): C and, when
    S1 equals S0, A must reuse T0's request; every other call is a different request, so no arm
    may carry another call's hash. B carries T0's memory note; no other arm carries one.
    """
    problems: list[str] = []
    t0_hash = run.t0.llm.request_hash
    a, b = run.arms["A"], run.arms["B"]
    if a.llm is not None:
        if unchanged and (a.llm.request_hash, a.llm.sample) != (t0_hash, 2):
            problems.append(f"{sid}/A: state unchanged, request must equal T0's with sample 2")
        if not unchanged and a.llm.request_hash == t0_hash:
            problems.append(f"{sid}/A: state changed, request must differ from T0's")
    if b.llm is not None and b.llm.request_hash == t0_hash:
        problems.append(f"{sid}/B: memory-note request must differ from T0's")
    if a.llm is not None and b.llm is not None and a.llm.request_hash == b.llm.request_hash:
        problems.append(f"{sid}/A and B: arms carry the same request hash")
    if b.memory_note != run.t0.decision.memory_note:
        problems.append(f"{sid}/B: memory_note must equal T0's decision.memory_note")
    for arm_id in ("A", "C", "D"):
        if run.arms[arm_id].memory_note is not None:
            problems.append(f"{sid}/{arm_id}: memory_note must be null (only B carries one)")
    return problems


def verify_artifact(artifact: RunArtifact) -> tuple[list[str], list[str], list[str]]:
    """Return ``(violations, true_regressions, nonconforming)``. Any violation fails ``verify``.

    True regressions (C's frozen-context decision differs from T0) and nonconforming payments (an
    executed refund whose amount or currency differs from the ticket's request) are reported,
    not failures. In arm D such a payment is also a violation: the gate recompute blocks it.
    """
    problems: list[str] = []
    regressions: list[str] = []
    for run in artifact.scenarios:
        sc = run.scenario
        sid = sc.id
        exp = sc.expected
        s1 = apply_patch(sc.t0.state, sc.mutation.ops)
        if run.t1_state != s1:
            problems.append(f"{sid}: t1_state does not equal T0 state + mutation ops")
        if run.state_diff != diff_states(sc.t0.state, s1):
            problems.append(f"{sid}: state_diff does not recompute")
        try:
            check_p8_stable(sc, s1)
        except ValueError as exc:
            problems.append(f"{sid}: {exc}")
        if sorted(run.arms) != ["A", "B", "C", "D"]:
            problems.append(f"{sid}: expected four arms, got {sorted(run.arms)}")
            continue
        t0 = run.t0
        want_eval = t0_eval(t0.decision, exp)
        if t0.eval != want_eval:
            problems.append(
                f"{sid}: t0.eval {t0.eval.model_dump(mode='json')} does not recompute to "
                f"{want_eval.model_dump(mode='json')}"
            )
        c, d = run.arms["C"], run.arms["D"]
        for arm_id, arm in run.arms.items():
            where = f"{sid}/{arm_id}"
            if arm.t1_context != T1_CONTEXT[arm_id]:
                problems.append(f"{where}: t1_context {arm.t1_context!r}")
            changed = not same_decision(arm.decision, t0.decision)
            if arm.decision_changed_vs_t0 != changed:
                problems.append(f"{where}: decision_changed_vs_t0 should be {changed}")
            if arm_id != "D" and arm.gate is not None:
                problems.append(f"{where}: only arm D has a gate")
            if arm_id != "D" and arm.llm is None:
                problems.append(f"{where}: missing llm call")
            gate = arm.gate if arm_id == "D" else None
            allowed = gate is None or gate.verdict == "allow"
            want_exec = execute(arm.decision, s1, arm=arm_id, at=sc.mutation.at, allowed=allowed)
            if arm.execution != want_exec:
                problems.append(f"{where}: execution does not recompute")
            try:
                want = outcome_for(arm.decision, arm.execution, gate, exp)
            except ValueError as exc:
                problems.append(f"{where}: {exc}")
                continue
            for field in ("disposition", "classification", "harm"):
                got, should = getattr(arm.outcome, field), getattr(want, field)
                if got != should:
                    problems.append(f"{where}: outcome.{field} is {got!r}, should be {should!r}")
        if d.llm is not None:
            problems.append(f"{sid}/D: arm D must make no LLM call")
        if d.decision != c.decision:
            problems.append(f"{sid}/D: decision is not C's decision")
        is_refund = d.decision.action == "issue_refund"
        if (d.gate is not None) != is_refund:
            problems.append(f"{sid}/D: gate must be present iff the decision is issue_refund")
        if d.gate is not None:
            fresh = evaluate_gate(d.decision, StateReader(s1, sc.mutation.at))
            if _gate_core(d.gate) != _gate_core(fresh):
                problems.append(f"{sid}/D: gate verdict does not recompute from t1_state")
        if c.llm is not None and (
            c.llm.request_hash != t0.llm.request_hash or c.llm.sample != 1
        ):
            problems.append(f"{sid}/C: request must equal T0's with sample 1")
        # C2 sample assignments: T0 0, C 1 (above), A 2 when S1 equals S0 else 0, B 0.
        if t0.llm.sample != 0:
            problems.append(f"{sid}/t0: sample is {t0.llm.sample}, should be 0")
        a, b = run.arms["A"], run.arms["B"]
        want_a = arm_a_sample(sc.t0.state, s1)
        if a.llm is not None and a.llm.sample != want_a:
            problems.append(f"{sid}/A: sample is {a.llm.sample}, should be {want_a} (C2)")
        if b.llm is not None and b.llm.sample != 0:
            problems.append(f"{sid}/B: sample is {b.llm.sample}, should be 0")
        problems += _arm_input_problems(sid, run, unchanged=want_a == 2)

        if not same_decision(c.decision, t0.decision):
            regressions.append(
                f"{sid}: C decided {c.decision.action} on frozen state, T0 decided "
                f"{t0.decision.action} (true regression)"
            )
            continue
        got_gate = (d.gate.verdict, d.gate.failed_rules) if d.gate else None
        want_gate = (exp.gate.verdict, exp.gate.failed_rules) if exp.gate else None
        if got_gate != want_gate:
            problems.append(f"{sid}/D: gate {got_gate} != expected {want_gate}")
        if c.outcome.classification != exp.arm_c:
            problems.append(f"{sid}/C: {c.outcome.classification} != expected {exp.arm_c}")
        if d.outcome.classification != exp.arm_d:
            problems.append(f"{sid}/D: {d.outcome.classification} != expected {exp.arm_d}")

    currencies = decision_currencies(artifact.scenarios)
    if len(currencies) > 1:
        problems.append(f"refund decisions disagree on currency: {sorted(currencies)}")
    want_currency = run_currency(artifact.scenarios)
    if artifact.run.currency != want_currency:
        problems.append(
            f"run.currency {artifact.run.currency!r} != recomputed {want_currency!r} "
            "(the currency of the run's refunds)"
        )
    if artifact.summary != summarize(artifact.scenarios):
        problems.append("summary does not recompute from the scenarios")
    if artifact.summary.t0_eval_passed != artifact.summary.t0_eval_total:
        problems.append(
            f"T0 eval {artifact.summary.t0_eval_passed}/{artifact.summary.t0_eval_total}"
        )
    run_id = compute_run_id(
        [r.scenario for r in artifact.scenarios],
        request_hashes(artifact.scenarios),
        artifact.run.model,
    )
    if artifact.run.run_id != run_id:
        problems.append(f"run_id {artifact.run.run_id} != recomputed {run_id}")
    if artifact.arms != ARM_SPECS:
        problems.append("arms catalogue differs from ARM_SPECS")
    if artifact.rules != RULE_SPECS:
        problems.append("rules catalogue differs from RULE_SPECS")
    return problems, regressions, nonconforming_notes(artifact.scenarios)
