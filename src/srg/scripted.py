"""Scripted DecisionProvider for tests and fixtures. Its outputs are never model outputs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from srg.canonical import digest
from srg.contracts import (
    AgentDecision,
    DecisionRequest,
    DecisionResult,
    LlmCallRef,
    Scenario,
)

SCRIPTED_MODEL = "fixture-scripted"
FIXTURE_PREFIX = "FIXTURE: scripted, not a model output."


def scripted_request_hash(req: DecisionRequest) -> str:
    """Hash of the request without label and sample, so C's hash equals T0's (as with the agent)."""
    return digest(req.model_dump(mode="json", exclude={"label", "sample"}))


class ScriptedProvider:
    """A DecisionProvider that returns a fixed decision per request label."""

    model = SCRIPTED_MODEL

    def __init__(self, decisions: Mapping[str, AgentDecision]) -> None:
        self.decisions = dict(decisions)

    def decide(self, req: DecisionRequest) -> DecisionResult:
        try:
            decision = self.decisions[req.label]
        except KeyError:
            raise KeyError(f"ScriptedProvider has no decision for label {req.label!r}") from None
        llm = LlmCallRef(
            request_hash=scripted_request_hash(req),
            sample=req.sample,
            model=SCRIPTED_MODEL,
            latency_ms=0,
            prompt_tokens=None,
            completion_tokens=None,
        )
        return DecisionResult(decision=decision, llm=llm)

    @classmethod
    def from_expected(cls, scenarios: Iterable[Scenario]) -> ScriptedProvider:
        """Labels ``<id>/t0|C|A|B``. T0 = C = B = the expected T0 decision.

        A = issue_refund if t1_should_pay or kind gate_blind_spot; escalate_to_human if the
        mutation lowered the auto-approve limit; otherwise deny_refund.
        """
        decisions: dict[str, AgentDecision] = {}
        for s in scenarios:
            exp = s.expected
            t0 = _decision(s, exp.t0_action, f"expected T0 action for {s.id}")
            for arm in ("t0", "C", "B"):
                decisions[f"{s.id}/{arm}"] = t0
            if exp.t1_should_pay or s.kind == "gate_blind_spot":
                a_action = "issue_refund"
            elif s.mutation.kind == "auto_approve_limit_lowered":
                a_action = "escalate_to_human"
            else:
                a_action = "deny_refund"
            decisions[f"{s.id}/A"] = _decision(
                s, a_action, f"scripted live-state action for {s.id}"
            )
        return cls(decisions)


def _decision(s: Scenario, action: str, reason: str) -> AgentDecision:
    ticket = s.ticket
    refund = action == "issue_refund"
    return AgentDecision(
        action=action,
        order_id=ticket.order_id,
        ticket_id=ticket.ticket_id,
        amount_minor=ticket.requested_minor if refund else None,  # C8
        currency=ticket.currency if refund else None,
        reason=reason,
        rationale=f"{FIXTURE_PREFIX} {action} on ticket {ticket.ticket_id}.",
        memory_note=f"{FIXTURE_PREFIX} Ticket {ticket.ticket_id}: {action}.",
    )
