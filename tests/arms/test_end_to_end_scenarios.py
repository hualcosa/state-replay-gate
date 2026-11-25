"""Scripted run over 2a's canonical scenarios reproduces §6's C/D totals. Skips until 2a."""

from __future__ import annotations

from pathlib import Path

import pytest

from srg.arms import run_scenarios, verify_artifact
from srg.scenarios import load_scenarios
from srg.scripted import SCRIPTED_MODEL, ScriptedProvider

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"
HAS_SCENARIOS = SCENARIOS_DIR.is_dir() and any(SCENARIOS_DIR.glob("S*.json"))

pytestmark = pytest.mark.skipif(not HAS_SCENARIOS, reason="scenarios/S*.json not present (2a)")


@pytest.fixture(scope="module")
def artifact():
    scenarios = load_scenarios(SCENARIOS_DIR)
    return run_scenarios(
        scenarios, ScriptedProvider.from_expected(scenarios), model=SCRIPTED_MODEL
    )


def test_section6_totals(artifact):
    c = artifact.summary.by_arm["C"]
    d = artifact.summary.by_arm["D"]
    assert len(artifact.scenarios) == 12
    assert (c.wrong_payments, c.wrong_payment_minor, c.false_stops, c.correct_payments) == (
        9,
        167050,
        1,
        2,
    )
    assert (d.wrong_payments, d.wrong_payment_minor, d.false_stops, d.correct_payments) == (
        1,
        9500,
        1,
        2,
    )
    assert d.correct_stops == 8
    dispositions = [r.arms["D"].outcome.disposition for r in artifact.scenarios]
    assert dispositions.count("blocked") == 7
    assert dispositions.count("held_for_review") == 1
    assert artifact.summary.t0_eval_passed == 12


def test_c_and_d_equal_expected_and_verify_passes(artifact):
    for run in artifact.scenarios:
        exp = run.scenario.expected
        assert run.arms["C"].outcome.classification == exp.arm_c, run.scenario.id
        assert run.arms["D"].outcome.classification == exp.arm_d, run.scenario.id
    assert verify_artifact(artifact) == ([], [], [])


def test_hero_and_honesty_cases(artifact):
    runs = {r.scenario.id: r for r in artifact.scenarios}
    s01 = runs["S01"]
    assert s01.arms["C"].outcome.harm.type == "double_payment"
    assert s01.arms["C"].outcome.harm.amount_minor == 34000
    assert s01.arms["D"].outcome.disposition == "blocked"
    assert s01.arms["D"].gate.failed_rules == ["no_open_chargeback"]
    for sid in ("S09", "S10"):
        assert runs[sid].arms["D"].gate.verdict == "allow"
    assert runs["S11"].arms["D"].gate is None
    assert runs["S11"].arms["D"].outcome.classification == "false_stop"
    assert runs["S12"].arms["D"].outcome.classification == "wrong_payment"
