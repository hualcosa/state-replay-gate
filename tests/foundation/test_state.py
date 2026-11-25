from __future__ import annotations

import pytest
from pydantic import ValidationError

from srg.canonical import digest
from srg.contracts import PatchOp
from srg.scenarios import load_scenario
from srg.state import StateReader, apply_patch, diff_states

DISPUTE = {
    "dispute_id": "DSP-1",
    "order_id": "ORD-F10",
    "type": "chargeback",
    "status": "open",
    "amount_minor": 100,
    "currency": "USD",
    "opened_at": "2026-10-12T00:00:00Z",
}


@pytest.fixture
def s0(fixture_scenarios_dir):
    return load_scenario(fixture_scenarios_dir / "F03-control-no-change.json").t0.state


def test_apply_patch_is_pure(s0):
    before = digest(s0)
    s1 = apply_patch(s0, [PatchOp(op="replace", path="/customer/fraud_flag", value=True)])
    assert digest(s0) == before
    assert s0.customer.fraud_flag is False and s1.customer.fraud_flag is True


def test_append_with_dash(s0):
    s1 = apply_patch(s0, [PatchOp(op="add", path="/disputes/-", value=DISPUTE)])
    assert len(s1.disputes) == 1 and s1.disputes[0].dispute_id == "DSP-1"
    s2 = apply_patch(
        s1, [PatchOp(op="add", path="/disputes/0", value={**DISPUTE, "dispute_id": "D0"})]
    )
    assert [d.dispute_id for d in s2.disputes] == ["D0", "DSP-1"]


def test_patch_value_not_aliased(s0):
    value = dict(DISPUTE)
    op = PatchOp(op="add", path="/disputes/-", value=value)
    apply_patch(s0, [op])
    assert op.value == DISPUTE


def test_remove_and_replace_in_list(s0):
    s1 = apply_patch(s0, [PatchOp(op="add", path="/disputes/-", value=DISPUTE)])
    s2 = apply_patch(s1, [PatchOp(op="replace", path="/disputes/0/status", value="won")])
    assert s2.disputes[0].status == "won"
    assert apply_patch(s2, [PatchOp(op="remove", path="/disputes/0")]).disputes == []


def test_invalid_enum_raises_validation_error(s0):
    with pytest.raises(ValidationError):
        apply_patch(s0, [PatchOp(op="replace", path="/order/payment/status", value="refunded")])


@pytest.mark.parametrize(
    "op",
    [
        PatchOp(op="replace", path="/order/nope", value=1),
        PatchOp(op="replace", path="order/status", value="x"),
        PatchOp(op="remove", path="/disputes/0"),
        PatchOp(op="replace", path="/disputes/-", value=1),
        PatchOp(op="add", path="/tickets/5", value={}),
        PatchOp(op="replace", path="/tickets/01/status", value="open"),
        PatchOp(op="add", path="/missing/deep", value=1),
        PatchOp(op="replace", path="", value={}),
    ],
)
def test_bad_pointer_raises(s0, op):
    with pytest.raises(ValueError):
        apply_patch(s0, [op])


def test_diff_identical_is_empty(s0):
    assert diff_states(s0, s0) == []


def test_diff_appended_dispute_is_one_change(s0):
    s1 = apply_patch(s0, [PatchOp(op="add", path="/disputes/-", value=DISPUTE)])
    changes = diff_states(s0, s1)
    assert len(changes) == 1
    assert changes[0].path == "/disputes/0"
    assert changes[0].before is None and changes[0].after == DISPUTE


def test_diff_leaf_changes_in_document_order(s0):
    s1 = apply_patch(
        s0,
        [
            PatchOp(op="replace", path="/policy/version", value=8),
            PatchOp(op="replace", path="/customer/tier", value="gold"),
        ],
    )
    changes = diff_states(s0, s1)
    assert [(c.path, c.before, c.after) for c in changes] == [
        ("/customer/tier", "standard", "gold"),
        ("/policy/version", 7, 8),
    ]
    removed = diff_states(s1, s0)
    assert [c.path for c in removed] == ["/customer/tier", "/policy/version"]


def test_reader_logs_in_order_and_returns_copies(s0):
    reader = StateReader(s0, "2026-10-12T14:00:00Z")
    items = reader.read("/order/items")
    status = reader.read("/order/payment/status")
    items[0]["qty"] = 999
    assert [r.path for r in reader.reads] == ["/order/items", "/order/payment/status"]
    assert status == "captured"
    assert reader.read("/order/items/0/qty") == 1
    assert reader.reads[0].value[0]["qty"] == 1
    assert s0.order.items[0].qty == 1
    assert reader.at == "2026-10-12T14:00:00Z"


def test_reader_whole_doc_and_digest(s0):
    reader = StateReader(s0, "t")
    assert reader.state_digest == digest(s0)
    assert reader.reads == []  # digest is not a read
    assert reader.read("") == s0.model_dump(mode="json")


@pytest.mark.parametrize("path", ["/order/nope", "/disputes/0", "/tickets/9", "/tickets/x"])
def test_reader_missing_pointer_raises_key_error(s0, path):
    reader = StateReader(s0, "t")
    with pytest.raises(KeyError):
        reader.read(path)
    assert reader.reads == []
