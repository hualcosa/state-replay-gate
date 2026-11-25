from __future__ import annotations

import json
import math
import subprocess
import sys

import jsonschema
import pytest

from srg.canonical import digest, pretty_json
from srg.contracts import ARM_SPECS, RULE_SPECS, RunArtifact, compute_run_id, same_decision
from srg.scenarios import load_scenarios
from srg.state import apply_patch, diff_states

SCHEMA_FILES = ["run-artifact.schema.json", "scenario.schema.json", "recording.schema.json"]


@pytest.fixture(scope="module")
def fixture_text(run_fixture_path):
    return run_fixture_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def artifact(fixture_text):
    return RunArtifact.model_validate_json(fixture_text)


def _validator(schemas_dir, name):
    schema = json.loads((schemas_dir / name).read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.parametrize("script", ["export_schemas.py", "make_fixture.py"])
def test_committed_outputs_are_current(repo_root, script):
    proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / script), "--check"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert proc.returncode == 0, proc.stderr


def test_fixture_validates_against_schema(schemas_dir, fixture_text):
    errors = list(_validator(schemas_dir, SCHEMA_FILES[0]).iter_errors(json.loads(fixture_text)))
    assert errors == []


def test_schema_rejects_unknown_key(schemas_dir, fixture_text):
    data = json.loads(fixture_text)
    data["scenarios"][0]["arms"]["A"]["surprise"] = 1
    assert list(_validator(schemas_dir, SCHEMA_FILES[0]).iter_errors(data))


@pytest.mark.parametrize("where", ["arms", "by_arm"])
@pytest.mark.parametrize("arm", ["A", "B", "C", "D"])
def test_schema_requires_every_arm_key(schemas_dir, fixture_text, where, arm):
    """Review round 1, finding 4: the exported schema enforces the four-arm maps like pydantic."""
    data = json.loads(fixture_text)
    target = data["scenarios"][0]["arms"] if where == "arms" else data["summary"]["by_arm"]
    del target[arm]
    errors = list(_validator(schemas_dir, SCHEMA_FILES[0]).iter_errors(data))
    assert any(e.validator == "required" and repr(arm) in e.message for e in errors)


def test_schema_rejects_string_for_bool(schemas_dir, fixture_text):
    data = json.loads(fixture_text)
    data["scenarios"][0]["t0"]["eval"]["passed"] = "false"
    assert list(_validator(schemas_dir, SCHEMA_FILES[0]).iter_errors(data))


def test_fixture_scenarios_validate_against_scenario_schema(schemas_dir, fixture_scenarios_dir):
    v = _validator(schemas_dir, "scenario.schema.json")
    for path in sorted(fixture_scenarios_dir.glob("*.json")):
        assert list(v.iter_errors(json.loads(path.read_text()))) == [], path.name


def test_recording_schema_is_valid(schemas_dir):
    _validator(schemas_dir, "recording.schema.json")


def test_fixture_round_trips(artifact, fixture_text):
    assert pretty_json(artifact) == fixture_text


def test_fixture_static_parts(artifact, fixture_scenarios_dir):
    assert artifact.arms == ARM_SPECS
    assert artifact.rules == RULE_SPECS
    assert artifact.run.model == "fixture-scripted"
    assert artifact.run.llm_mode == "replay"
    assert artifact.run.recorded_at is None and artifact.run.timings_included is False
    scenarios = load_scenarios(fixture_scenarios_dir)
    assert [r.scenario for r in artifact.scenarios] == scenarios
    for r in artifact.scenarios:
        s1 = apply_patch(r.scenario.t0.state, r.scenario.mutation.ops)
        assert r.t1_state == s1
        assert r.state_diff == diff_states(r.scenario.t0.state, s1)
    hashes = [r.t0.llm.request_hash for r in artifact.scenarios] + [
        a.llm.request_hash for r in artifact.scenarios for a in r.arms.values() if a.llm
    ]
    assert artifact.run.run_id == compute_run_id(scenarios, hashes, "fixture-scripted")


def test_fixture_matches_expected(artifact):
    for r in artifact.scenarios:
        exp = r.scenario.expected
        assert r.t0.eval.passed
        assert r.arms["C"].outcome.classification == exp.arm_c, r.scenario.id
        assert r.arms["D"].outcome.classification == exp.arm_d, r.scenario.id
        gate = r.arms["D"].gate
        if exp.gate is None:
            assert gate is None
        else:
            assert (gate.verdict, gate.failed_rules) == (exp.gate.verdict, exp.gate.failed_rules)
            assert [x.rule_id for x in gate.results] == [x.id for x in RULE_SPECS]
        assert r.arms["D"].llm is None
        assert r.arms["C"].llm.request_hash == r.t0.llm.request_hash
        assert r.arms["C"].llm.sample == 1
    samples_a = {r.scenario.id: r.arms["A"].llm.sample for r in artifact.scenarios}
    assert samples_a == {"F01": 0, "F02": 0, "F03": 2, "F04": 0, "F05": 0}


def _classify(paid, should_pay):
    return {
        (True, True): "correct_payment",
        (True, False): "wrong_payment",
        (False, True): "false_stop",
        (False, False): "correct_stop",
    }[(paid, should_pay)]


def test_fixture_outcomes_recompute(artifact):
    for r in artifact.scenarios:
        for arm_id, arm in r.arms.items():
            paid = arm.execution.executed
            assert (arm.outcome.disposition == "paid") == paid
            assert arm.outcome.classification == _classify(paid, r.scenario.expected.t1_should_pay)
            if arm.outcome.classification == "wrong_payment":
                assert arm.outcome.harm == r.scenario.expected.harm_if_paid
            else:
                assert (arm.outcome.harm.type, arm.outcome.harm.amount_minor) == ("none", 0)
            assert arm.decision_changed_vs_t0 == (not same_decision(arm.decision, r.t0.decision))
            if paid:
                assert (
                    arm.execution.refund.refund_id == f"rf-{arm_id.lower()}-{r.scenario.ticket_id}"
                )
                assert arm.execution.refund.issued_by == f"refund-agent/arm-{arm_id}"
            assert r.t1_state.refunds == []  # no arm's write leaks into t1_state


def _p(values, pct):
    if not values:
        return None
    v = sorted(values)
    return v[max(1, math.ceil(pct / 100 * len(v))) - 1]


def test_fixture_summary_recomputes(artifact):
    runs = artifact.scenarios
    s = artifact.summary
    assert (s.t0_eval_passed, s.t0_eval_total) == (sum(r.t0.eval.passed for r in runs), len(runs))
    for arm_id, got in s.by_arm.items():
        arms = [r.arms[arm_id] for r in runs]
        cls = [a.outcome.classification for a in arms]
        calls = [a.llm for a in arms if a.llm]
        assert got.scenarios == len(runs)
        assert got.correct_payments == cls.count("correct_payment")
        assert got.wrong_payments == cls.count("wrong_payment")
        assert got.correct_stops == cls.count("correct_stop")
        assert got.false_stops == cls.count("false_stop")
        assert got.wrong_payment_minor == sum(a.outcome.harm.amount_minor for a in arms)
        assert got.review_load == sum(
            a.outcome.disposition in ("escalated", "held_for_review") for a in arms
        )
        assert got.decisions_changed_vs_t0 == sum(a.decision_changed_vs_t0 for a in arms)
        assert got.llm_calls_at_t1 == len(calls)
        assert got.prompt_tokens_at_t1 is None and got.completion_tokens_at_t1 is None
        assert got.llm_latency_ms_p50 == _p([c.latency_ms for c in calls], 50)
        assert got.llm_latency_ms_p95 == _p([c.latency_ms for c in calls], 95)
    reg = s.regressions
    assert reg.scenarios_with_state_change == sum(bool(r.scenario.mutation.ops) for r in runs)
    assert reg.changed_on_live_state == sum(r.arms["A"].decision_changed_vs_t0 for r in runs)
    assert reg.changed_on_frozen_state == sum(r.arms["C"].decision_changed_vs_t0 for r in runs)
    # F01-F05 table: C 3 wrong / 1 false stop / 1 correct; D 1 wrong / 1 false / 2 stops / 1 paid.
    c, d = s.by_arm["C"], s.by_arm["D"]
    assert (c.wrong_payments, c.false_stops, c.correct_payments) == (3, 1, 1)
    assert c.wrong_payment_minor == 25000 + 30000 + 6000
    assert (d.wrong_payments, d.false_stops, d.correct_stops, d.correct_payments) == (1, 1, 2, 1)
    assert d.wrong_payment_minor == 6000


def test_state_digest_is_s1(artifact):
    for r in artifact.scenarios:
        if r.arms["D"].gate:
            assert r.arms["D"].gate.state_digest == digest(r.t1_state)
