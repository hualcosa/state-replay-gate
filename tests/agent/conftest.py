"""Fixtures for agent/recorder tests; helpers live in agent_helpers (importlib mode)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from srg.contracts import Scenario  # noqa: E402
from srg.scenarios import load_scenario  # noqa: E402


@pytest.fixture
def f01(fixture_scenarios_dir: Path) -> Scenario:
    return load_scenario(fixture_scenarios_dir / "F01-hero-chargeback.json")


@pytest.fixture
def f03(fixture_scenarios_dir: Path) -> Scenario:
    return load_scenario(fixture_scenarios_dir / "F03-control-no-change.json")
