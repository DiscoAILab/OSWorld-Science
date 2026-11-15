"""Use synthetic chemistry; real benchmark answers stay in the private data tree."""
import json

import pytest

from osworld_science.evaluators.xrd import formula_key, phase_set


def test_profex_snapshot(settings):
    from osworld_science.vm.snapshots import SnapshotRegistry
    snapshot = SnapshotRegistry(settings.configs_dir / "snapshots.yaml")["ubuntu_profex"]
    assert snapshot.domain == "materials"
    assert snapshot.default_port == 5180
    assert snapshot.provision == "scripts/vm_prep/profex/provision_guest.sh"
    assert snapshot.window_classes["profex"] == "profex"
    assert "5.7.2" in snapshot.description


@pytest.mark.parametrize("a,b", [
    ("Fe₂O₃", "Fe2O3"), ("O3Fe2", "Fe2O3"), ("Fe4 O6", "Fe2O3"),
    ("Ca(OH)2", "CaO2H2"), ("Al2(SO4)3", "Al2S3O12"),
    ("Ca (O H)2", "CaO2H2"), ("K4(Fe(CN)6)", "K4FeC6N6"),
])
def test_equivalent_formula(a, b):
    assert formula_key(a) == formula_key(b)


def test_element_case_matters():
    assert formula_key("Co") != formula_key("CO")


@pytest.mark.parametrize("bad", ["", "???", "Fe0O3", "Fe2O3; import os", 3, None,
                                 "Ca()2", "Ca(OH", "CaOH)", "Ca(OH)0", "CuSO4·xH2O"])
def test_invalid_formula(bad):
    with pytest.raises(ValueError):
        formula_key(bad)


@pytest.mark.parametrize("phases,expected", [
    (["SiO2", "Fe2O3"], 1.0), (["Fe₂O₃", "O2Si"], 1.0), (["Fe2O3"], 0.0),
    (["Fe2O3", "SiO2", "CaO"], 0.0), (["CaO"], 0.0),
    (["Fe2O3", "SiO2", "CuSO4·xH2O"], 0.0),
    (["Fe2O3", "SiO2", None], 0.0),
    (["Fe2O3", "Fe4O6"], 0.0), ([], 0.0), ([None], 0.0),
])
def test_phase_metric(tmp_path, phases, expected):
    (tmp_path / "phases.json").write_text(json.dumps({"phases": phases}))
    score, _ = phase_set(tmp_path, {"file": "phases.json"}, {"phases": ["Fe2O3", "SiO2"]}, None)
    assert score == pytest.approx(expected)


@pytest.mark.parametrize("text", ['{"phases":[],"phases":["Fe2O3"]}', 'not json', '[]'])
def test_bad_json(tmp_path, text):
    (tmp_path / "phases.json").write_text(text)
    score, detail = phase_set(tmp_path, {"file": "phases.json"}, {"phases": ["Fe2O3"]}, None)
    assert score == 0 and "error" in detail


def test_symlink_rejected(tmp_path):
    (tmp_path / "outside.json").write_text('{"phases":["Fe2O3"]}')
    (tmp_path / "phases.json").symlink_to(tmp_path / "outside.json")
    assert phase_set(tmp_path, {"file": "phases.json"}, {"phases": ["Fe2O3"]}, None)[0] == 0
