"""Deterministic refund gate.

The gate sees only the agent's proposed action and a read-only view of the live business state.
Every rule reads through the ``StateReader``, so each ``RuleResult.reads`` lists exactly the
JSON Pointers that rule looked at. Rule ids, order and severities match ``RULE_SPECS``.

Binding (fail closed): rules on order/payment data fail when the action's order is not the
order in live state (``observed.order_matches``); ticket rules fail when the action's ticket is
missing from live state or belongs to another order (``ticket_found``, ``ticket_order_matches``);
``amount_matches_request`` fails unless the action's amount and currency equal the ticket's request.
``no_duplicate_refund`` also fails when the action's own ticket is already refunded or a refund
row already carries its ticket id.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from srg.contracts import (
    RULE_SPECS,
    AgentDecision,
    GateVerdict,
    Rule,
    RuleResult,
    Severity,
    verdict_for,
)
from srg.state import StateReader

_SPECS = {spec.id: spec for spec in RULE_SPECS}

CheckFn = Callable[[AgentDecision, StateReader], tuple[bool, dict[str, Any], str]]


def _fmt_money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100:,}.{abs(minor) % 100:02d}"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _order_binding(action: AgentDecision, state: StateReader) -> tuple[bool, str]:
    """Whether the action targets the order in live state; a mismatch fails the rule closed."""
    state_order = state.read("/order/order_id")
    return state_order == action.order_id, state_order


def _order_message(ok: bool, state_order: str, action: AgentDecision, message: str) -> str:
    if ok:
        return message
    return f"order mismatch: action {action.order_id} != live order {state_order}; {message}"


def _ticket_matches(action: AgentDecision, state: StateReader) -> int:
    return sum(1 for t in state.read("/tickets") if t["ticket_id"] == action.ticket_id)


def _own_ticket(action: AgentDecision, state: StateReader) -> tuple[dict | None, bool, bool]:
    """The action's ticket in live state, whether it was found, whether its order matches.

    Fails closed unless exactly one live row carries the action's ticket id (review round 3,
    finding 1): with several, which row binds would depend on list order.
    """
    matches = [t for t in state.read("/tickets") if t["ticket_id"] == action.ticket_id]
    if len(matches) != 1:
        return None, False, False
    ticket = matches[0]
    return ticket, True, ticket["order_id"] == action.order_id


def _ticket_problem(
    action: AgentDecision, found: bool, order_matches: bool, state: StateReader
) -> str | None:
    if not found:
        count = _ticket_matches(action, state)
        if count > 1:
            return f"ticket {action.ticket_id} is ambiguous: {count} rows in live state"
        return f"ticket {action.ticket_id} not found in live state"
    if not order_matches:
        return f"ticket {action.ticket_id} belongs to another order than {action.order_id}"
    return None


class FunctionRule:
    """A gate rule: catalogue metadata from ``RULE_SPECS`` plus a pure check function."""

    def __init__(self, rule_id: str, check: CheckFn) -> None:
        spec = _SPECS[rule_id]
        self.id: str = spec.id
        self.description: str = spec.description
        self.severity: Severity = spec.severity
        self._check = check

    def check(self, action: AgentDecision, state: StateReader) -> RuleResult:
        start = len(state.reads)
        passed, observed, message = self._check(action, state)
        reads: list[str] = []
        for r in state.reads[start:]:
            if r.path not in reads:
                reads.append(r.path)
        return RuleResult(
            rule_id=self.id,
            severity=self.severity,
            passed=passed,
            observed=observed,
            message=message,
            reads=reads,
        )

    def __repr__(self) -> str:
        return f"FunctionRule({self.id!r})"


# ---------------------------------------------------------------------------
# Rule checks, in RULE_SPECS order
# ---------------------------------------------------------------------------


def _payment_captured(action: AgentDecision, state: StateReader):
    order_ok, state_order = _order_binding(action, state)
    status = state.read("/order/payment/status")
    return (
        order_ok and status == "captured",
        {"payment_status": status, "order_matches": order_ok},
        _order_message(order_ok, state_order, action, f"payment_status = {status}"),
    )


def _customer_not_fraud_flagged(action: AgentDecision, state: StateReader):
    flag = bool(state.read("/customer/fraud_flag"))
    reason = state.read("/customer/fraud_reason")
    message = f"fraud_flag = {_bool(flag)}" + (f" ({reason})" if flag and reason else "")
    return not flag, {"fraud_flag": flag, "fraud_reason": reason}, message


def _no_open_chargeback(action: AgentDecision, state: StateReader):
    ids = [
        d["dispute_id"]
        for d in state.read("/disputes")
        if d["order_id"] == action.order_id and d["type"] == "chargeback" and d["status"] == "open"
    ]
    is_open = bool(ids)
    message = f"chargeback_open = {_bool(is_open)}" + (f" ({', '.join(ids)})" if ids else "")
    return not is_open, {"chargeback_open": is_open, "dispute_ids": ids}, message


def _currency_matches_order(action: AgentDecision, state: StateReader):
    order_ok, state_order = _order_binding(action, state)
    payment_currency = state.read("/order/payment/currency")
    same = action.currency == payment_currency
    op = "==" if same else "!="
    return (
        order_ok and same,
        {
            "action_currency": action.currency,
            "payment_currency": payment_currency,
            "order_matches": order_ok,
        },
        _order_message(
            order_ok,
            state_order,
            action,
            f"action_currency {action.currency} {op} payment_currency {payment_currency}",
        ),
    )


def _amount_within_refundable_balance(action: AgentDecision, state: StateReader):
    order_ok, state_order = _order_binding(action, state)
    captured = int(state.read("/order/payment/captured_minor"))
    refunded = sum(
        int(r["amount_minor"]) for r in state.read("/refunds") if r["order_id"] == action.order_id
    )
    remaining = captured - refunded
    requested = int(action.amount_minor or 0)
    within = requested <= remaining
    op = "<=" if within else ">"
    return (
        order_ok and within,
        {
            "captured_minor": captured,
            "already_refunded_minor": refunded,
            "remaining_minor": remaining,
            "requested_minor": requested,
            "order_matches": order_ok,
        },
        _order_message(
            order_ok,
            state_order,
            action,
            f"requested {_fmt_money(requested)} {op} remaining {_fmt_money(remaining)} "
            f"(captured {_fmt_money(captured)} - refunded {_fmt_money(refunded)})",
        ),
    )


def _amount_matches_request(action: AgentDecision, state: StateReader):
    ticket, found, order_matches = _own_ticket(action, state)
    requested = ticket["requested_minor"] if ticket else None
    requested_currency = ticket["currency"] if ticket else None
    amount = action.amount_minor
    observed = {
        "requested_minor": requested,
        "requested_currency": requested_currency,
        "action_amount_minor": amount,
        "action_currency": action.currency,
        "ticket_found": found,
    }
    binding = _ticket_problem(action, found, order_matches, state)
    if binding:
        return False, observed, binding
    same = amount == requested and action.currency == requested_currency
    op = "==" if same else "≠"
    message = (
        f"refund {_fmt_money(int(amount or 0))} {action.currency} {op} requested "
        f"{_fmt_money(int(requested))} {requested_currency} ({action.ticket_id})"
    )
    return same, observed, message


def _no_duplicate_refund(action: AgentDecision, state: StateReader):
    ticket, found, order_matches = _own_ticket(action, state)
    reason = ticket["reason_code"] if ticket else None
    dupes = []
    if ticket is not None:
        dupes = [
            t["ticket_id"]
            for t in state.read("/tickets")
            if t["ticket_id"] != action.ticket_id
            and t["order_id"] == action.order_id
            and t["reason_code"] == reason
            and t["status"] == "resolved"
            and t["resolution"] == "refunded"
        ]
    own_refunded = bool(
        ticket and ticket["status"] == "resolved" and ticket["resolution"] == "refunded"
    )
    own_refund_ids = [
        r["refund_id"] for r in state.read("/refunds") if r["ticket_id"] == action.ticket_id
    ]
    observed = {
        "duplicate_ticket_ids": dupes,
        "ticket_found": found,
        "ticket_order_matches": order_matches,
        "own_ticket_refunded": own_refunded,
        "own_refund_ids": own_refund_ids,
    }
    problems = []
    binding = _ticket_problem(action, found, order_matches, state)
    if binding:
        problems.append(binding)
    if own_refunded:
        problems.append(f"ticket {action.ticket_id} is already resolved as refunded")
    if own_refund_ids:
        problems.append(
            f"refund {', '.join(own_refund_ids)} already issued for ticket {action.ticket_id}"
        )
    if dupes:
        problems.append(f"already refunded on {', '.join(dupes)} ({reason})")
    message = "; ".join(problems) if problems else f"no other refunded ticket for {reason}"
    return not problems, observed, message


_RETURN_OK = ("in_transit", "received")


def _return_eligible(action: AgentDecision, state: StateReader):
    ticket, found, order_matches = _own_ticket(action, state)
    reason = ticket["reason_code"] if ticket else None
    status = state.read("/return_info/status")
    # P7: a rejected return is never refundable, even for a damaged item.
    eligible = status != "rejected" and (
        reason == "damaged" or (reason is not None and status in _RETURN_OK)
    )
    binding = _ticket_problem(action, found, order_matches, state)
    if binding:
        message = binding
    elif reason == "damaged" and status == "rejected":
        message = "reason_code = damaged, but return_status = rejected is never refundable"
    elif reason == "damaged":
        message = f"reason_code = damaged, no return needed (return_status = {status})"
    else:
        message = f"reason_code = {reason}, return_status = {status}"
    return (
        binding is None and eligible,
        {
            "reason_code": reason,
            "return_status": status,
            "ticket_found": found,
            "ticket_order_matches": order_matches,
        },
        message,
    )


def _within_auto_approve_limit(action: AgentDecision, state: StateReader):
    limit = int(state.read("/policy/auto_approve_limit_minor"))
    version = state.read("/policy/version")
    requested = int(action.amount_minor or 0)
    passed = requested <= limit
    op = "<=" if passed else ">"
    return (
        passed,
        {"limit_minor": limit, "requested_minor": requested, "policy_version": version},
        f"requested {_fmt_money(requested)} {op} limit {_fmt_money(limit)} (policy v{version})",
    )


DEFAULT_RULES: tuple[Rule, ...] = (
    FunctionRule("payment_captured", _payment_captured),
    FunctionRule("customer_not_fraud_flagged", _customer_not_fraud_flagged),
    FunctionRule("no_open_chargeback", _no_open_chargeback),
    FunctionRule("currency_matches_order", _currency_matches_order),
    FunctionRule("amount_within_refundable_balance", _amount_within_refundable_balance),
    FunctionRule("amount_matches_request", _amount_matches_request),
    FunctionRule("no_duplicate_refund", _no_duplicate_refund),
    FunctionRule("return_eligible", _return_eligible),
    FunctionRule("within_auto_approve_limit", _within_auto_approve_limit),
)


def evaluate_gate(
    action: AgentDecision,
    reader: StateReader,
    rules: Sequence[Rule] = DEFAULT_RULES,
    *,
    timed: bool = False,
) -> GateVerdict:
    """Run every rule against live state; block > review > allow. Only for ``issue_refund``."""
    if action.action != "issue_refund":
        raise ValueError(f"the gate only checks issue_refund, got {action.action}")
    start = time.perf_counter_ns() if timed else 0
    results = [rule.check(action, reader) for rule in rules]
    failed = sorted({r.rule_id for r in results if not r.passed})
    latency_us = (time.perf_counter_ns() - start) // 1000 if timed else None
    return GateVerdict(
        verdict=verdict_for(failed),
        checked_at=reader.at,
        state_digest=reader.state_digest,
        results=results,
        failed_rules=failed,
        latency_us=latency_us,
    )
