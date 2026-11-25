"""`make record`: a full fresh session that replaces recordings/ only when complete."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import srg.record_session as rs

CANONICAL = [f"S{i:02d}" for i in range(1, 13)]


@pytest.fixture(autouse=True)
def _canonical_session(repo_root, tmp_path, monkeypatch):
    """Point the session at the committed S01-S12 and a coverage file the fake replays write."""
    monkeypatch.setattr(rs, "SCENARIOS_DIR", repo_root / "scenarios")
    monkeypatch.setattr(rs, "COVERAGE_OUT", str(tmp_path / "coverage.json"))


def _write_coverage(ids=CANONICAL) -> None:
    doc = {"scenarios": [{"scenario": {"id": i}} for i in ids]}
    Path(rs.COVERAGE_OUT).write_text(json.dumps(doc))


def _covering(rc: int = 0, ids=CANONICAL):
    """A fake replay step that writes a staged artifact with ``ids``."""

    def replay(directory: Path) -> int:
        _write_coverage(ids)
        return rc

    return replay


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "recordings"
    (target / "m").mkdir(parents=True)
    (target / "m" / "old.json").write_text("old")
    return target, tmp_path / "recordings.new"


def _writes(name: str, rc: int = 0):
    def step(directory: Path) -> int:
        assert directory.is_dir()
        (directory / "m").mkdir(exist_ok=True)
        (directory / "m" / name).write_text(name)
        return rc

    return step


def _files(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.rglob("*") if p.is_file())


def test_records_into_an_empty_staging_dir_then_replaces(tmp_path):
    target, staging = _layout(tmp_path)
    seen: list[list[str]] = []

    def record(directory: Path) -> int:
        seen.append(_files(directory))  # empty: nothing from the old set can be reused
        return _writes("new.json")(directory)

    def replay(directory: Path) -> int:
        assert directory == staging and _files(directory) == ["new.json"]
        _write_coverage()
        return 0

    verified: list[Path] = []

    def verify(directory: Path) -> int:
        verified.append(directory)
        return 0

    rc = rs.record_fresh_session(
        target, staging, record=record, replay=replay, verify=verify, log=str
    )
    assert rc == 0
    assert seen == [[]] and verified == [staging]
    assert _files(target) == ["new.json"]
    assert not staging.exists() and not (tmp_path / "recordings.old").exists()


@pytest.mark.parametrize(("record_rc", "replay_rc"), [(1, 0), (0, 2)])
def test_failed_record_or_incomplete_coverage_keeps_old_set(tmp_path, record_rc, replay_rc):
    target, staging = _layout(tmp_path)
    replayed: list[Path] = []

    def replay(directory: Path) -> int:
        replayed.append(directory)
        return replay_rc

    rc = rs.record_fresh_session(
        target,
        staging,
        record=_writes("new.json", record_rc),
        replay=replay,
        verify=lambda d: pytest.fail("verify must not run after a failed record or replay"),
        log=str,
    )
    assert rc != 0
    assert _files(target) == ["old.json"]
    assert _files(staging) == ["new.json"]  # kept for inspection
    assert replayed == ([] if record_rc else [staging])


def test_complete_session_that_fails_verify_is_not_promoted(tmp_path):
    """Review round 2, finding 5: full coverage is not acceptance; verify gates the swap."""
    target, staging = _layout(tmp_path)
    calls: list[str] = []
    logs: list[str] = []

    def replay(directory: Path) -> int:
        calls.append("replay")
        return 0

    def verify(directory: Path) -> int:
        calls.append("verify")
        return 1

    rc = rs.record_fresh_session(
        target,
        staging,
        record=_writes("new.json"),
        replay=replay,
        verify=verify,
        log=logs.append,
    )
    assert rc == 1
    assert calls == ["replay", "verify"]  # one attempt, no retry
    assert _files(target) == ["old.json"]
    assert _files(staging) == ["new.json"]  # kept for diagnosis
    assert not (tmp_path / "recordings.old").exists()
    assert "verify" in logs[-1] and "unchanged" in logs[-1] and "diagnosis" in logs[-1]


def test_refuses_a_leftover_staging_dir(tmp_path):
    target, staging = _layout(tmp_path)
    staging.mkdir()
    calls: list[Path] = []
    rc = rs.record_fresh_session(
        target,
        staging,
        record=lambda d: calls.append(d) or 0,
        replay=_covering(),
        verify=lambda d: 0,
        log=str,
    )
    assert rc == 1 and calls == [] and _files(target) == ["old.json"]


def test_coverage_check_is_a_real_offline_replay(repo_root, tmp_path, monkeypatch):
    """The default replay step passes on the committed set and misses (exit 2) on an empty one."""
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("SRG_LLM_MODE", "live")  # --mode replay must win
    monkeypatch.delenv("CLIPROXY_API_KEY", raising=False)
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://127.0.0.1:9")
    assert rs._cli_replay(repo_root / "recordings") == 0
    assert rs._cli_verify(repo_root / "recordings") == 0  # verifies the staged replay output
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rs._cli_replay(empty) == 2


def _recipe(makefile: str, target: str) -> str:
    match = re.search(rf"^{target}:.*\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert match, target
    return match.group(1)


def test_makefile_pins_replay_and_records_fresh(repo_root):
    makefile = (repo_root / "Makefile").read_text()
    for target in ("run", "web-data"):
        assert "srg run --mode replay " in _recipe(makefile, target), target
    assert _recipe(makefile, "verify").count("srg ") == 2  # validate + verify of make run's output
    assert "python -m srg.record_session" in _recipe(makefile, "record")
    ignored = (repo_root / ".gitignore").read_text().splitlines()
    assert {"recordings.new/", "recordings.old/"} <= set(ignored)


# --- review round 3, finding 4: the canonical scenario set is required ----------------------


def test_scenarios_dir_without_s12_is_not_recorded(repo_root, tmp_path, monkeypatch):
    partial = tmp_path / "scenarios"
    partial.mkdir()
    for path in sorted((repo_root / "scenarios").glob("S*.json")):
        if not path.name.startswith("S12"):
            (partial / path.name).write_text(path.read_text())
    monkeypatch.setattr(rs, "SCENARIOS_DIR", partial)
    target, staging = _layout(tmp_path)
    logs: list[str] = []
    rc = rs.record_fresh_session(
        target,
        staging,
        record=lambda d: pytest.fail("must not record a non-canonical set"),
        replay=_covering(),
        verify=lambda d: 0,
        log=logs.append,
    )
    assert rc == 1 and not staging.exists() and _files(target) == ["old.json"]
    assert "missing S12" in logs[-1]


@pytest.mark.parametrize(
    ("ids", "why"),
    [
        (CANONICAL[:11], "missing S12"),  # the reviewer's S01-S11 staged replay
        ([*CANONICAL, "S01"], "repeated S01"),
        ([*CANONICAL[:11], "F01"], "missing S12; unexpected F01"),
    ],
)
def test_staged_artifact_without_the_canonical_set_is_not_promoted(tmp_path, ids, why):
    target, staging = _layout(tmp_path)
    logs: list[str] = []
    rc = rs.record_fresh_session(
        target,
        staging,
        record=_writes("new.json"),
        replay=_covering(ids=ids),
        verify=lambda d: 0,  # the reviewer's case: replay and verify both returned 0
        log=logs.append,
    )
    assert rc == 1
    assert _files(target) == ["old.json"]
    assert _files(staging) == ["new.json"]
    assert why in logs[-1] and "not promoted" in logs[-1]


def test_canonical_set_problem():
    assert rs.canonical_set_problem(CANONICAL) is None
    assert rs.canonical_set_problem(list(reversed(CANONICAL))) is None
    assert rs.canonical_set_problem([]) == f"missing {', '.join(CANONICAL)}"


# --- review round 3, finding 8: promotion never loses the active set or its backup -----------


def test_refuses_to_start_while_a_backup_exists(tmp_path):
    target, staging = _layout(tmp_path)
    backup = tmp_path / "recordings.old"
    (backup / "m").mkdir(parents=True)
    (backup / "m" / "keep.json").write_text("keep")
    logs: list[str] = []
    rc = rs.record_fresh_session(
        target,
        staging,
        record=lambda d: pytest.fail("must not record while a backup exists"),
        replay=_covering(),
        verify=lambda d: 0,
        log=logs.append,
    )
    assert rc == 1 and not staging.exists()
    assert _files(backup) == ["keep.json"]  # never deleted
    assert _files(target) == ["old.json"]
    assert "recordings.old already exists" in logs[-1]


def test_failed_promotion_restores_the_active_set(tmp_path, monkeypatch):
    target, staging = _layout(tmp_path)
    real_rename = Path.rename
    renames: list[tuple[str, str]] = []

    def rename(self: Path, dest):
        renames.append((Path(self).name, Path(dest).name))
        if Path(self) == staging:
            raise OSError("simulated rename failure")
        return real_rename(self, dest)

    monkeypatch.setattr(Path, "rename", rename)
    logs: list[str] = []
    rc = rs.record_fresh_session(
        target,
        staging,
        record=_writes("new.json"),
        replay=_covering(),
        verify=lambda d: 0,
        log=logs.append,
    )
    assert rc == 1
    assert renames == [
        ("recordings", "recordings.old"),
        ("recordings.new", "recordings"),
        ("recordings.old", "recordings"),
    ]
    assert _files(target) == ["old.json"]  # active set restored
    assert _files(staging) == ["new.json"]  # session kept
    assert not (tmp_path / "recordings.old").exists()
    assert "restored" in logs[-1]
