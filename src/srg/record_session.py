"""Full fresh recording session.

Usage (``make record``): ``uv run python -m srg.record_session``

1. Record every scenario into a new, empty staging directory, so every sample is a fresh call
   from this session (record mode over the old set would reuse its hits).
2. Replay the whole run from the staging directory alone: complete coverage or nothing.
3. ``srg verify`` the staged replay artifact: integrated acceptance, not only coverage (review
   round 2, finding 5).
4. Only then replace ``recordings/`` with the staging directory.

The session requires the canonical scenario set, S01-S12 each exactly once, in ``scenarios/``
before recording and in the staged replay artifact before promotion (review round 3, finding
4): a set missing a scenario must not replace the recordings. Ordinary subset runs
(``srg run --scenario``) are unaffected.

Promotion never deletes a recovery copy it did not create: it refuses to start while
``recordings.old/`` exists, and if moving the staged set into place fails after the active set
was moved aside, it moves the active set back (review round 3, finding 8).

A failure at 1, 2 or 3 leaves ``recordings/`` untouched and keeps the staging directory for
inspection; it must be removed before the next attempt. There are no retries: a complete
session that fails verification is kept for diagnosis, never promoted or re-recorded until it
passes.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

RECORDINGS = Path("recordings")
STAGING = Path("recordings.new")
SCENARIOS_DIR = Path("scenarios")
CANONICAL_IDS = tuple(f"S{i:02d}" for i in range(1, 13))
RECORD_OUT = "runs/record.json"
COVERAGE_OUT = "runs/record-coverage.json"

Step = Callable[[Path], int]


def _cli_record(directory: Path) -> int:
    from srg.cli import main

    return main(
        ["run", "--mode", "record", "--recordings-dir", str(directory), "--out", RECORD_OUT]
    )


def _cli_replay(directory: Path) -> int:
    from srg.cli import main

    return main(
        ["run", "--mode", "replay", "--recordings-dir", str(directory), "--out", COVERAGE_OUT]
    )


def _cli_verify(directory: Path) -> int:
    from srg.cli import main

    return main(["verify", COVERAGE_OUT, "--recordings", str(directory)])


def canonical_set_problem(ids: Sequence[str]) -> str | None:
    """Why ``ids`` is not exactly the canonical S01-S12 set, each once; None if it is."""
    counts = Counter(ids)
    missing = [i for i in CANONICAL_IDS if i not in counts]
    extra = sorted(i for i in counts if i not in CANONICAL_IDS)
    dupes = sorted(i for i, n in counts.items() if n > 1)
    parts = []
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    if extra:
        parts.append(f"unexpected {', '.join(extra)}")
    if dupes:
        parts.append(f"repeated {', '.join(dupes)}")
    return "; ".join(parts) or None


def _scenario_dir_ids(directory: Path) -> list[str]:
    from srg.scenarios import load_scenarios

    return [s.id for s in load_scenarios(directory)]


def _artifact_ids(path: Path) -> list[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [str(run["scenario"]["id"]) for run in doc["scenarios"]]


def record_fresh_session(
    target: Path = RECORDINGS,
    staging: Path = STAGING,
    *,
    scenarios_dir: Path | None = None,
    coverage_out: Path | None = None,
    record: Step = _cli_record,
    replay: Step = _cli_replay,
    verify: Step = _cli_verify,
    log: Callable[[str], None] = print,
) -> int:
    """Return 0 after replacing ``target`` with a fully covered, verified fresh session."""
    coverage = Path(coverage_out if coverage_out is not None else COVERAGE_OUT)
    scenarios_dir = Path(scenarios_dir if scenarios_dir is not None else SCENARIOS_DIR)
    backup = target.with_name(f"{target.name}.old")
    if staging.exists():
        log(f"record: {staging} already exists (an earlier attempt?); inspect and remove it first")
        return 1
    if backup.exists():
        log(f"record: {backup} already exists (an earlier recovery copy?); inspect and remove it")
        return 1
    try:
        problem = canonical_set_problem(_scenario_dir_ids(scenarios_dir))
    except (OSError, ValueError) as exc:
        problem = f"cannot load scenarios: {exc}"
    if problem:
        log(
            f"record: {scenarios_dir} is not the canonical S01-S12 set ({problem}); "
            f"nothing recorded, {target} unchanged"
        )
        return 1
    staging.mkdir(parents=True)
    rc = record(staging)
    if rc != 0:
        log(f"record: recording failed (exit {rc}); {target} unchanged, partial set in {staging}")
        return rc
    rc = replay(staging)
    if rc != 0:
        log(
            f"record: replay over {staging} failed (exit {rc}): the new set does not cover the "
            f"run; {target} unchanged"
        )
        return rc
    rc = verify(staging)
    if rc != 0:
        log(
            f"record: srg verify of the replay over {staging} failed (exit {rc}): the session is "
            f"complete but does not pass acceptance; {target} unchanged, session kept in "
            f"{staging} for diagnosis"
        )
        return rc
    try:
        problem = canonical_set_problem(_artifact_ids(coverage))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        problem = f"cannot read {coverage}: {exc}"
    if problem:
        log(
            f"record: the staged replay {coverage} is not the canonical S01-S12 set ({problem}); "
            f"not promoted, {target} unchanged, session kept in {staging}"
        )
        return 1
    if backup.exists():  # appeared during the session: never delete it
        log(f"record: {backup} appeared during the session; not promoted, {target} unchanged")
        return 1
    moved_aside = False
    if target.exists():
        target.rename(backup)
        moved_aside = True
    try:
        staging.rename(target)
    except OSError as exc:
        if moved_aside:
            backup.rename(target)
        log(
            f"record: moving {staging} into place failed ({exc}); {target} restored, "
            f"session kept in {staging}"
        )
        return 1
    if moved_aside:
        shutil.rmtree(backup)
    log(f"record: {target} replaced by a complete, verified fresh session")
    return 0


if __name__ == "__main__":
    sys.exit(record_fresh_session())
