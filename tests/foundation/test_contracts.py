from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from srg.canonical import digest
from srg.contracts import (
    ARM_SPECS,
    RULE_IDS,
    RULE_SPECS,
    AgentDecision,
    DecisionRequest,
    Expected,
    ExpectedGate,
    Mutation,
    PatchOp,
    Recording,
    RecordingMissError,
    RunArtifact,
    Scenario,
    WorldState,
    same_decision,
    verdict_for,
)
from srg.scenarios import load_scenario


@pytest.fixture
def f01(fixture_scenarios_dir) -> Scenario:
    return load_scenario(fixture_scenarios_dir / "F01-hero-chargeback.json")


def _refund(**kw) -> dict:
    base = dict(
        action="issue_refund",
        order_id="O1",
        ticket_id="T1",
        amount_minor=100,
        currency="USD",
        reason="r",
        rationale="why",
        memory_note="note",
    )
    base.update(kw)
    return base


def _recording_dict() -> dict:
    request = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    return {
        "schema_version": "1.0",
        "request_hash": digest(request),
        "labels": ["a", "b"],
        "request": request,
        "samples": [
            {"sample": 0, "response": {}, "latency_ms": 1, "recorded_at": "2026-01-01T00:00:00Z"}
        ],
    }


# --- unknown keys rejected on every top-level model -------------------------


def test_unknown_key_rejected_world_state(f01):
    d = f01.t0.state.model_dump(mode="json")
    d["extra"] = 1
    with pytest.raises(ValidationError, match="extra"):
        WorldState.model_validate(d)


def test_unknown_nested_key_rejected(f01):
    d = f01.t0.state.model_dump(mode="json")
    d["order"]["payment"]["surprise"] = True
    with pytest.raises(ValidationError):
        WorldState.model_validate(d)


def test_unknown_key_rejected_scenario(f01):
    d = f01.model_dump(mode="json")
    d["notes"] = "x"
    with pytest.raises(ValidationError):
        Scenario.model_validate(d)


def test_unknown_key_rejected_decision():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(_refund(confidence=0.9))


def test_unknown_key_rejected_recording():
    d = _recording_dict()
    d["headers"] = {}
    with pytest.raises(ValidationError):
        Recording.model_validate(d)


def test_unknown_key_rejected_run_artifact(run_fixture_path):
    d = RunArtifact.model_validate_json(run_fixture_path.read_text()).model_dump(mode="json")
    d["bonus"] = 1
    with pytest.raises(ValidationError):
        RunArtifact.model_validate(d)


# --- AgentDecision ----------------------------------------------------------


def test_deny_with_amount_rejected():
    with pytest.raises(ValidationError, match="null"):
        AgentDecision.model_validate(_refund(action="deny_refund"))


def test_issue_refund_without_currency_rejected():
    with pytest.raises(ValidationError, match="currency"):
        AgentDecision.model_validate(_refund(currency=None))


@pytest.mark.parametrize(
    "kw",
    [
        {"amount_minor": 0},
        {"currency": "usd"},
        {"rationale": "x" * 601},
        {"memory_note": "x" * 401},
    ],
)
def test_decision_field_limits(kw):
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(_refund(**kw))


def test_decision_limits_inclusive():
    AgentDecision.model_validate(_refund(rationale="x" * 600, memory_note="x" * 400))


def test_same_decision():
    a = AgentDecision.model_validate(_refund())
    b = AgentDecision.model_validate(_refund(reason="other", rationale="other", memory_note="o"))
    assert same_decision(a, b)
    assert not same_decision(a, AgentDecision.model_validate(_refund(amount_minor=101)))
    assert not same_decision(a, AgentDecision.model_validate(_refund(currency="CAD")))
    deny1 = AgentDecision.model_validate(
        _refund(action="deny_refund", amount_minor=None, currency=None)
    )
    deny2 = deny1.model_copy(update={"rationale": "different"})
    assert same_decision(deny1, deny2)
    assert not same_decision(a, deny1)


# --- DecisionRequest --------------------------------------------------------


def test_decision_request_context_consistency(f01):
    ticket = f01.ticket
    DecisionRequest(
        label="x", ticket=ticket, context_kind="state", state=f01.t0.state, memory_note=None
    )
    DecisionRequest(
        label="x", ticket=ticket, context_kind="memory_note", state=None, memory_note="n"
    )
    with pytest.raises(ValidationError):
        DecisionRequest(label="x", ticket=ticket, context_kind="state", state=None, memory_note="n")
    with pytest.raises(ValidationError):
        DecisionRequest(
            label="x",
            ticket=ticket,
            context_kind="memory_note",
            state=f01.t0.state,
            memory_note="n",
        )
    with pytest.raises(ValidationError):
        DecisionRequest(
            label="x",
            ticket=ticket,
            context_kind="state",
            state=f01.t0.state,
            memory_note=None,
            sample=-1,
        )


# --- Mutation / PatchOp -----------------------------------------------------


def test_patch_op_value_required_for_add_replace():
    with pytest.raises(ValidationError):
        PatchOp(op="replace", path="/a")
    PatchOp(op="replace", path="/customer/fraud_reason", value=None)  # explicit null is a value
    PatchOp(op="remove", path="/a")


def test_mutation_rules():
    base = dict(kind="none", label="No change", actor="x", narrative="n", at="t", ops=[])
    Mutation.model_validate(base)
    with pytest.raises(ValidationError):
        Mutation.model_validate({**base, "label": "x" * 41})
    with pytest.raises(ValidationError):
        Mutation.model_validate({**base, "ops": [{"op": "remove", "path": "/notes/0"}]})
    with pytest.raises(ValidationError):
        Mutation.model_validate({**base, "kind": "benign_change"})


# --- Expected / ExpectedGate ------------------------------------------------


def test_expected_rules(f01):
    good = f01.expected.model_dump(mode="json")
    Expected.model_validate(good)
    for bad in (
        {"t0_amount_minor": None},
        {"gate": None},
        {"t1_should_pay": True},  # harm is not none
        {"harm_if_paid": {"type": "none", "amount_minor": 0, "explanation": ""}},
        {"t0_action": "deny_refund"},
    ):
        with pytest.raises(ValidationError):
            Expected.model_validate({**copy.deepcopy(good), **bad})


@pytest.mark.parametrize(
    "gate",
    [
        {"verdict": "block", "failed_rules": ["unknown_rule"]},
        {
            "verdict": "block",
            "failed_rules": ["no_open_chargeback", "amount_within_refundable_balance"],
        },
        {"verdict": "block", "failed_rules": ["no_open_chargeback", "no_open_chargeback"]},
        {"verdict": "allow", "failed_rules": ["no_open_chargeback"]},
        {"verdict": "block", "failed_rules": ["within_auto_approve_limit"]},
    ],
)
def test_expected_gate_rejects(gate):
    with pytest.raises(ValidationError):
        ExpectedGate.model_validate(gate)


def test_verdict_for():
    assert verdict_for([]) == "allow"
    assert verdict_for(["within_auto_approve_limit"]) == "review"
    assert verdict_for(["within_auto_approve_limit", "payment_captured"]) == "block"


# --- Scenario ---------------------------------------------------------------


@pytest.mark.parametrize("bad_id", ["X01", "S1", "S001", "s01"])
def test_scenario_id_pattern(f01, bad_id):
    d = f01.model_dump(mode="json")
    d["id"] = bad_id
    with pytest.raises(ValidationError):
        Scenario.model_validate(d)


def test_scenario_ticket_must_exist(f01):
    d = f01.model_dump(mode="json")
    d["ticket_id"] = "NOPE"
    with pytest.raises(ValidationError, match="NOPE"):
        Scenario.model_validate(d)


# --- Recording --------------------------------------------------------------


def test_recording_valid():
    Recording.model_validate(_recording_dict())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(labels=["b", "a"]),
        lambda d: d.update(labels=["a", "a"]),
        lambda d: d["samples"][0].update(sample=1),
        lambda d: d.update(request_hash="sha256:" + "0" * 64),
    ],
)
def test_recording_invalid(mutate):
    d = _recording_dict()
    mutate(d)
    with pytest.raises(ValidationError):
        Recording.model_validate(d)


def test_recording_miss_error_fields():
    err = RecordingMissError("S01/t0", "sha256:abc", 1)
    assert (err.label, err.request_hash, err.sample) == ("S01/t0", "sha256:abc", 1)
    assert "S01/t0" in str(err) and "sha256:abc" in str(err)


# --- constants --------------------------------------------------------------


def test_rule_specs_order_and_severity():
    assert list(RULE_IDS) == [
        "payment_captured",
        "customer_not_fraud_flagged",
        "no_open_chargeback",
        "currency_matches_order",
        "amount_within_refundable_balance",
        "amount_matches_request",
        "no_duplicate_refund",
        "return_eligible",
        "within_auto_approve_limit",
    ]
    assert [r.severity for r in RULE_SPECS] == ["block"] * 8 + ["review"]


def test_arm_specs():
    assert [a.id for a in ARM_SPECS] == ["A", "B", "C", "D"]
    assert [(a.t1_context, a.llm_call_at_t1, a.has_gate) for a in ARM_SPECS] == [
        ("live_state", True, False),
        ("memory_note", True, False),
        ("frozen_state", True, False),
        ("frozen_state", False, True),
    ]
