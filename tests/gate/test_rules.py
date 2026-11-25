"""Gate rules (§5.5): each rule on F01's S0 and on a state built to break it, plus edge cases."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from srg.canonical import digest
from srg.contracts import RULE_SPECS, AgentDecision, PatchOp, RuleSpec, WorldState
from srg.gate import DEFAULT_RULES, evaluate_gate
from srg.scenarios import load_scenario
from srg.state import StateReader, apply_patch

AT = "2026-10-12T14:00:00Z"
ORDER = "ORD-F10"
TICKET = "TCK-F11"


@pytest.fixture(scope="module")
def f01(fixture_scenarios_dir: Path):
    return load_scenario(fixture_scenarios_dir / "F01-hero-chargeback.json")


@pytest.fixture(scope="module")
def s0(f01) -> WorldState:
    return f01.t0.state


def refund_action(amount: int = 25000, currency: str = "USD", **kw) -> AgentDecision:
    return AgentDecision(
        action="issue_refund",
        order_id=kw.get("order_id", ORDER),
        ticket_id=kw.get("ticket_id", TICKET),
        amount_minor=amount,
        currency=currency,
        reason="test",
        rationale="test",
        memory_note="test",
    )


def patched(state: WorldState, *ops: dict) -> WorldState:
    return apply_patch(state, [PatchOp(**op) for op in ops])


def gate(state: WorldState, action: AgentDecision | None = None, **kw):
    return evaluate_gate(action or refund_action(), StateReader(state, AT), **kw)


def dispute(order_id: str = ORDER, status: str = "open", dispute_id: str = "DSP-T1") -> dict:
    return {
        "dispute_id": dispute_id,
        "order_id": order_id,
        "type": "chargeback",
        "status": status,
        "amount_minor": 25000,
        "currency": "USD",
        "opened_at": "2026-10-12T13:00:00Z",
    }


def refund_row(amount: int, order_id: str = ORDER, refund_id: str = "RF-T1") -> dict:
    return {
        "refund_id": refund_id,
        "order_id": order_id,
        "ticket_id": None,
        "amount_minor": amount,
        "currency": "USD",
        "issued_at": "2026-10-12T12:00:00Z",
        "issued_by": "support",
        "note": None,
    }


def ticket_row(
    ticket_id: str = "TCK-OTHER",
    reason: str = "not_as_described",
    status: str = "resolved",
    resolution: str | None = "refunded",
    order_id: str = ORDER,
) -> dict:
    return {
        "ticket_id": ticket_id,
        "order_id": order_id,
        "channel": "chat",
        "reason_code": reason,
        "message": "same request by chat",
        "requested_minor": 25000,
        "currency": "USD",
        "opened_at": "2026-10-11T08:00:00Z",
        "status": status,
        "resolution": resolution,
    }


# One state per rule that breaks exactly that rule.
BREAKERS: dict[str, list[dict]] = {
    "payment_captured": [{"op": "replace", "path": "/order/payment/status", "value": "reversed"}],
    "customer_not_fraud_flagged": [
        {"op": "replace", "path": "/customer/fraud_flag", "value": True},
        {"op": "replace", "path": "/customer/fraud_reason", "value": "linked claims"},
    ],
    "no_open_chargeback": [{"op": "add", "path": "/disputes/-", "value": dispute()}],
    "currency_matches_order": [
        {"op": "replace", "path": "/order/payment/currency", "value": "CAD"}
    ],
    "amount_within_refundable_balance": [
        {"op": "add", "path": "/refunds/-", "value": refund_row(1)}
    ],
    "amount_matches_request": [
        {"op": "replace", "path": "/tickets/0/requested_minor", "value": 24999}
    ],
    "no_duplicate_refund": [{"op": "add", "path": "/tickets/-", "value": ticket_row()}],
    "return_eligible": [{"op": "replace", "path": "/return_info/status", "value": "rejected"}],
    "within_auto_approve_limit": [
        {"op": "replace", "path": "/policy/auto_approve_limit_minor", "value": 24999}
    ],
}

OBSERVED_KEYS = {
    "payment_captured": {"payment_status", "order_matches"},
    "customer_not_fraud_flagged": {"fraud_flag", "fraud_reason"},
    "no_open_chargeback": {"chargeback_open", "dispute_ids"},
    "currency_matches_order": {"action_currency", "payment_currency", "order_matches"},
    "amount_within_refundable_balance": {
        "captured_minor",
        "already_refunded_minor",
        "remaining_minor",
        "requested_minor",
        "order_matches",
    },
    "amount_matches_request": {
        "requested_minor",
        "requested_currency",
        "action_amount_minor",
        "action_currency",
        "ticket_found",
    },
    "no_duplicate_refund": {
        "duplicate_ticket_ids",
        "ticket_found",
        "ticket_order_matches",
        "own_ticket_refunded",
        "own_refund_ids",
    },
    "return_eligible": {"reason_code", "return_status", "ticket_found", "ticket_order_matches"},
    "within_auto_approve_limit": {"limit_minor", "requested_minor", "policy_version"},
}


def test_catalogue_matches_rule_specs():
    specs = [
        RuleSpec(id=r.id, description=r.description, severity=r.severity) for r in DEFAULT_RULES
    ]
    assert specs == RULE_SPECS
    assert set(BREAKERS) == {s.id for s in RULE_SPECS}


def test_all_rules_pass_on_f01_s0(s0):
    v = gate(s0)
    assert v.verdict == "allow"
    assert v.failed_rules == []
    assert [r.rule_id for r in v.results] == [s.id for s in RULE_SPECS]
    assert all(r.passed for r in v.results)
    for r in v.results:
        assert set(r.observed) == OBSERVED_KEYS[r.rule_id]
        assert r.reads, f"{r.rule_id} must record its reads"
        assert r.message


@pytest.mark.parametrize("rule_id", list(BREAKERS))
def test_each_rule_fails_on_its_breaking_state(s0, rule_id):
    v = gate(patched(s0, *BREAKERS[rule_id]))
    severity = next(s.severity for s in RULE_SPECS if s.id == rule_id)
    assert v.failed_rules == [rule_id]
    assert v.verdict == severity
    failed = next(r for r in v.results if r.rule_id == rule_id)
    assert not failed.passed
    assert set(failed.observed) == OBSERVED_KEYS[rule_id]


def test_live_read_allows_on_s0_blocks_on_s1(f01, s0):
    s1 = apply_patch(s0, f01.mutation.ops)
    assert gate(s0).verdict == "allow"
    v = gate(s1)
    assert v.verdict == "block"
    assert v.failed_rules == ["no_open_chargeback"]
    chargeback = v.results[2]
    assert chargeback.observed == {"chargeback_open": True, "dispute_ids": ["DSP-F011"]}
    assert chargeback.message == "chargeback_open = true (DSP-F011)"
    assert chargeback.reads == ["/disputes"]


def test_verdict_metadata(s0):
    reader = StateReader(s0, AT)
    v = evaluate_gate(refund_action(), reader)
    assert v.checked_at == AT
    assert v.state_digest == digest(s0)
    assert v.latency_us is None
    timed = gate(s0, timed=True)
    assert isinstance(timed.latency_us, int) and timed.latency_us >= 0


def asks(minor: int) -> dict:
    """Patch op: the action's ticket (index 0 in F01) requests ``minor``."""
    return {"op": "replace", "path": "/tickets/0/requested_minor", "value": minor}


def test_amount_equal_to_remaining_balance_passes_plus_one_fails(s0):
    state = patched(
        s0, {"op": "add", "path": "/refunds/-", "value": refund_row(5000)}, asks(20000)
    )
    ok = gate(state, refund_action(20000))
    assert ok.verdict == "allow"
    balance = ok.results[4].observed
    assert balance == {
        "captured_minor": 25000,
        "already_refunded_minor": 5000,
        "remaining_minor": 20000,
        "requested_minor": 20000,
        "order_matches": True,
    }
    over = gate(patched(state, asks(20001)), refund_action(20001))
    assert over.failed_rules == ["amount_within_refundable_balance"]


def test_amount_equal_to_limit_passes_plus_one_reviews(s0):
    state = patched(
        s0, {"op": "replace", "path": "/policy/auto_approve_limit_minor", "value": 25000}
    )
    assert gate(state, refund_action(25000)).verdict == "allow"
    lower = patched(
        s0, {"op": "replace", "path": "/policy/auto_approve_limit_minor", "value": 24999}
    )
    v = gate(lower, refund_action(25000))
    assert v.verdict == "review"
    assert v.failed_rules == ["within_auto_approve_limit"]


def test_other_orders_and_won_disputes_are_ignored(s0):
    state = patched(
        s0,
        {"op": "add", "path": "/refunds/-", "value": refund_row(25000, order_id="ORD-OTHER")},
        {"op": "add", "path": "/disputes/-", "value": dispute(order_id="ORD-OTHER")},
        {"op": "add", "path": "/disputes/-", "value": dispute(status="won", dispute_id="DSP-W")},
        {"op": "add", "path": "/disputes/-", "value": dispute(status="lost", dispute_id="DSP-L")},
        {"op": "add", "path": "/tickets/-", "value": ticket_row(order_id="ORD-OTHER")},
    )
    v = gate(state)
    assert v.verdict == "allow", v.failed_rules


def test_duplicate_check_ignores_denied_open_and_other_reasons(s0):
    denied = patched(
        s0, {"op": "add", "path": "/tickets/-", "value": ticket_row(resolution="denied")}
    )
    assert gate(denied).results[6].passed
    other_reason = patched(
        s0, {"op": "add", "path": "/tickets/-", "value": ticket_row(reason="damaged")}
    )
    assert gate(other_reason).results[6].passed
    still_open = patched(
        s0,
        {"op": "add", "path": "/tickets/-", "value": ticket_row(status="open", resolution=None)},
    )
    assert gate(still_open).results[6].passed
    dupe = gate(patched(s0, {"op": "add", "path": "/tickets/-", "value": ticket_row()}))
    assert dupe.results[6].observed["duplicate_ticket_ids"] == ["TCK-OTHER"]


# --- hardening: order binding, ticket binding, own-ticket duplicate ---------------------

ORDER_RULES = ["amount_within_refundable_balance", "currency_matches_order", "payment_captured"]
TICKET_RULES = ["amount_matches_request", "no_duplicate_refund", "return_eligible"]
OTHER_TICKET_RULES = ["no_duplicate_refund", "return_eligible"]  # also observe ticket_order_matches


def test_mismatched_order_id_fails_order_and_ticket_rules(s0):
    v = gate(s0, refund_action(order_id="ORD-ELSEWHERE"))
    assert v.verdict == "block"
    # the action's ticket belongs to ORD-F10, so the ticket rules fail closed too
    assert v.failed_rules == sorted(ORDER_RULES + TICKET_RULES)
    results = {r.rule_id: r for r in v.results}
    for rule_id in ORDER_RULES:
        assert results[rule_id].observed["order_matches"] is False
        assert "order mismatch" in results[rule_id].message
        assert "/order/order_id" in results[rule_id].reads
    for rule_id in TICKET_RULES:
        assert results[rule_id].observed["ticket_found"] is True
    for rule_id in OTHER_TICKET_RULES:
        assert results[rule_id].observed["ticket_order_matches"] is False
    assert "belongs to another order" in results["amount_matches_request"].message


def test_order_rules_pass_when_order_matches(s0):
    for result in gate(s0).results:
        if result.rule_id in ORDER_RULES:
            assert result.observed["order_matches"] is True


def test_unknown_ticket_fails_ticket_rules_closed(s0):
    v = gate(s0, refund_action(ticket_id="TCK-NOPE"))
    assert v.verdict == "block"
    assert v.failed_rules == TICKET_RULES
    for result in v.results:
        if result.rule_id in TICKET_RULES:
            assert result.observed["ticket_found"] is False
            assert result.observed.get("ticket_order_matches", False) is False
            assert "not found" in result.message


def test_ticket_from_another_order_fails_ticket_rules(s0):
    state = patched(
        s0,
        {"op": "add", "path": "/tickets/-", "value": ticket_row(
            ticket_id="TCK-X", status="open", resolution=None, order_id="ORD-OTHER"
        )},
    )
    v = gate(state, refund_action(ticket_id="TCK-X"))
    assert v.failed_rules == TICKET_RULES
    assert v.results[7].observed["ticket_order_matches"] is False


def test_own_ticket_already_refunded_fails_duplicate(s0):
    own_resolved = patched(
        s0,
        {"op": "replace", "path": "/tickets/0/status", "value": "resolved"},
        {"op": "replace", "path": "/tickets/0/resolution", "value": "refunded"},
    )
    v = gate(own_resolved)
    assert v.failed_rules == ["no_duplicate_refund"]
    assert v.results[6].observed["own_ticket_refunded"] is True
    own_denied = patched(
        s0,
        {"op": "replace", "path": "/tickets/0/status", "value": "resolved"},
        {"op": "replace", "path": "/tickets/0/resolution", "value": "denied"},
    )
    assert gate(own_denied).results[6].passed


def test_refund_row_for_own_ticket_fails_duplicate(s0):
    row = refund_row(1000) | {"ticket_id": TICKET, "refund_id": "RF-OWN"}
    state = patched(s0, {"op": "add", "path": "/refunds/-", "value": row}, asks(20000))
    v = gate(state, refund_action(20000))  # $200 fits the remaining $240
    assert v.failed_rules == ["no_duplicate_refund"]
    assert v.results[6].observed["own_refund_ids"] == ["RF-OWN"]
    assert "RF-OWN" in v.results[6].message
    # a refund row on the same order without a ticket id is the balance rule's job, not this one
    untagged_state = patched(
        s0, {"op": "add", "path": "/refunds/-", "value": refund_row(1000)}, asks(20000)
    )
    untagged = gate(untagged_state, refund_action(20000))
    assert untagged.results[6].passed


def test_damaged_needs_no_return(s0):
    state = patched(
        s0,
        {"op": "replace", "path": "/tickets/0/reason_code", "value": "damaged"},
        {"op": "replace", "path": "/return_info/status", "value": "not_required"},
    )
    assert gate(state).verdict == "allow"
    received = patched(s0, {"op": "replace", "path": "/return_info/status", "value": "received"})
    assert gate(received).verdict == "allow"
    label = patched(s0, {"op": "replace", "path": "/return_info/status", "value": "label_created"})
    assert gate(label).failed_rules == ["return_eligible"]
    damaged_rejected = patched(
        s0,
        {"op": "replace", "path": "/tickets/0/reason_code", "value": "damaged"},
        {"op": "replace", "path": "/return_info/status", "value": "rejected"},
    )
    v = gate(damaged_rejected)
    assert (v.verdict, v.failed_rules) == ("block", ["return_eligible"])


def test_block_beats_review_and_failed_rules_sorted(s0):
    state = patched(s0, *BREAKERS["within_auto_approve_limit"], *BREAKERS["no_open_chargeback"])
    state = patched(state, *BREAKERS["currency_matches_order"])
    v = gate(state)
    assert v.verdict == "block"
    assert v.failed_rules == [
        "currency_matches_order",
        "no_open_chargeback",
        "within_auto_approve_limit",
    ]
    assert [r.rule_id for r in v.results] == [r.id for r in DEFAULT_RULES]


def test_gate_rejects_non_refund_actions(s0):
    deny = AgentDecision(
        action="deny_refund",
        order_id=ORDER,
        ticket_id=TICKET,
        amount_minor=None,
        currency=None,
        reason="r",
        rationale="r",
        memory_note="n",
    )
    with pytest.raises(ValueError):
        gate(s0, deny)


def test_gate_does_not_mutate_or_peek(s0):
    before = s0.model_dump(mode="json")
    reader = StateReader(s0, AT)
    evaluate_gate(refund_action(), reader)
    assert s0.model_dump(mode="json") == before
    # every read goes through the reader, and each rule's reads are among them
    read_paths = {r.path for r in reader.reads}
    for result in evaluate_gate(refund_action(), StateReader(s0, AT)).results:
        assert set(result.reads) <= read_paths


def test_no_peeking_in_gate_source(repo_root: Path):
    source = (repo_root / "src" / "srg" / "gate.py").read_text(encoding="utf-8")
    hits = re.findall(r"scenario|mutation|expected|S[0-9]{2}", source, flags=re.IGNORECASE)
    assert hits == []
