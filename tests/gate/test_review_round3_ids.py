"""Review round 3, finding 1: duplicate row ids in live state.

Reproduction: on S09, a second row with the scenario's ticket id, marked resolved/refunded, made
D allow when appended and block when inserted first. Duplicate ids are now invalid state, and the
gate fails closed unless the action binds to exactly one ticket row.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from srg.contracts import AgentDecision, PatchOp, Scenario, WorldState
from srg.gate import evaluate_gate
from srg.scenarios import load_scenario
from srg.state import StateReader, apply_patch

AT = "2026-10-12T14:00:00Z"


@pytest.fixture(scope="module")
def s09(scenarios_dir: Path) -> Scenario:
    return load_scenario(next(scenarios_dir.glob("S09-*.json")))


def _refund_action(sc: Scenario) -> AgentDecision:
    t = sc.ticket
    return AgentDecision(
        action="issue_refund",
        order_id=t.order_id,
        ticket_id=t.ticket_id,
        amount_minor=t.requested_minor,
        currency=t.currency,
        reason="r",
        rationale="r",
        memory_note="m",
    )


def _refunded_twin(sc: Scenario):
    return sc.ticket.model_copy(update={"status": "resolved", "resolution": "refunded"})


def _orders(sc: Scenario):
    twin = _refunded_twin(sc)
    tickets = list(sc.t0.state.tickets)
    return {"appended": [*tickets, twin], "first": [twin, *tickets]}


@pytest.mark.parametrize("order", ["appended", "first"])
def test_world_state_rejects_duplicate_ticket_ids_in_either_order(s09, order):
    doc = s09.t0.state.model_dump(mode="json")
    doc["tickets"] = [t.model_dump(mode="json") for t in _orders(s09)[order]]
    with pytest.raises(ValidationError, match="duplicate ticket_id"):
        WorldState.model_validate(doc)


@pytest.mark.parametrize(
    ("key", "id_field"), [("refunds", "refund_id"), ("disputes", "dispute_id")]
)
def test_world_state_rejects_duplicate_refund_and_dispute_ids(scenarios_dir, key, id_field):
    # S02 adds a refund at T1, S01 a dispute: duplicate that row in S1.
    name = "S02-*.json" if key == "refunds" else "S01-*.json"
    sc = load_scenario(next(scenarios_dir.glob(name)))
    s1 = apply_patch(sc.t0.state, sc.mutation.ops).model_dump(mode="json")
    s1[key].append(dict(s1[key][-1]))
    with pytest.raises(ValidationError, match=f"duplicate {id_field}"):
        WorldState.model_validate(s1)


def test_patch_creating_a_duplicate_ticket_fails(s09):
    twin = _refunded_twin(s09).model_dump(mode="json")
    with pytest.raises(ValidationError, match="duplicate ticket_id"):
        apply_patch(s09.t0.state, [PatchOp(op="add", path="/tickets/-", value=twin)])
    with pytest.raises(ValidationError, match="duplicate ticket_id"):
        apply_patch(s09.t0.state, [PatchOp(op="add", path="/tickets/0", value=twin)])


@pytest.mark.parametrize("order", ["appended", "first"])
def test_gate_fails_closed_on_an_ambiguous_ticket_in_either_order(s09, order):
    # Bypass validation to reach the gate with the reviewer's state.
    state = WorldState.model_construct(
        **{**dict(s09.t0.state), "tickets": _orders(s09)[order]}
    )
    verdict = evaluate_gate(_refund_action(s09), StateReader(state, AT))
    assert verdict.verdict == "block"
    assert {"amount_matches_request", "no_duplicate_refund", "return_eligible"} <= set(
        verdict.failed_rules
    )
    dup = next(r for r in verdict.results if r.rule_id == "no_duplicate_refund")
    assert dup.observed["ticket_found"] is False
    assert f"ticket {s09.ticket_id} is ambiguous: 2 rows" in dup.message


def test_gate_still_allows_the_unambiguous_s09(s09):
    verdict = evaluate_gate(_refund_action(s09), StateReader(s09.t0.state, AT))
    assert (verdict.verdict, verdict.failed_rules) == ("allow", [])
