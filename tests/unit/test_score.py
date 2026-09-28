import json
from pathlib import Path

from osworld_science.data.layout import DataLayout
from osworld_science.evaluators.registry import EvalContext, register
from osworld_science.evaluators.score import grade
from osworld_science.tasks.model import Task


@register("_always")
def _always(sub, params, gt, ctx):
    return float(params["v"]), {"note": "test"}


def make_task(tmp_path: Path, ev: dict) -> Task:
    raw = {"id": "t1", "snapshot": "s", "instruction": "do", "config": [], "evaluator": ev}
    p = tmp_path / "t1.json"
    p.write_text(json.dumps(raw))
    return Task.load(p, domain="d")


def test_weighted_total_and_gate(tmp_path):
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    ev = {"func": ["_always", "_always", "_always"], "weights": [0.2, 0.5, 0.3],
          "labels": ["gate", "b", "c"], "params": [{"v": 1}, {"v": 0.5}, {"v": 1}],
          "stages": [{"gate": 0, "blocks_from": 1}], "pass_score": 1.0}
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert abs(res["score"] - (0.2 + 0.25 + 0.3)) < 1e-9 and res["verdict"] == "FAIL"
    ev["params"][0]["v"] = 0.5
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 0.1 and res["gate_blocked"] == [1, 2]
    assert res["checks"][1]["detail"]["blocked_by_gate"] and res["checks"][1]["detail"]["original_score"] == 0.5


def test_unknown_evaluator_scores_zero(tmp_path):
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    ev = {"func": ["_nope"], "weights": [1.0], "labels": ["x"], "params": [{}]}
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 0.0 and "unknown evaluator" in res["checks"][0]["detail"]["error"]
