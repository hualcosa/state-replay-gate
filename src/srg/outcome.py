"""Outcome classification and run summary.

Everything here is computed from execution results and ``expected.t1_should_pay``; nothing is
hand-entered.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from srg.contracts import (
    ARM_IDS,
    AgentDecision,
    ArmSummary,
    Classification,
    Disposition,
    Execution,
    Expected,
    GateVerdict,
    Harm,
    Outcome,
    RegressionSummary,
    ScenarioRun,
    Summary,
)

NO_HARM = Harm(type="none", amount_minor=0, explanation="")


def disposition_for(
    decision: AgentDecision, execution: Execution, gate: GateVerdict | None
) -> Disposition:
    if execution.executed:
        return "paid"
    if gate is not None and gate.verdict == "block":
        return "blocked"
    if gate is not None and gate.verdict == "review":
        return "held_for_review"
    if decision.action == "deny_refund":
        return "denied"
    if decision.action == "escalate_to_human":
        return "escalated"
    raise ValueError(
        f"issue_refund was neither executed nor stopped by a gate (gate={gate and gate.verdict})"
    )


def classify(paid: bool, should_pay: bool) -> Classification:
    if paid:
        return "correct_payment" if should_pay else "wrong_payment"
    return "false_stop" if should_pay else "correct_stop"


def outcome_for(
    decision: AgentDecision, execution: Execution, gate: GateVerdict | None, expected: Expected
) -> Outcome:
    """Disposition, classification and harm. Harm is the scenario's harm iff wrong_payment (C1)."""
    disposition = disposition_for(decision, execution, gate)
    classification = classify(disposition == "paid", expected.t1_should_pay)
    harm = expected.harm_if_paid if classification == "wrong_payment" else NO_HARM
    return Outcome(disposition=disposition, classification=classification, harm=harm)


def nearest_rank(values: Sequence[int], pct: int) -> int | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(pct / 100 * len(ordered))) - 1]


def summarize(runs: Sequence[ScenarioRun]) -> Summary:
    by_arm: dict[str, ArmSummary] = {}
    for arm in ARM_IDS:
        arm_runs = [r.arms[arm] for r in runs]
        cls = [x.outcome.classification for x in arm_runs]
        calls = [x.llm for x in arm_runs if x.llm is not None]
        has_usage = bool(calls) and all(
            c.prompt_tokens is not None and c.completion_tokens is not None for c in calls
        )
        latencies = [c.latency_ms for c in calls]
        by_arm[arm] = ArmSummary(
            scenarios=len(arm_runs),
            correct_payments=cls.count("correct_payment"),
            wrong_payments=cls.count("wrong_payment"),
            wrong_payment_minor=sum(x.outcome.harm.amount_minor for x in arm_runs),  # C1
            correct_stops=cls.count("correct_stop"),
            false_stops=cls.count("false_stop"),
            review_load=sum(
                x.outcome.disposition in ("escalated", "held_for_review") for x in arm_runs
            ),
            decisions_changed_vs_t0=sum(x.decision_changed_vs_t0 for x in arm_runs),
            llm_calls_at_t1=len(calls),
            prompt_tokens_at_t1=sum(c.prompt_tokens for c in calls) if has_usage else None,
            completion_tokens_at_t1=sum(c.completion_tokens for c in calls) if has_usage else None,
            llm_latency_ms_p50=nearest_rank(latencies, 50),
            llm_latency_ms_p95=nearest_rank(latencies, 95),
        )
    return Summary(
        t0_eval_passed=sum(r.t0.eval.passed for r in runs),
        t0_eval_total=len(runs),
        by_arm=by_arm,
        regressions=_regressions(runs),
    )


def _regressions(runs: Sequence[ScenarioRun]) -> RegressionSummary:
    """All three counts share one population: scenarios whose state changed (non-empty net
    ``state_diff``, the same test that gives A sample 2), so a no-op patch counts as unchanged.

    A decision that changed on an unchanged control is sampling variance, not a reaction to a
    changed world, so it is not counted on either side of the live/frozen split.
    """
    changed = [r for r in runs if r.state_diff]
    return RegressionSummary(
        scenarios_with_state_change=len(changed),
        changed_on_live_state=sum(r.arms["A"].decision_changed_vs_t0 for r in changed),
        changed_on_frozen_state=sum(r.arms["C"].decision_changed_vs_t0 for r in changed),
    )
