"""Build schemas/fixtures/run.fixture.json from the F01-F05 fixture scenarios.

Usage: uv run python scripts/make_fixture.py [--check]

Runs the real pipeline (srg.arms: gate, ledger, outcome, summary) with decisions from
ScriptedProvider.from_expected, so the fixture passes `srg verify`. It is labelled as fixture
data by its model id ("fixture-scripted") and its F-prefixed scenario ids.
--check exits 1 if the committed file differs from a fresh build.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from srg.arms import run_scenarios
from srg.canonical import pretty_json
from srg.contracts import RunArtifact
from srg.scenarios import load_scenarios
from srg.scripted import SCRIPTED_MODEL, ScriptedProvider

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "tests" / "fixtures" / "scenarios"
OUT = ROOT / "schemas" / "fixtures" / "run.fixture.json"


def build() -> RunArtifact:
    scenarios = load_scenarios(SCENARIOS_DIR)
    provider = ScriptedProvider.from_expected(scenarios)
    return run_scenarios(scenarios, provider, llm_mode="replay", model=SCRIPTED_MODEL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = parser.parse_args(argv)
    text = pretty_json(build())
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(
                f"stale fixture (run scripts/make_fixture.py): {OUT.relative_to(ROOT)}",
                file=sys.stderr,
            )
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
