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


@register("_pending")
def _pending(sub, params, gt, ctx):
    return 0.0, {"pending": "review required"}


def test_pending_check_withholds_score_and_verdict(tmp_path):
    from osworld_science.evaluators.score import format_report
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    ev = {"func": ["_always", "_pending", "_always"], "weights": [0.0, 1.0, 0.0],
          "labels": ["gate", "reviewed", "record"], "params": [{"v": 1}, {}, {"v": 0.5}],
          "stages": [{"gate": 0, "blocks_from": 1}]}
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] is None and res["verdict"] is None and res["provisional_score"] == 0.0
    assert res["pending"] == [{"index": 1, "reason": "review required"}]
    assert "PENDING" in format_report(res) and "[wait]" in format_report(res)
    # a failed gate is a statement about the submission: the blocked check is no longer pending
    ev["params"][0]["v"] = 0
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 0.0 and res["verdict"] == "FAIL" and "pending" not in res
    # a zero-weight pending check never withholds the score
    ev = {"func": ["_always", "_pending"], "weights": [1.0, 0.0], "labels": ["a", "b"], "params": [{"v": 1}, {}]}
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 1.0 and res["verdict"] == "PASS"


def test_required_check_forces_fail_without_changing_the_score(tmp_path):
    from osworld_science.evaluators.score import format_report
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    # the weighted checks are perfect; the zero-weight required deliverable check is not
    ev = {"func": ["_always", "_always", "_always"], "weights": [0.4, 0.6, 0.0],
          "labels": ["a", "b", "deliverable"], "params": [{"v": 1}, {"v": 1}, {"v": 0.875}],
          "required": [2], "pass_score": 1.0}
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 1.0 and res["verdict"] == "FAIL"
    assert res["required"] == [2] and res["required_failed"] == [2]
    assert "required check(s) failed: [2] -> FAIL" in format_report(res)
    # the same submission with the required check satisfied passes, on the same score
    ev["params"][2]["v"] = 1
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 1.0 and res["verdict"] == "PASS" and res["required_failed"] == []
    assert "required" not in format_report(res)
    # a partial score stays what the weights give, with or without the required failure
    ev["params"] = [{"v": 1}, {"v": 0.5}, {"v": 0}]
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 0.7 and res["verdict"] == "FAIL" and res["required_failed"] == [2]
    # no `required` key: nothing is required, a zero-weight miss does not decide the verdict
    del ev["required"]
    ev["params"] = [{"v": 1}, {"v": 1}, {"v": 0}]
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["verdict"] == "PASS" and res["required"] == [] and res["required_failed"] == []


def test_required_and_gates_and_pending(tmp_path):
    from osworld_science.evaluators.score import format_report
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    # a required check behind a failed gate is blocked to zero, so it fails too
    ev = {"func": ["_always", "_always", "_always"], "weights": [0.0, 1.0, 0.0],
          "labels": ["gate", "value", "deliverable"], "params": [{"v": 0}, {"v": 1}, {"v": 1}],
          "stages": [{"gate": 0, "blocks_from": 1}], "required": [0, 2]}
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] == 0.0 and res["verdict"] == "FAIL" and res["required_failed"] == [0, 2]
    # a pending weighted check still withholds score and verdict; a required failure is reported with it
    ev = {"func": ["_always", "_pending", "_always"], "weights": [0.0, 1.0, 0.0],
          "labels": ["gate", "reviewed", "deliverable"], "params": [{"v": 1}, {}, {"v": 0}], "required": [2]}
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["score"] is None and res["verdict"] is None and res["provisional_score"] == 0.0
    assert res["pending"] == [{"index": 1, "reason": "review required"}] and res["required_failed"] == [2]
    assert "PENDING" in format_report(res) and "required check(s) failed: [2]" in format_report(res)
    # a required check that answers "not yet" has not failed: it withholds the verdict even at weight 0
    ev = {"func": ["_always", "_pending"], "weights": [1.0, 0.0], "labels": ["a", "b"],
          "params": [{"v": 1}, {}], "required": [1]}
    res = grade(make_task(tmp_path, ev), {}, tmp_path, ctx)
    assert res["verdict"] is None and res["pending"] == [{"index": 1, "reason": "review required"}]
    assert res["required_failed"] == [] and res["provisional_score"] == 1.0


def test_sas_or_r_program_accepts_ordinary_renderings(tmp_path):
    """The program check decides the verdict where a task lists it in `required`, so it
    may tell the language but not dock a style (the stat suite's must-pass variants)."""
    from osworld_science.evaluators.registry import get
    fn = get("sas_or_r_program")
    ctx = EvalContext(domain="d", layout=DataLayout(tmp_path))
    params = {"file": "program.txt", "min_bytes": 40, "must_match": [r"glm|proc\s+logistic", "caliper"]}

    def verdict(text: str):
        (tmp_path / "program.txt").write_text(text)
        score, detail = fn(tmp_path, params, {}, ctx)
        return bool(score), detail["language"]

    r_prog = 'd <- read.csv("ontario_a.csv")\nfit <- glm(trtm ~ stent, family = binomial(), data = d)\n# caliper matching\n'
    assert verdict(r_prog) == (True, "R")
    assert verdict(r_prog.replace("family = binomial()", 'family = "binomial"')) == (True, "R")
    assert verdict(r_prog.replace("binomial()", 'binomial(link = "logit")').replace("read.csv(", "readr::read_csv(")) == (True, "R")
    assert verdict(r_prog.replace("read.csv(", "data.table::fread(")) == (True, "R")
    sas = ('proc import datafile="ontario_a.csv" out=coh dbms=csv replace; run;\n'
           "proc logistic data=coh descending; model trtm = stent; run;\n/* caliper matching */\n")
    assert verdict(sas) == (True, "SAS")
    assert verdict(sas.replace("proc import", "data coh; infile").replace("out=coh dbms=csv replace", "dsd")) == (True, "SAS")
    # Python is still refused, with or without the R-looking calls, and so is a program that fits nothing
    py = ("import pandas as pd\nimport statsmodels.api as sm\nd = pd.read_csv('ontario_a.csv')\n"
          "fit = sm.GLM(y, X, family=sm.families.Binomial()).fit()  # glm( caliper\n")
    assert verdict(py) == (False, "unknown")
    assert verdict("# logistic caliper match strat smd\n" * 20) == (False, "unknown")
    assert verdict(r_prog.replace("caliper", "radius"))[0] is False        # a must_match pattern is missing


# The four tests below came with the public repository's `evaluator.required` change and are kept
# as written; the two `required` tests above cover the same rule together with pending checks.
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
