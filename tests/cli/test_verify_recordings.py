"""Review round 3, finding 3: ``srg verify`` alone is an internal-consistency check.

Reproduction: swapping S01/A and S01/B's request hashes, or replacing S01/A's rationale with
text absent from the recording, still printed ``verify ok``. ``--recordings DIR`` rebuilds each
request, checks its hash, replays the recording and compares the decision, so both now fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from srg.arms import run_scenarios
from srg.canonical import pretty_json
from srg.cli import EXIT_ERROR, EXIT_OK, main
from srg.contracts import RunArtifact
from srg.provenance import verify_recordings
from srg.scenarios import load_scenarios


@pytest.fixture(scope="module")
def real(repo_root: Path) -> RunArtifact:
    from srg.agent import RefundAgent

    agent = RefundAgent.from_env(mode="replay", recordings_dir=repo_root / "recordings")
    return run_scenarios(load_scenarios(repo_root / "scenarios"), agent, model=agent.model)


def _s01(art: RunArtifact):
    return next(r for r in art.scenarios if r.scenario.id == "S01")


def _swap_ab_hashes(art: RunArtifact) -> RunArtifact:
    t = art.model_copy(deep=True)
    a, b = _s01(t).arms["A"].llm, _s01(t).arms["B"].llm
    a.request_hash, b.request_hash = b.request_hash, a.request_hash
    return t


def _substitute_rationale(art: RunArtifact) -> RunArtifact:
    t = art.model_copy(deep=True)
    arm = _s01(t).arms["A"]
    arm.decision = arm.decision.model_copy(update={"rationale": "Not what the model said."})
    return t


def _write(tmp_path: Path, art: RunArtifact) -> str:
    path = tmp_path / "run.json"
    path.write_text(pretty_json(art), encoding="utf-8")
    return str(path)


def test_real_artifact_matches_the_committed_recordings(real, repo_root, tmp_path, capsys):
    assert verify_recordings(real, repo_root / "recordings") == []
    rec = str(repo_root / "recordings")
    assert main(["verify", _write(tmp_path, real), "--recordings", rec]) == EXIT_OK
    out = capsys.readouterr().out
    assert "verify ok" in out and "every LLM call matches the recordings" in out


@pytest.mark.parametrize("tamper", [_swap_ab_hashes, _substitute_rationale])
def test_reviewer_tampering_passes_consistency_but_fails_provenance(
    real, repo_root, tmp_path, capsys, tamper
):
    path = _write(tmp_path, tamper(real))
    # Without --recordings the artifact is internally consistent; the output says what that means.
    assert main(["verify", path]) == EXIT_OK
    out = capsys.readouterr().out
    assert "verify ok" in out and "recording provenance not checked" in out
    assert main(["verify", path, "--recordings", str(repo_root / "recordings")]) == EXIT_ERROR
    out = capsys.readouterr().out
    assert "verify FAILED" in out and "S01/A" in out and "verify ok" not in out


def test_problem_messages_name_the_cause(real, repo_root):
    rec = repo_root / "recordings"
    swapped = verify_recordings(_swap_ab_hashes(real), rec)
    assert any(p.startswith("S01/A: request_hash") for p in swapped)
    assert any(p.startswith("S01/B: request_hash") for p in swapped)
    substituted = verify_recordings(_substitute_rationale(real), rec)
    assert substituted == ["S01/A: decision differs from the recorded response (rationale)"]


def test_missing_recordings_are_reported_offline(real, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    problems = verify_recordings(real, empty)
    assert len(problems) == 4 * len(real.scenarios)
    assert all("no recording for" in p for p in problems)


def test_help_describes_verify_as_internal_consistency(capsys):
    with pytest.raises(SystemExit):
        main(["verify", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "internal consistency check of the artifact, not proof of recording provenance" in out
    assert "--recordings DIR" in out
