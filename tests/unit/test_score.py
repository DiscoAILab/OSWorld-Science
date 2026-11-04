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


def _required_ev(artifact: float, main: float = 1.0) -> dict:
    # the shape the revised tasks use: [gate (w 0), main metric (w 1), required artifact (w 0)]
    return {"func": ["_always", "_always", "_always"], "weights": [0.0, 1.0, 0.0],
            "labels": ["gate", "metric", "artifact"],
            "params": [{"v": 1}, {"v": main}, {"v": artifact}],
            "stages": [{"gate": 0, "blocks_from": 1}], "required": [2], "pass_score": 1.0}


def test_required_check_must_pass_for_a_pass(tmp_path):
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    ok = grade(make_task(tmp_path, _required_ev(1.0)), {}, tmp_path, ctx)
    assert ok["verdict"] == "PASS" and ok["score"] == 1.0 and ok["required_failed"] == []
    # a zero-weight required check that fails used to cost nothing (PASS, 1.0):
    # now the verdict is FAIL and the continuous score is unchanged
    missing = grade(make_task(tmp_path, _required_ev(0.0)), {}, tmp_path, ctx)
    assert missing["verdict"] == "FAIL" and missing["score"] == 1.0 and missing["required_failed"] == [2]
    # partial credit on a required check is still a failure
    partial = grade(make_task(tmp_path, _required_ev(0.5)), {}, tmp_path, ctx)
    assert partial["verdict"] == "FAIL" and partial["score"] == 1.0 and partial["required_failed"] == [2]
    # a degraded submission scores the same with or without the required file
    degraded = grade(make_task(tmp_path, _required_ev(1.0, main=0.6)), {}, tmp_path, ctx)
    degraded_missing = grade(make_task(tmp_path, _required_ev(0.0, main=0.6)), {}, tmp_path, ctx)
    assert degraded["score"] == degraded_missing["score"] == 0.6 and degraded_missing["verdict"] == "FAIL"


def test_required_does_not_change_tasks_without_it(tmp_path):
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    ev = _required_ev(0.0, main=0.4)
    del ev["required"]
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 0.4 and res["verdict"] == "FAIL" and res["required_failed"] == []


def test_required_gate_failure_reported_once(tmp_path):
    # a required check that is also a gate: the gate zeroes the checks after it
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    ev = _required_ev(1.0)
    ev["required"] = [0, 2]
    ev["params"][0]["v"] = 0.0
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["verdict"] == "FAIL" and res["score"] == 0.0
    assert res["required_failed"] == [0, 2] and res["gate_blocked"] == [1, 2]


def test_validate_rejects_bad_required_indices(tmp_path):
    from osworld_science.tasks.validate import validate_task
    ev = _required_ev(1.0)
    ev["result"] = []
    assert validate_task(make_task(tmp_path, ev)) == []
    for bad in ([3], [-1], ["2"], 2):
        ev["required"] = bad
        errs = validate_task(make_task(tmp_path, ev))
        assert any("evaluator.required" in e for e in errs), (bad, errs)
