"""Integration with 2a's canonical scenarios. Skips until scenarios/S*.json exists."""

from __future__ import annotations

from pathlib import Path

import pytest

from srg.contracts import PatchOp
from srg.gate import evaluate_gate
from srg.scenarios import load_scenarios
from srg.scripted import ScriptedProvider
from srg.state import StateReader, apply_patch

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"
HAS_SCENARIOS = SCENARIOS_DIR.is_dir() and any(SCENARIOS_DIR.glob("S*.json"))

pytestmark = pytest.mark.skipif(not HAS_SCENARIOS, reason="scenarios/S*.json not present (2a)")


def _cases():
    if not HAS_SCENARIOS:
        return []
    return load_scenarios(SCENARIOS_DIR)


@pytest.mark.parametrize("scenario", _cases(), ids=lambda s: s.id)
def test_gate_allows_on_s0_and_matches_expected_on_s1(scenario):
    exp = scenario.expected
    if exp.gate is None:
        assert exp.t0_action != "issue_refund"
        return
    t0 = ScriptedProvider.from_expected([scenario]).decisions[f"{scenario.id}/t0"]
    assert t0.action == "issue_refund"
    on_s0 = evaluate_gate(t0, StateReader(scenario.t0.state, scenario.t0.at))
    assert on_s0.verdict == "allow", on_s0.failed_rules
    s1 = apply_patch(scenario.t0.state, scenario.mutation.ops)
    on_s1 = evaluate_gate(t0, StateReader(s1, scenario.mutation.at))
    assert (on_s1.verdict, on_s1.failed_rules) == (exp.gate.verdict, exp.gate.failed_rules)


def test_twelve_canonical_scenarios():
    assert [s.id for s in _cases()] == [f"S{i:02d}" for i in range(1, 13)]


def test_rejected_return_blocks_damaged_refund_s09():
    """Review round 1, finding 1: P7 says a rejected return is never refundable, even if damaged."""
    s09 = next(s for s in _cases() if s.id == "S09")
    assert s09.ticket.reason_code == "damaged"
    t0 = ScriptedProvider.from_expected([s09]).decisions["S09/t0"]
    assert t0.action == "issue_refund"
    rejected = apply_patch(
        s09.t0.state, [PatchOp(op="replace", path="/return_info/status", value="rejected")]
    )
    verdict = evaluate_gate(t0, StateReader(rejected, s09.t0.at))
    assert (verdict.verdict, verdict.failed_rules) == ("block", ["return_eligible"])
