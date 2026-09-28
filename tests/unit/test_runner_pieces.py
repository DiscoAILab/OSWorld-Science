import json

from osworld_science.reporting.tables import n_delivered, verdict_of, write_tables
from osworld_science.runner.episode import tally_windows
from osworld_science.runner.summary import Summary, recompute_totals


def test_summary_totals_and_atomic_write(tmp_path):
    s = Summary(tmp_path / "summary.json")
    s.set_entry("m", "t1", {"finished": True, "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.1,
                            "n_llm_calls": 2, "verdict": "PASS"})
    s.set_entry("m", "t2", {"finished": False, "input_tokens": 3, "output_tokens": 1, "n_llm_calls": 1})
    d = json.loads((tmp_path / "summary.json").read_text())
    tot = d["totals"]["by_model"]["m"]
    assert tot["runs"] == 2 and tot["runs_finished"] == 1 and tot["total_tokens"] == 19
    assert tot["cost_usd_incomplete"] is True  # t2 has tokens but no price
    recompute_totals(d)
    assert d["totals"]["overall"]["n_llm_calls"] == 3


def test_verdict_rules():
    assert verdict_of({"finished": False}) == "VOID"
    assert verdict_of({"finished": True, "status": "done", "verdict": "FAIL"}) == "FAIL"
    assert verdict_of({"finished": True, "status": "predict_error:boom", "verdict": None}) == "VOID"
    assert verdict_of({"finished": True, "status": "done", "verdict": None}) == "VOID"


def test_max_steps_voids_only_the_null_run():
    """Out of steps, no DONE/FAIL from the agent, nothing of its own delivered."""
    null_run = {"finished": True, "status": "max_steps", "verdict": "FAIL",
                "score": 0.1, "delivered": 0}
    assert verdict_of(null_run) == "VOID"
    assert verdict_of({**null_run, "delivered": 1}) == "FAIL"     # delivered something
    assert verdict_of({**null_run, "status": "fail"}) == "FAIL"   # stopped on its own
    assert verdict_of({**null_run, "verdict": "PASS", "delivered": 2}) == "PASS"


def test_n_delivered_ignores_staged_inputs(tmp_path):
    """The `inputs/` copies come back whether or not the agent did anything."""
    sub = tmp_path / "submission"
    (sub / "inputs").mkdir(parents=True)
    (sub / "inputs" / "scan.dcm").write_text("x")
    (sub / "collect.json").write_text("{}")
    assert n_delivered(sub) == 0
    (sub / "findings.csv").write_text("a,b")
    assert n_delivered(sub) == 1
    assert n_delivered(tmp_path / "gone") == 0


def test_delivered_counted_off_disk(tmp_path):
    for task, files in (("t1", ["inputs/in.csv"]), ("t2", ["inputs/in.csv", "out.csv"])):
        sub = tmp_path / task / "m1" / "submission"
        for f in files:
            (sub / f).parent.mkdir(parents=True, exist_ok=True)
            (sub / f).write_text("x")
    cell = {"finished": True, "status": "max_steps", "verdict": "FAIL", "score": 0.1}
    (tmp_path / "summary.json").write_text(json.dumps(
        {"results": {"m1": {"t1": dict(cell), "t2": dict(cell)}}}))
    write_tables(tmp_path / "summary.json", tmp_path / "csv", task_order=["t1", "t2"])
    verdict = (tmp_path / "csv" / "verdict.csv").read_text().splitlines()
    assert verdict[1] == "m1,VOID,FAIL"  # t1 delivered only its inputs back


def test_write_tables(tmp_path):
    summary = {"results": {"m1": {"t1": {"finished": True, "status": "done", "verdict": "PASS", "score": 1.0,
                                          "steps_used": 5, "cost_usd": 0.2, "total_tokens": 100},
                                   "t2": {"finished": True, "status": "empty_response", "verdict": "FAIL",
                                          "score": 0.1, "steps_used": 3}}}}
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    per = write_tables(tmp_path / "summary.json", tmp_path / "csv", task_order=["t2", "t1"])
    assert per["m1"]["PASS"] == 1 and per["m1"]["VOID"] == 1 and per["m1"]["FAIL"] == 0
    verdict = (tmp_path / "csv" / "verdict.csv").read_text().splitlines()
    assert verdict[0] == "model,t2,t1" and verdict[1] == "m1,VOID,PASS"


def test_tally_windows():
    seen = {}
    wl = "0x1 0 gnome-terminal-server.Gnome-terminal  host user@host: ~\n0x2 0 praat.Praat host Praat Objects"
    tally_windows(wl, {"gnome-terminal": "terminal", "praat": "praat", "slicer": "slicer"}, seen)
    assert seen == {"terminal": 1, "praat": 1}
