"""Independent evaluator of refund policy v1 (P1-P9), written for the scenario tests only."""

from __future__ import annotations

from datetime import datetime

from srg.contracts import Ticket, WorldState

# P1-P7 and P9 map to the gate rule ids of §5.5; P8 has no rule.
CLAUSE_RULE = {
    "P1": "payment_captured",
    "P2": "customer_not_fraud_flagged",
    "P3": "no_open_chargeback",
    "P4": "currency_matches_order",
    "P5": "amount_within_refundable_balance",
    "P6": "no_duplicate_refund",
    "P7": "return_eligible",
    "P9": "within_auto_approve_limit",
}


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def policy(ticket: Ticket, state: WorldState) -> tuple[str, list[str]]:
    """Return (action, failed clauses) for refunding exactly ``ticket.requested_minor``."""
    order = state.order
    failed: list[str] = []
    if order.payment.status != "captured":
        failed.append("P1")
    if state.customer.fraud_flag:
        failed.append("P2")
    if any(d.order_id == order.order_id and d.type == "chargeback" and d.status == "open"
           for d in state.disputes):
        failed.append("P3")
    if ticket.currency != order.payment.currency:
        failed.append("P4")
    already = sum(r.amount_minor for r in state.refunds if r.order_id == order.order_id)
    if ticket.requested_minor > order.payment.captured_minor - already:
        failed.append("P5")
    if any(t.ticket_id != ticket.ticket_id and t.order_id == ticket.order_id
           and t.reason_code == ticket.reason_code
           and t.status == "resolved" and t.resolution == "refunded" for t in state.tickets):
        failed.append("P6")
    returned = state.return_info.status in ("in_transit", "received")
    if state.return_info.status == "rejected" or (ticket.reason_code != "damaged" and not returned):
        failed.append("P7")
    assert order.delivered_at is not None
    if (_ts(ticket.opened_at) - _ts(order.delivered_at)).days > state.policy.refund_window_days:
        failed.append("P8")
    if failed:
        return "deny_refund", failed
    if ticket.requested_minor > state.policy.auto_approve_limit_minor:
        return "escalate_to_human", ["P9"]
    return "issue_refund", []


def failed_rules(clauses: list[str]) -> list[str]:
    """Gate rule ids behind the failed clauses (P8 has none; P9 is the review rule)."""
    return sorted(CLAUSE_RULE[c] for c in clauses if c in CLAUSE_RULE)
