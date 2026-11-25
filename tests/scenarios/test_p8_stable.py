"""Review round 3, finding 2: a mutation must not change P8 (refund window) eligibility.

Reproduction: S09 with only ``/policy/refund_window_days`` replaced 30 -> 1 was schema-valid; the
policy denies on P8 at T1 but D, which has no P8 rule, allowed and paid. Such a scenario is now
rejected by the loader, the runner and ``verify``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from srg.arms import run_scenario, verify_artifact
from srg.contracts import PatchOp, Scenario
from srg.scenarios import load_scenario, load_scenarios, p8_eligible
from srg.scripted import ScriptedProvider
from srg.state import apply_patch

WINDOW_OP = {"op": "replace", "path": "/policy/refund_window_days", "value": 1}


def _s09_path(scenarios_dir: Path) -> Path:
    return next(scenarios_dir.glob("S09-*.json"))


def _drifted_doc(scenarios_dir: Path) -> dict:
    doc = json.loads(_s09_path(scenarios_dir).read_text())
    doc["mutation"].update(kind="benign_change", ops=[WINDOW_OP])
    return doc


def test_all_committed_scenarios_keep_p8(scenarios_dir):
    scenarios = load_scenarios(scenarios_dir)
    assert [s.id for s in scenarios] == [f"S{i:02d}" for i in range(1, 13)]
    for sc in scenarios:
        s1 = apply_patch(sc.t0.state, sc.mutation.ops)
        assert p8_eligible(sc.ticket_id, sc.t0.state) == p8_eligible(sc.ticket_id, s1), sc.id


def test_loader_rejects_s09_with_window_shrunk_to_one_day(scenarios_dir, tmp_path):
    path = tmp_path / "S09-drift.json"
    path.write_text(json.dumps(_drifted_doc(scenarios_dir)))
    with pytest.raises(ValueError, match=r"S09: the mutation changes P8 eligibility") as exc:
        load_scenario(path)
    assert "eligible at T0, ineligible at T1" in str(exc.value)


def test_runner_and_verify_reject_the_drifted_scenario(scenarios_dir):
    good = load_scenario(_s09_path(scenarios_dir))
    run = run_scenario(good, ScriptedProvider.from_expected([good]))
    drifted = Scenario.model_validate(_drifted_doc(scenarios_dir))  # schema-valid on its own
    with pytest.raises(ValueError, match="P8 eligibility"):
        run_scenario(drifted, ScriptedProvider.from_expected([drifted]))

    from srg.arms import run_scenarios

    art = run_scenarios([good], ScriptedProvider.from_expected([good]), model="m")
    assert verify_artifact(art)[0] == []
    s1 = apply_patch(drifted.t0.state, [PatchOp(**WINDOW_OP)])
    tampered = art.model_copy(deep=True)
    tampered.scenarios[0] = run.model_copy(update={"scenario": drifted, "t1_state": s1})
    assert any("P8 eligibility" in p for p in verify_artifact(tampered)[0])


def test_p8_boundary_matches_the_policy_text(scenarios_dir):
    sc = load_scenario(_s09_path(scenarios_dir))  # opened 4 days 17h after delivery
    for days, eligible in ((5, True), (4, True), (3, False)):
        s = apply_patch(sc.t0.state, [PatchOp(op="replace", path=WINDOW_OP["path"], value=days)])
        assert p8_eligible(sc.ticket_id, s) is eligible, days
