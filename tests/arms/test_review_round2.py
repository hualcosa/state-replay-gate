"""Review round 2 reproductions (findings 2, 3, 4, 6, 7) on the real recorded run.

The real artifact is rebuilt offline from the committed recordings (replay only: a recording
miss raises, no provider is contacted).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from srg.arms import run_scenario, run_scenarios, verify_artifact
from srg.canonical import pretty_json
from srg.cli import main
from srg.contracts import PatchOp, RunArtifact
from srg.outcome import summarize
from srg.render import make_console, render_run
from srg.scenarios import load_scenarios
from srg.scripted import ScriptedProvider


@pytest.fixture(scope="module")
def real(repo_root: Path) -> RunArtifact:
    from srg.agent import RefundAgent

    agent = RefundAgent.from_env(mode="replay", recordings_dir=repo_root / "recordings")
    return run_scenarios(load_scenarios(repo_root / "scenarios"), agent, model=agent.model)


@pytest.fixture
def tampered(real: RunArtifact) -> RunArtifact:
    return real.model_copy(deep=True)


def _by_id(artifact: RunArtifact):
    return {r.scenario.id: r for r in artifact.scenarios}


def test_real_run_verifies_and_keeps_the_committed_split(real):
    assert verify_artifact(real) == ([], [], [])
    reg = real.summary.regressions
    assert (reg.scenarios_with_state_change, reg.changed_on_live_state) == (11, 10)
    assert reg.changed_on_frozen_state == 0
    # no committed scenario has ops but an empty net diff
    for run in real.scenarios:
        assert bool(run.scenario.mutation.ops) == bool(run.state_diff), run.scenario.id


# --- finding 2: the shared schema enforces the action-dependent amount/currency rule ---------


def _s01_c_amount_null(real: RunArtifact) -> dict:
    data = json.loads(pretty_json(real))
    s01 = next(r for r in data["scenarios"] if r["scenario"]["id"] == "S01")
    assert s01["arms"]["C"]["decision"]["action"] == "issue_refund"
    s01["arms"]["C"]["decision"]["amount_minor"] = None
    return data


def _schema_errors(schemas_dir: Path, data: dict) -> list:
    schema = json.loads((schemas_dir / "run-artifact.schema.json").read_text())
    return list(jsonschema.Draft202012Validator(schema).iter_errors(data))


def test_schema_and_cli_reject_the_same_specimen(real, schemas_dir, tmp_path, capsys):
    assert _schema_errors(schemas_dir, json.loads(pretty_json(real))) == []
    data = _s01_c_amount_null(real)
    assert _schema_errors(schemas_dir, data), "schema accepted issue_refund with null amount"
    path = tmp_path / "s01c.json"
    path.write_text(json.dumps(data))
    capsys.readouterr()
    assert main(["validate", str(path)]) == 3
    assert "issue_refund requires both amount_minor and currency" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("action", "amount", "currency"),
    [
        ("issue_refund", 0, "USD"),  # amount must be >= 1
        ("issue_refund", 100, "usd"),  # currency pattern
        ("issue_refund", 100, None),
        ("deny_refund", 100, None),  # non-refund actions carry nulls
        ("escalate_to_human", None, "USD"),
    ],
)
def test_schema_enforces_every_branch_of_the_rule(real, schemas_dir, action, amount, currency):
    data = json.loads(pretty_json(real))
    decision = data["scenarios"][0]["arms"]["C"]["decision"]
    decision.update(action=action, amount_minor=amount, currency=currency)
    assert _schema_errors(schemas_dir, data)


# --- finding 3: whole T0Eval and the reporting currency --------------------------------------


def test_verify_flags_a_wrong_run_currency(tampered):
    tampered.run.currency = "JPY"
    problems, *_ = verify_artifact(tampered)
    assert any(p.startswith("run.currency 'JPY'") for p in problems), problems


def test_verify_flags_disagreeing_refund_currencies(tampered):
    c = _by_id(tampered)["S01"].arms["C"].decision
    c.currency = "CAD"
    problems, *_ = verify_artifact(tampered)
    assert any("disagree on currency" in p for p in problems), problems


@pytest.mark.parametrize(
    ("field", "value"),
    [("expected_action", "deny_refund"), ("expected_amount_minor", 1), ("passed", False)],
)
def test_verify_compares_the_whole_t0_eval(tampered, field, value):
    setattr(_by_id(tampered)["S01"].t0.eval, field, value)
    problems, *_ = verify_artifact(tampered)
    assert any(p.startswith("S01: t0.eval") for p in problems), problems


# --- finding 4: arm inputs (request hashes and memory notes) ---------------------------------


def test_verify_flags_cross_arm_hash_substitution(tampered):
    s09 = _by_id(tampered)["S09"]
    assert s09.arms["A"].llm.sample == 2  # unchanged state
    s09.arms["A"].llm.request_hash = s09.arms["B"].llm.request_hash
    problems, *_ = verify_artifact(tampered)
    assert any(p.startswith("S09/A: state unchanged") for p in problems), problems


def test_verify_flags_a_reusing_t0_on_changed_state(tampered):
    s01 = _by_id(tampered)["S01"]
    s01.arms["A"].llm.request_hash = s01.t0.llm.request_hash
    problems, *_ = verify_artifact(tampered)
    assert any(p.startswith("S01/A: state changed") for p in problems), problems


def test_verify_flags_b_reusing_t0_request(tampered):
    s01 = _by_id(tampered)["S01"]
    s01.arms["B"].llm.request_hash = s01.t0.llm.request_hash
    problems, *_ = verify_artifact(tampered)
    assert any(p.startswith("S01/B: memory-note request") for p in problems), problems


def test_verify_flags_a_removed_b_note(tampered):
    _by_id(tampered)["S01"].arms["B"].memory_note = None
    problems, *_ = verify_artifact(tampered)
    assert any(p.startswith("S01/B: memory_note") for p in problems), problems


@pytest.mark.parametrize("arm", ["A", "C", "D"])
def test_verify_flags_a_note_on_other_arms(tampered, arm):
    run = _by_id(tampered)["S01"]
    run.arms[arm].memory_note = run.t0.decision.memory_note
    problems, *_ = verify_artifact(tampered)
    assert any(p.startswith(f"S01/{arm}: memory_note must be null") for p in problems), problems


# --- finding 6: the changed-world population is the net state diff ---------------------------


def test_noop_patch_counts_as_unchanged(repo_root):
    """S09 with a valid ``captured -> captured`` patch: empty diff, A sample 2, not in the split."""
    s09 = load_scenarios(repo_root / "scenarios")[8]
    assert s09.id == "S09" and s09.t0.state.order.payment.status == "captured"
    noop = s09.model_copy(deep=True)
    noop.mutation.kind = "benign_change"
    noop.mutation.ops = [PatchOp(op="replace", path="/order/payment/status", value="captured")]
    provider = ScriptedProvider.from_expected([noop])
    flip = provider.decisions["S09/t0"].model_copy(
        update={"action": "deny_refund", "amount_minor": None, "currency": None}
    )
    provider.decisions["S09/A"] = flip  # a scripted A flip on the unchanged world
    run = run_scenario(noop, provider)
    assert run.state_diff == []
    assert run.arms["A"].llm.sample == 2
    assert run.arms["A"].decision_changed_vs_t0
    reg = summarize([run]).regressions
    assert (reg.scenarios_with_state_change, reg.changed_on_live_state) == (0, 0)


# --- finding 7 (CLI part): honest labels -----------------------------------------------------


def test_render_labels_harm_and_simulated_latency(real, fixture_scenarios_dir, capsys):
    render_run(make_console(), real, out="runs/x.json")
    out = capsys.readouterr().out
    assert "Wrong payments (harm)" in out
    assert "simulated" not in out
    scenarios = load_scenarios(fixture_scenarios_dir)
    scripted = run_scenarios(
        scenarios, ScriptedProvider.from_expected(scenarios), model="fixture-scripted"
    )
    render_run(make_console(), scripted, out="runs/x.json")
    assert "simulated (scripted)" in capsys.readouterr().out
