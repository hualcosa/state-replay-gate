"""Arm runner, ledger, outcome and summary on the F01-F05 fixtures with the scripted provider."""

from __future__ import annotations

from pathlib import Path

import pytest

import srg.arms as arms_mod
from srg.arms import run_scenario, run_scenarios, verify_artifact
from srg.canonical import digest
from srg.contracts import (
    AgentDecision,
    DecisionRequest,
    DecisionResult,
    RunArtifact,
    compute_run_id,
)
from srg.ledger import execute
from srg.outcome import classify, disposition_for, nearest_rank, summarize
from srg.scenarios import load_scenarios
from srg.scripted import SCRIPTED_MODEL, ScriptedProvider


@pytest.fixture(scope="module")
def scenarios(fixture_scenarios_dir: Path):
    return load_scenarios(fixture_scenarios_dir)


@pytest.fixture(scope="module")
def artifact(scenarios) -> RunArtifact:
    return run_scenarios(
        scenarios, ScriptedProvider.from_expected(scenarios), model=SCRIPTED_MODEL
    )


def by_id(artifact: RunArtifact):
    return {r.scenario.id: r for r in artifact.scenarios}


class RecordingProvider:
    """Wraps a provider and remembers every request, in call order."""

    def __init__(self, inner, *, tokens: tuple[int, int] | None = None, latency: int = 0):
        self.inner = inner
        self.requests: list[DecisionRequest] = []
        self.tokens = tokens
        self.latency = latency

    def decide(self, req: DecisionRequest) -> DecisionResult:
        self.requests.append(req)
        result = self.inner.decide(req)
        if self.tokens is None and not self.latency:
            return result
        llm = result.llm.model_copy(
            update={
                "prompt_tokens": self.tokens[0] if self.tokens else None,
                "completion_tokens": self.tokens[1] if self.tokens else None,
                "latency_ms": self.latency + len(self.requests),
            }
        )
        return DecisionResult(decision=result.decision, llm=llm)


def test_c_and_d_match_expected(artifact):
    for run in artifact.scenarios:
        exp = run.scenario.expected
        assert run.arms["C"].outcome.classification == exp.arm_c, run.scenario.id
        assert run.arms["D"].outcome.classification == exp.arm_d, run.scenario.id
        gate = run.arms["D"].gate
        got = (gate.verdict, gate.failed_rules) if gate else None
        want = (exp.gate.verdict, exp.gate.failed_rules) if exp.gate else None
        assert got == want, run.scenario.id
        assert run.t0.eval.passed


def test_call_order_samples_and_contexts(scenarios):
    provider = RecordingProvider(ScriptedProvider.from_expected(scenarios))
    for s in scenarios:
        run_scenario(s, provider)
    labels = [r.label for r in provider.requests]
    expected_labels = [f"{s.id}/{arm}" for s in scenarios for arm in ("t0", "C", "A", "B")]
    assert labels == expected_labels
    reqs = {r.label: r for r in provider.requests}
    for s in scenarios:
        t0, c, a, b = (reqs[f"{s.id}/{x}"] for x in ("t0", "C", "A", "B"))
        assert (t0.sample, c.sample, b.sample) == (0, 1, 0)
        assert a.sample == (2 if s.id == "F03" else 0)
        assert c.state == t0.state == s.t0.state  # frozen: byte-identical request
        assert a.context_kind == "state"
        assert (a.state == s.t0.state) == (s.id == "F03")  # only F03 has no state change
        assert b.context_kind == "memory_note" and b.state is None


def test_hashes_and_llm_refs(artifact):
    for run in artifact.scenarios:
        c, a, d = run.arms["C"], run.arms["A"], run.arms["D"]
        assert c.llm.request_hash == run.t0.llm.request_hash
        assert c.llm.sample == 1
        assert a.llm.sample == (2 if run.scenario.id == "F03" else 0)
        if run.scenario.id == "F03":
            assert a.llm.request_hash == run.t0.llm.request_hash
        else:
            assert a.llm.request_hash != run.t0.llm.request_hash
        assert d.llm is None
        assert d.decision == c.decision
        assert run.arms["B"].memory_note == run.t0.decision.memory_note
        assert [run.arms[x].memory_note for x in "ACD"] == [None, None, None]
        assert [run.arms[x].t1_context for x in "ABCD"] == [
            "live_state",
            "memory_note",
            "frozen_state",
            "frozen_state",
        ]


def test_stale_denial_has_no_gate(artifact):
    f04 = by_id(artifact)["F04"]
    d = f04.arms["D"]
    assert d.gate is None
    assert d.outcome.disposition == "denied"
    assert d.outcome.classification == "false_stop"


def test_hero_blocked_and_c_pays_twice(artifact):
    f01 = by_id(artifact)["F01"]
    c, d = f01.arms["C"], f01.arms["D"]
    assert c.outcome.disposition == "paid"
    assert c.outcome.harm.type == "double_payment"
    assert c.outcome.harm.amount_minor == 25000
    assert d.outcome.disposition == "blocked"
    assert d.gate.failed_rules == ["no_open_chargeback"]
    assert d.gate.results[2].observed["chargeback_open"] is True
    assert d.execution.executed is False and d.execution.refund is None
    f02 = by_id(artifact)["F02"]
    assert f02.arms["D"].outcome.disposition == "held_for_review"


def test_arms_are_isolated(artifact):
    f03 = by_id(artifact)["F03"]  # every arm pays $40 on the same order
    for arm in "ABCD":
        ex = f03.arms[arm].execution
        assert ex.executed
        assert ex.refund.refund_id == f"rf-{arm.lower()}-TCK-F31"
        assert ex.refund.issued_by == f"refund-agent/arm-{arm}"
        assert ex.total_refunded_minor == 4000  # never sees another arm's write
    assert f03.t1_state.refunds == []


def test_ledger_never_mutates_its_input(scenarios):
    s = scenarios[2]
    state = s.t0.state
    before = state.model_dump(mode="json")
    decision = ScriptedProvider.from_expected([s]).decisions[f"{s.id}/C"]
    ex = execute(decision, state, arm="A", at="2026-10-12T14:00:00Z", allowed=True)
    assert ex.executed and ex.refund.issued_at == "2026-10-12T14:00:00Z"
    assert ex.refund.note == decision.reason
    assert state.model_dump(mode="json") == before
    stopped = execute(decision, state, arm="D", at="x", allowed=False)
    assert not stopped.executed and stopped.total_refunded_minor == 0


def test_d_gate_reads_s1_not_s0(scenarios, monkeypatch):
    seen = []
    real = arms_mod.evaluate_gate

    def spy(action, reader, **kw):
        seen.append(reader)
        return real(action, reader, **kw)

    monkeypatch.setattr(arms_mod, "evaluate_gate", spy)
    f01 = scenarios[0]
    run = run_scenario(f01, ScriptedProvider.from_expected(scenarios))
    assert len(seen) == 1
    assert seen[0].state_digest == digest(run.t1_state)
    assert seen[0].state_digest != digest(f01.t0.state)
    assert seen[0].at == f01.mutation.at
    assert run.arms["D"].gate.state_digest == digest(run.t1_state)


def test_decision_changed_vs_t0(artifact):
    changed = {
        (r.scenario.id, arm) for r in artifact.scenarios for arm in "ABCD"
        if r.arms[arm].decision_changed_vs_t0
    }
    assert changed == {("F01", "A"), ("F02", "A"), ("F04", "A")}


def test_run_info_and_fixture_parity(artifact, scenarios, run_fixture_path: Path):
    info = artifact.run
    assert info.model == SCRIPTED_MODEL
    assert info.llm_mode == "replay"
    assert info.recorded_at is None
    assert info.currency == "USD"
    assert info.timings_included is False
    hashes = arms_mod.request_hashes(artifact.scenarios)
    assert info.run_id == compute_run_id(scenarios, hashes, SCRIPTED_MODEL)
    fixture = RunArtifact.model_validate_json(run_fixture_path.read_bytes())
    assert info.run_id == fixture.run.run_id
    assert artifact.summary == fixture.summary


def test_summarize_recomputes_committed_fixture(run_fixture_path: Path):
    fixture = RunArtifact.model_validate_json(run_fixture_path.read_bytes())
    assert summarize(fixture.scenarios) == fixture.summary


def test_summary_counts(artifact):
    by = artifact.summary.by_arm
    assert (by["C"].wrong_payments, by["C"].wrong_payment_minor) == (3, 25000 + 30000 + 6000)
    assert (by["D"].wrong_payments, by["D"].wrong_payment_minor) == (1, 6000)
    assert (by["D"].correct_stops, by["D"].false_stops, by["D"].review_load) == (2, 1, 1)
    assert by["A"].review_load == 1  # F02 escalated
    assert [by[a].llm_calls_at_t1 for a in "ABCD"] == [5, 5, 5, 0]
    assert by["D"].llm_latency_ms_p50 is None
    assert by["A"].prompt_tokens_at_t1 is None  # scripted calls carry no usage
    reg = artifact.summary.regressions
    assert (reg.scenarios_with_state_change, reg.changed_on_live_state) == (4, 3)
    assert reg.changed_on_frozen_state == 0


def test_tokens_and_latency_when_usage_present(scenarios):
    provider = RecordingProvider(
        ScriptedProvider.from_expected(scenarios), tokens=(100, 10), latency=50
    )
    art = run_scenarios(scenarios, provider, model="m")
    a = art.summary.by_arm["A"]
    assert (a.prompt_tokens_at_t1, a.completion_tokens_at_t1) == (500, 50)
    lat = sorted(x.arms["A"].llm.latency_ms for x in art.scenarios)
    assert a.llm_latency_ms_p50 == lat[2] and a.llm_latency_ms_p95 == lat[4]


def test_recorded_at_and_timings(scenarios):
    provider = ScriptedProvider.from_expected(scenarios)
    provider.latest_recorded_at = lambda: "2026-11-19T10:00:00Z"
    art = run_scenarios(scenarios, provider, model="m", llm_mode="record", timed=True)
    assert art.run.recorded_at == "2026-11-19T10:00:00Z"
    assert art.run.llm_mode == "record"
    assert art.run.timings_included is True
    for run in art.scenarios:
        gate = run.arms["D"].gate
        if gate is not None:
            assert isinstance(gate.latency_us, int)


def test_nearest_rank_and_classify():
    assert nearest_rank([], 50) is None
    assert nearest_rank([7], 95) == 7
    assert nearest_rank([1, 2, 3, 4], 50) == 2
    assert nearest_rank([5, 1, 4, 2, 3], 95) == 5
    assert nearest_rank(list(range(1, 21)), 95) == 19
    assert classify(True, True) == "correct_payment"
    assert classify(True, False) == "wrong_payment"
    assert classify(False, True) == "false_stop"
    assert classify(False, False) == "correct_stop"


def test_unexecuted_refund_without_gate_is_an_error(scenarios):
    decision: AgentDecision = ScriptedProvider.from_expected(scenarios).decisions["F01/C"]
    ex = execute(decision, scenarios[0].t0.state, arm="C", at="x", allowed=False)
    with pytest.raises(ValueError):
        disposition_for(decision, ex, None)


def test_verify_passes_on_a_real_run_and_flags_tampering(artifact):
    assert verify_artifact(artifact) == ([], [], [])
    tampered = artifact.model_copy(deep=True)
    tampered.scenarios[0].arms["C"].outcome.classification = "correct_stop"
    problems, *_ = verify_artifact(tampered)
    assert any("F01/C" in p for p in problems)
    assert any("summary" in p for p in problems)


def test_verify_reports_true_regressions_without_failing(scenarios):
    provider = ScriptedProvider.from_expected(scenarios)
    deny = provider.decisions["F01/t0"].model_copy(
        update={"action": "deny_refund", "amount_minor": None, "currency": None}
    )
    provider.decisions["F01/C"] = deny
    art = run_scenarios(scenarios, provider, model=SCRIPTED_MODEL)
    problems, regressions, _ = verify_artifact(art)
    assert problems == []
    assert len(regressions) == 1 and regressions[0].startswith("F01:")
    assert art.summary.regressions.changed_on_frozen_state == 1


def test_regression_split_counts_only_state_changed_scenarios(scenarios):
    """Review round 1, finding 3: a flip on an unchanged control (F03) is not in the split."""
    provider = ScriptedProvider.from_expected(scenarios)
    deny = provider.decisions["F03/t0"].model_copy(
        update={"action": "deny_refund", "amount_minor": None, "currency": None}
    )
    provider.decisions["F03/A"] = deny
    provider.decisions["F03/C"] = deny
    art = run_scenarios(scenarios, provider, model=SCRIPTED_MODEL)
    f03 = by_id(art)["F03"]
    assert f03.scenario.mutation.ops == []
    assert f03.arms["A"].decision_changed_vs_t0 and f03.arms["C"].decision_changed_vs_t0
    reg = art.summary.regressions
    assert (reg.scenarios_with_state_change, reg.changed_on_live_state) == (4, 3)
    assert reg.changed_on_frozen_state == 0
    problems, regressions, _ = verify_artifact(art)
    assert problems == []
    assert [r[:4] for r in regressions] == ["F03:"]  # still reported, just not in the split
    # a summary that counts the control flip no longer verifies
    tampered = art.model_copy(deep=True)
    tampered.summary.regressions.changed_on_live_state = 4
    assert "summary does not recompute from the scenarios" in verify_artifact(tampered)[0]


@pytest.mark.parametrize(
    ("sid", "where", "bad"),
    [
        ("F03", "A", 0),  # S1 == S0: A must use sample 2
        ("F01", "A", 2),  # S1 != S0: A must use sample 0
        ("F01", "B", 1),
        ("F01", "t0", 1),
        ("F01", "C", 0),
    ],
)
def test_verify_checks_every_c2_sample_assignment(artifact, sid, where, bad):
    """Review round 1, finding 8: verify protects T0, A and B samples, not only C's."""
    tampered = artifact.model_copy(deep=True)
    run = by_id(tampered)[sid]
    llm = run.t0.llm if where == "t0" else run.arms[where].llm
    llm.sample = bad
    problems, *_ = verify_artifact(tampered)
    assert any(p.startswith(f"{sid}/{where}:") and "sample" in p for p in problems), problems
