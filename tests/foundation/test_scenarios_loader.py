from __future__ import annotations

import json
import shutil

import pytest

from srg.scenarios import load_scenario, load_scenarios


def test_loads_fixtures_sorted(fixture_scenarios_dir):
    scenarios = load_scenarios(fixture_scenarios_dir)
    assert [s.id for s in scenarios] == ["F01", "F02", "F03", "F04", "F05"]
    assert [s.hero for s in scenarios] == [True, False, False, False, False]


def test_sorts_by_id_not_filename(tmp_path, fixture_scenarios_dir):
    shutil.copy(fixture_scenarios_dir / "F01-hero-chargeback.json", tmp_path / "zzz.json")
    shutil.copy(fixture_scenarios_dir / "F02-limit-lowered.json", tmp_path / "aaa.json")
    assert [s.id for s in load_scenarios(tmp_path)] == ["F01", "F02"]


def test_rejects_duplicate_ids(tmp_path, fixture_scenarios_dir):
    src = fixture_scenarios_dir / "F01-hero-chargeback.json"
    shutil.copy(src, tmp_path / "a.json")
    shutil.copy(src, tmp_path / "b.json")
    with pytest.raises(ValueError, match="duplicate scenario id F01"):
        load_scenarios(tmp_path)


def test_names_the_bad_file(tmp_path, fixture_scenarios_dir):
    shutil.copy(fixture_scenarios_dir / "F01-hero-chargeback.json", tmp_path / "good.json")
    data = json.loads((fixture_scenarios_dir / "F02-limit-lowered.json").read_text())
    data["expected"]["gate"]["verdict"] = "allow"
    (tmp_path / "broken.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="broken.json"):
        load_scenarios(tmp_path)


def test_invalid_json_names_file(tmp_path):
    (tmp_path / "garbage.json").write_text("{not json")
    with pytest.raises(ValueError, match="garbage.json"):
        load_scenario(tmp_path / "garbage.json")


def test_missing_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_scenarios(tmp_path / "absent")
