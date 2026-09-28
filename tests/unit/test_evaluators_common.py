import hashlib
import json
from pathlib import Path

from osworld_science.data.layout import DataLayout
from osworld_science.evaluators.common import (
    artifact,
    csv_table,
    json_fields,
    protected_inputs_hash,
)
from osworld_science.evaluators.registry import EvalContext


def ctx(tmp_path: Path) -> EvalContext:
    return EvalContext(domain="t", layout=DataLayout(tmp_path / "data"))


def test_json_fields(tmp_path):
    (tmp_path / "out.json").write_text(json.dumps({"a": {"x": 1.0, "y": 2.0}}))
    gt = {"datasets": {"a": {"x": {"expect": 1.0, "tol_abs": 0}, "y": {"expect": 2.5, "tol_abs": 0.1}}}}
    score, d = json_fields(tmp_path, {"file": "out.json", "doc_path": ["a"], "gt_path": ["datasets", "a"]},
                           gt, ctx(tmp_path))
    assert score == 0.5 and d["n_failures"] == 1
    assert json_fields(tmp_path, {"file": "missing.json"}, gt, ctx(tmp_path))[0] == 0.0


def test_csv_table(tmp_path):
    (tmp_path / "t.csv").write_text("id,Score A\n1,10.00\n2,20.01\n")
    gt = {"rows": [{"id": 1, "score_a": 10}, {"id": 2, "score_a": 20}],
          "spec": {"key": "id", "columns": ["id", "score_a"], "tolerance": {"score_a": {"tol_abs": 0.011}}}}
    score, d = csv_table(tmp_path, {"file": "t.csv", "gt_path": ["rows"], "spec_path": ["spec"]}, gt, ctx(tmp_path))
    assert score == 1.0, d
    (tmp_path / "t.csv").write_text("id,Score A\n1,10.00\n")
    score, d = csv_table(tmp_path, {"file": "t.csv", "gt_path": ["rows"], "spec_path": ["spec"]}, gt, ctx(tmp_path))
    assert score < 1.0 and any(f["field"] == "<row count>" for f in d["failures"])


def test_protected_inputs_hash_with_flat_fallback(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "a.csv").write_bytes(b"1,2\n")
    (inputs / "4003253_1054713880_1.dcm").write_bytes(b"DICM")
    gt = {"protected_inputs": {"fam/a.csv": hashlib.sha256(b"1,2\n").hexdigest(),
                               "mri_spine/4003253/1054713880/1.dcm": hashlib.sha256(b"DICM").hexdigest(),
                               "fam/b.csv": "0" * 64}}
    score, d = protected_inputs_hash(tmp_path, {"dir": "inputs"}, gt, ctx(tmp_path))
    assert abs(score - 2 / 3) < 1e-9
    assert d["failures"][0]["field"] == "fam/b.csv"
    (inputs / "a.csv").write_bytes(b"1,3\n")
    score, d = protected_inputs_hash(tmp_path, {"dir": "inputs"}, gt, ctx(tmp_path))
    assert "MODIFIED" in d["failures"][0]["detail"]


def test_artifact(tmp_path):
    (tmp_path / "r.pdf").write_bytes(b"%PDF-1.4 ...")
    (tmp_path / "n.txt").write_text("Findings: consolidation in patient 15361.")
    (tmp_path / "stub.png").write_bytes(b"not a png")
    params = {"files": [{"path": "r.pdf", "kind": "pdf"},
                        {"path": "n.txt", "kind": "text", "min_bytes": 10, "must_match": ["15361"]},
                        {"path": "stub.png", "kind": "png"},
                        {"path": "gone.txt"}]}
    score, d = artifact(tmp_path, params, {}, ctx(tmp_path))
    assert score == 0.5
    assert {f["field"] for f in d["failures"]} == {"stub.png", "gone.txt"}
