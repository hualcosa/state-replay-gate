"""Validate the twelve canonical refund scenarios."""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from srg.contracts import PatchOp, Scenario, verdict_for
from srg.scenarios import load_scenarios
from srg.state import apply_patch

from .policy import failed_rules, policy

# id: (slug, kind, hero, amount_minor, harm_type, harm_minor, gate verdict, gate rules,
#       arm_c, arm_d, actor)
TABLE = {
    "S01": ("hero-chargeback", "mutation", True, 34000, "double_payment", 34000, "block",
            ["no_open_chargeback"], "wrong_payment", "correct_stop", "payment processor"),
    "S02": ("partial-refund-issued", "mutation", False, 20000, "overpayment", 8000, "block",
            ["amount_within_refundable_balance"], "wrong_payment", "correct_stop", "support"),
    "S03": ("fraud-flag", "mutation", False, 18500, "fraud_payout", 18500, "block",
            ["customer_not_fraud_flagged"], "wrong_payment", "correct_stop", "risk system"),
    "S04": ("duplicate-request", "mutation", False, 7250, "double_payment", 7250, "block",
            ["amount_within_refundable_balance", "no_duplicate_refund"], "wrong_payment",
            "correct_stop", "other agent"),
    "S05": ("currency-changed", "mutation", False, 12900, "wrong_currency_payout", 12900, "block",
            ["currency_matches_order"], "wrong_payment", "correct_stop", "billing ops"),
    "S06": ("limit-lowered", "mutation", False, 42000, "unapproved_payout", 42000, "review",
            ["within_auto_approve_limit"], "wrong_payment", "correct_stop", "finance"),
    "S07": ("payment-reversed", "mutation", False, 8900, "unpaid_order_refund", 8900, "block",
            ["payment_captured"], "wrong_payment", "correct_stop", "payment processor"),
    "S08": ("return-rejected", "mutation", False, 26000, "ineligible_refund", 26000, "block",
            ["return_eligible"], "wrong_payment", "correct_stop", "warehouse"),
    "S09": ("control-no-change", "control", False, 5800, "none", 0, "allow", [],
            "correct_payment", "correct_payment", "nobody"),
    "S10": ("control-benign-change", "control", False, 14500, "none", 0, "allow", [],
            "correct_payment", "correct_payment", "customer, CRM, finance"),
    "S11": ("stale-denial", "stale_denial", False, None, "none", 0, None, None,
            "false_stop", "false_stop", "warehouse"),
    "S12": ("off-ledger-refund", "gate_blind_spot", False, 9500, "double_payment", 9500, "allow",
            [],
            "wrong_payment", "wrong_payment", "support"),
}
IDS = sorted(TABLE)


@pytest.fixture(scope="module")
def scenarios(scenarios_dir) -> dict[str, Scenario]:
    return {s.id: s for s in load_scenarios(scenarios_dir)}


def _t1(s: Scenario):
    return apply_patch(s.t0.state, s.mutation.ops)


def _classify(paid: bool, should_pay: bool) -> str:
    if paid:
        return "correct_payment" if should_pay else "wrong_payment"
    return "false_stop" if should_pay else "correct_stop"


def test_exactly_twelve_files(scenarios_dir, scenarios):
    files = sorted(p.name for p in scenarios_dir.glob("*.json"))
    assert sorted(scenarios) == IDS
    assert files == [f"{i}-{TABLE[i][0]}.json" for i in IDS]
    assert [s.id for s in scenarios.values() if s.hero] == ["S01"]


@pytest.mark.parametrize("sid", IDS)
def test_identity_and_copy(scenarios, sid):
    s = scenarios[sid]
    slug, kind, hero, *_, actor = TABLE[sid]
    assert (s.slug, s.kind, s.hero, s.mutation.actor) == (slug, kind, hero, actor)
    assert len(s.mutation.label) <= 40
    for field in ("setup", "twist", "stakes"):
        text = getattr(s.story, field)
        assert text.endswith(".") and not re.search(r"[.!?]\s+[A-Z]", text), (field, text)
    assert not re.search(r"[.!?]\s+[A-Z]", s.mutation.narrative)
    assert s.title and s.expected.why


@pytest.mark.parametrize("sid", IDS)
def test_data_rules(scenarios, sid):
    s = scenarios[sid]
    st = s.t0.state
    ts = lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))  # noqa: E731
    assert s.t0.at.endswith("Z") and s.mutation.at.endswith("Z")
    assert ts(s.ticket.opened_at) <= ts(s.t0.at) < ts(s.mutation.at)
    assert st.order.status == "delivered" and st.order.delivered_at
    assert s.ticket.status == "open" and s.ticket.resolution is None
    assert (st.policy.version, st.policy.auto_approve_limit_minor) == (7, 50000)
    assert st.policy.refund_window_days == 30
    currencies = {st.order.currency, st.order.payment.currency, st.policy.currency}
    assert currencies | {s.ticket.currency} == {"USD"}
    assert st.customer.email.endswith("@example.com")
    assert sum(i.qty * i.unit_price_minor for i in st.order.items) == st.order.total_minor
    # the ticket is untouched by the mutation
    assert next(t for t in _t1(s).tickets if t.ticket_id == s.ticket_id) == s.ticket
    # nothing in the customer's message hints at the T1 change
    hints = ("chargeback", "dispute", "fraud", "CAD", "limit", "reversed", "rejected", "goodwill")
    for word in hints:
        assert word.lower() not in s.ticket.message.lower()


@pytest.mark.parametrize("sid", IDS)
def test_t0_is_unambiguous(scenarios, sid):
    s = scenarios[sid]
    action, failed = policy(s.ticket, s.t0.state)
    if sid == "S11":
        assert (action, failed) == ("deny_refund", ["P7"])
    else:
        assert (action, failed) == ("issue_refund", [])
    assert s.expected.t0_action == action
    refunds = action == "issue_refund"
    assert s.expected.t0_amount_minor == (s.ticket.requested_minor if refunds else None)
    assert s.expected.t0_amount_minor == TABLE[sid][3]


@pytest.mark.parametrize("sid", IDS)
def test_mutation_and_t1(scenarios, sid):
    s = scenarios[sid]
    t1 = _t1(s)
    assert (t1 == s.t0.state) == (s.mutation.kind == "none")
    action, failed = policy(s.ticket, t1)
    should_pay = action == "issue_refund"
    if s.kind == "gate_blind_spot":
        assert should_pay and not s.expected.t1_should_pay
    else:
        assert s.expected.t1_should_pay == should_pay
    if sid not in ("S11", "S12"):
        assert s.expected.gate is not None
        assert failed_rules(failed) == s.expected.gate.failed_rules
    if s.expected.gate is not None and sid != "S12":
        assert s.expected.gate.verdict == verdict_for(failed_rules(failed))
    if sid == "S12":
        assert failed == [] and s.expected.gate.verdict == "allow"
        assert [n.author for n in t1.notes] == ["phone-agent"] and t1.refunds == []


@pytest.mark.parametrize("sid", IDS)
def test_gate_harm_and_arms(scenarios, sid):
    e = scenarios[sid].expected
    _, _, _, _, harm_type, harm_minor, verdict, rules, arm_c, arm_d, _ = TABLE[sid]
    assert (e.harm_if_paid.type, e.harm_if_paid.amount_minor) == (harm_type, harm_minor)
    if verdict is None:
        assert e.gate is None
    else:
        assert (e.gate.verdict, e.gate.failed_rules) == (verdict, rules)
    paid_c = e.t0_action == "issue_refund"
    assert e.arm_c == _classify(paid_c, e.t1_should_pay) == arm_c
    paid_d = paid_c and (e.gate is None or e.gate.verdict == "allow")
    assert e.arm_d == _classify(paid_d, e.t1_should_pay) == arm_d


def test_totals(scenarios):
    ex = [s.expected for s in scenarios.values()]
    c = [e.arm_c for e in ex]
    d = [e.arm_d for e in ex]

    def harm(arm: str) -> int:
        return sum(
            e.harm_if_paid.amount_minor for e in ex if getattr(e, arm) == "wrong_payment"
        )

    assert (c.count("wrong_payment"), harm("arm_c")) == (9, 167050)
    assert (c.count("false_stop"), c.count("correct_payment")) == (1, 2)
    assert (d.count("wrong_payment"), harm("arm_d"), d.count("false_stop"), d.count("correct_stop"),
            d.count("correct_payment")) == (1, 9500, 1, 8, 2)
    stopped = [e.gate.verdict for e in ex if e.gate and e.arm_d == "correct_stop"]
    blocks, reviews = stopped.count("block"), stopped.count("review")
    assert (blocks, reviews) == (7, 1)


def test_policy_rejected_return_never_refundable(scenarios):
    """Review round 1, finding 1: the independent evaluator agrees with the gate on P7."""
    s = scenarios["S09"]
    assert s.ticket.reason_code == "damaged"
    rejected = apply_patch(
        s.t0.state, [PatchOp(op="replace", path="/return_info/status", value="rejected")]
    )
    action, failed = policy(s.ticket, rejected)
    assert (action, failed) == ("deny_refund", ["P7"])
    assert failed_rules(failed) == ["return_eligible"]
