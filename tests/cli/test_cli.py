"""End-to-end CLI over F01-F05 with the scripted provider; exit codes per §5.6."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from srg.cli import main
from srg.contracts import DecisionRequest, RecordingMissError, RunArtifact


@pytest.fixture
def fx(fixture_scenarios_dir: Path) -> list[str]:
    return ["--scenarios-dir", str(fixture_scenarios_dir)]


def run_scripted(fx: list[str], out: Path, *extra: str) -> int:
    return main(["run", "--provider", "scripted", *fx, "--out", str(out), *extra])


def test_run_is_byte_identical_and_verifies(fx, tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "nested" / "b.json"
    assert run_scripted(fx, a) == 0
    out = capsys.readouterr().out
    assert "Arm scoreboard" in out and "Regression split" in out and "F01" in out
    assert run_scripted(fx, b) == 0
    assert a.read_bytes() == b.read_bytes()
    art = RunArtifact.model_validate_json(a.read_bytes())
    for run in art.scenarios:
        assert run.arms["C"].outcome.classification == run.scenario.expected.arm_c
        assert run.arms["D"].outcome.classification == run.scenario.expected.arm_d
        gate = run.arms["D"].gate
        assert gate is None or gate.latency_us is None
    assert art.run.timings_included is False
    capsys.readouterr()
    assert main(["validate", str(a)]) == 0
    assert main(["verify", str(a)]) == 0
    assert "verify ok" in capsys.readouterr().out


def test_with_timings(fx, tmp_path):
    out = tmp_path / "t.json"
    assert run_scripted(fx, out, "--with-timings") == 0
    art = RunArtifact.model_validate_json(out.read_bytes())
    assert art.run.timings_included is True
    gates = [r.arms["D"].gate for r in art.scenarios if r.arms["D"].gate is not None]
    assert gates and all(isinstance(g.latency_us, int) for g in gates)
    assert main(["verify", str(out)]) == 0  # timings do not break verification


def test_json_prints_clean_summary(fx, tmp_path, capsys):
    out = tmp_path / "j.json"
    assert run_scripted(fx, out, "--json") == 0
    printed = capsys.readouterr().out
    art = RunArtifact.model_validate_json(out.read_bytes())
    assert json.loads(printed) == art.summary.model_dump(mode="json")
    assert "\x1b[" not in printed


def test_scenario_filter_and_unknown_id(fx, tmp_path, capsys):
    out = tmp_path / "f.json"
    assert run_scripted(fx, out, "--scenario", "F03", "--scenario", "F01") == 0
    art = RunArtifact.model_validate_json(out.read_bytes())
    assert [r.scenario.id for r in art.scenarios] == ["F01", "F03"]
    assert main(["verify", str(out)]) == 0
    assert run_scripted(fx, out, "--scenario", "F99") == 1
    assert "unknown scenario id" in capsys.readouterr().err


def test_tampered_classification_fails_verify(fx, tmp_path, capsys):
    out = tmp_path / "x.json"
    assert run_scripted(fx, out) == 0
    data = json.loads(out.read_text())
    data["scenarios"][0]["arms"]["D"]["outcome"]["classification"] = "wrong_payment"
    out.write_text(json.dumps(data))
    assert main(["validate", str(out)]) == 0  # still contract-valid
    capsys.readouterr()
    assert main(["verify", str(out)]) == 1
    assert "F01/D" in capsys.readouterr().out


def test_corrupted_file_exits_3(fx, tmp_path, capsys):
    out = tmp_path / "c.json"
    assert run_scripted(fx, out) == 0
    out.write_text(out.read_text()[:-200])
    assert main(["validate", str(out)]) == 3
    assert main(["verify", str(out)]) == 3
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{}")  # valid JSON, not a RunArtifact
    assert main(["validate", str(bad_path)]) == 3
    assert "contract validation error" in capsys.readouterr().err


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda d: d["scenarios"][0]["t0"]["eval"].update(passed="true"), id="str-bool"
        ),
        pytest.param(lambda d: d["summary"].update(t0_eval_total="5"), id="str-int"),
        pytest.param(lambda d: d["scenarios"][0]["arms"].pop("D"), id="missing-arm"),
        pytest.param(lambda d: d["summary"]["by_arm"].pop("B"), id="missing-by-arm"),
    ],
)
def test_loosely_typed_or_incomplete_artifact_exits_3(fx, tmp_path, capsys, mutate):
    """Review round 1, finding 4: the CLI reads artifacts with strict JSON types."""
    out = tmp_path / "s.json"
    assert run_scripted(fx, out) == 0
    data = json.loads(out.read_text())
    mutate(data)
    out.write_text(json.dumps(data))
    capsys.readouterr()
    assert main(["validate", str(out)]) == 3
    assert main(["verify", str(out)]) == 3
    assert "contract validation error" in capsys.readouterr().err


def test_missing_file_exits_1(tmp_path, capsys):
    assert main(["validate", str(tmp_path / "nope.json")]) == 1
    assert "file not found" in capsys.readouterr().err


class MissProvider:
    model = "m"

    def decide(self, req: DecisionRequest):
        raise RecordingMissError(req.label, "sha256:" + "ab" * 32, req.sample)


def test_recording_miss_exits_2(fx, tmp_path, capsys):
    out = tmp_path / "m.json"
    assert main(["run", *fx, "--out", str(out)], provider=MissProvider()) == 2
    err = capsys.readouterr().err
    assert "label=F01/t0" in err and "sample=0" in err and "ab" * 32 in err
    assert not out.exists()


def test_invalid_scenario_file_exits_3(fixture_scenarios_dir, tmp_path, capsys):
    bad_dir = tmp_path / "sc"
    bad_dir.mkdir()
    data = json.loads((fixture_scenarios_dir / "F01-hero-chargeback.json").read_text())
    data["id"] = "X1"
    (bad_dir / "F01.json").write_text(json.dumps(data))
    assert main(["scenarios", "--scenarios-dir", str(bad_dir)]) == 3
    assert "F01.json" in capsys.readouterr().err


def test_missing_scenarios_dir_exits_1(tmp_path, capsys):
    assert main(["scenarios", "--scenarios-dir", str(tmp_path / "none")]) == 1


def test_scenarios_and_story(fx, capsys):
    assert main(["scenarios", *fx]) == 0
    out = capsys.readouterr().out
    for sid in ("F01", "F02", "F03", "F04", "F05"):
        assert sid in out
    assert main(["story", "F01", *fx]) == 0
    out = capsys.readouterr().out
    assert "Chargeback opened" in out and "/disputes/0" in out and "no_open_chargeback" in out
    assert main(["story", "F99", *fx]) == 1


@pytest.mark.skipif(
    importlib.util.find_spec("srg.agent") is not None, reason="srg.agent present (2b landed)"
)
def test_agent_provider_missing_is_a_clean_error(fx, tmp_path, capsys):
    assert main(["run", *fx, "--out", str(tmp_path / "a.json")]) == 1
    assert "srg.agent" in capsys.readouterr().err


def test_cli_import_does_not_load_agent():
    code = "import sys, srg.cli; print('srg.agent' in sys.modules, 'srg.llm' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False False"


def test_python_dash_m(fixture_scenarios_dir):
    result = subprocess.run(
        [sys.executable, "-m", "srg", "scenarios", "--scenarios-dir", str(fixture_scenarios_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "F05" in result.stdout
