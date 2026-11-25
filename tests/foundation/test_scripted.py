from __future__ import annotations

import pytest

from srg.canonical import digest
from srg.contracts import DecisionRequest
from srg.scenarios import load_scenarios
from srg.scripted import FIXTURE_PREFIX, ScriptedProvider
from srg.state import apply_patch


@pytest.fixture(scope="module")
def scenarios(fixture_scenarios_dir):
    return {s.id: s for s in load_scenarios(fixture_scenarios_dir)}


@pytest.fixture(scope="module")
def provider(scenarios):
    return ScriptedProvider.from_expected(scenarios.values())


def _req(s, label, sample, state):
    return DecisionRequest(
        label=f"{s.id}/{label}",
        ticket=s.ticket,
        context_kind="state",
        state=state,
        memory_note=None,
        sample=sample,
    )


def test_labels(provider):
    assert sorted(provider.decisions) == sorted(
        f"F0{n}/{arm}" for n in range(1, 6) for arm in ("t0", "C", "A", "B")
    )


def test_c_hash_equals_t0_hash(provider, scenarios):
    s = scenarios["F01"]
    t0 = provider.decide(_req(s, "t0", 0, s.t0.state))
    c = provider.decide(_req(s, "C", 1, s.t0.state))
    assert t0.llm.request_hash == c.llm.request_hash
    assert (t0.llm.sample, c.llm.sample) == (0, 1)
    assert t0.llm.model == "fixture-scripted" and t0.llm.latency_ms == 0
    assert t0.llm.prompt_tokens is None and t0.llm.completion_tokens is None
    s1 = apply_patch(s.t0.state, s.mutation.ops)
    a = provider.decide(_req(s, "A", 0, s1))
    assert a.llm.request_hash != t0.llm.request_hash
    assert digest(s1) != digest(s.t0.state)


def test_from_expected_actions(provider, scenarios):
    actions = {sid: provider.decisions[f"{sid}/A"].action for sid in scenarios}
    assert actions == {
        "F01": "deny_refund",
        "F02": "escalate_to_human",
        "F03": "issue_refund",
        "F04": "issue_refund",
        "F05": "issue_refund",
    }
    for sid, s in scenarios.items():
        t0 = provider.decisions[f"{sid}/t0"]
        assert t0 == provider.decisions[f"{sid}/C"] == provider.decisions[f"{sid}/B"]
        assert t0.action == s.expected.t0_action
        assert t0.amount_minor == s.expected.t0_amount_minor
    refund = provider.decisions["F04/A"]
    assert (refund.amount_minor, refund.currency) == (9000, "USD")


def test_rationale_marks_fixture(provider):
    assert all(d.rationale.startswith(FIXTURE_PREFIX) for d in provider.decisions.values())


def test_unknown_label(provider, scenarios):
    s = scenarios["F01"]
    with pytest.raises(KeyError, match="F01/X"):
        provider.decide(_req(s, "X", 0, s.t0.state))
