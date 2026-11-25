"""Shared path fixtures only (owned by task 2.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixture_scenarios_dir() -> Path:
    """F01-F05 fixture scenarios."""
    return REPO_ROOT / "tests" / "fixtures" / "scenarios"


@pytest.fixture(scope="session")
def scenarios_dir() -> Path:
    """Canonical S01-S12 scenarios (may not exist before task 2a lands)."""
    return REPO_ROOT / "scenarios"


@pytest.fixture(scope="session")
def schemas_dir() -> Path:
    return REPO_ROOT / "schemas"


@pytest.fixture(scope="session")
def run_fixture_path() -> Path:
    return REPO_ROOT / "schemas" / "fixtures" / "run.fixture.json"
