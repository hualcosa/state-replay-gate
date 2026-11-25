"""Simulated refund ledger. Each arm writes to its own deep copy of the T1 state."""

from __future__ import annotations

from srg.contracts import AgentDecision, Execution, Refund, WorldState


def execute_on(
    decision: AgentDecision, state: WorldState, *, arm: str, at: str, allowed: bool
) -> tuple[Execution, WorldState]:
    """Like :func:`execute`, also returning the arm's post-write state (a private copy)."""
    own = state.model_copy(deep=True)
    refund: Refund | None = None
    if decision.action == "issue_refund" and allowed:
        assert decision.amount_minor is not None and decision.currency is not None
        refund = Refund(
            refund_id=f"rf-{arm.lower()}-{decision.ticket_id}",
            order_id=decision.order_id,
            ticket_id=decision.ticket_id,
            amount_minor=decision.amount_minor,
            currency=decision.currency,
            issued_at=at,
            issued_by=f"refund-agent/arm-{arm}",
            note=decision.reason,
        )
        own.refunds.append(refund)
    total = sum(r.amount_minor for r in own.refunds if r.order_id == decision.order_id)
    execution = Execution(executed=refund is not None, refund=refund, total_refunded_minor=total)
    return execution, own


def execute(
    decision: AgentDecision, state: WorldState, *, arm: str, at: str, allowed: bool
) -> Execution:
    """Run the decision against a deep copy of ``state``; ``state`` itself is never changed.

    A refund is written only for ``issue_refund`` when ``allowed`` (no gate, or gate allow).
    """
    return execute_on(decision, state, arm=arm, at=at, allowed=allowed)[0]
