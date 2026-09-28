"""Evaluators that delegate to code shipped with the data, and the
`vm_command_line` result getter they rely on."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from osworld_science.data.layout import DataLayout
from osworld_science.evaluators.registry import EvalContext, get, load_builtin
from osworld_science.harness.collect import collect
from osworld_science.tasks.model import Task
from osworld_science.tasks.validate import validate_task


def _ctx(tmp_path: Path, domain: str = "dom") -> tuple[EvalContext, Path]:
    load_builtin()
    data = tmp_path / "data"
    priv = data / domain / "private" / "reference_private" / "t1"
    priv.mkdir(parents=True)
    (data / domain / "public").mkdir()
    return EvalContext(domain=domain, layout=DataLayout(data)), priv


def test_module_metric_tuple_dict_and_missing_file(tmp_path):
    ctx, priv = _ctx(tmp_path)
    (priv / "evaluator.py").write_text(textwrap.dedent('''
        import json
        from pathlib import Path
        def metric(result, expected=None, **_):
            gt = json.loads((Path(expected["rules"]["oracle_dir"]) / "gt.json").read_text())
            return (1.0 if Path(result).read_text() == gt["want"] else 0.0), "compared"
        def judge(result, expected=None, assets=None):
            return {"score": 0.5, "metrics": [{"name": "m", "score": 0.5}]}
    '''))
    (priv / "private_gt").mkdir()
    (priv / "private_gt" / "gt.json").write_text('{"want": "ok"}')
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "out.txt").write_text("ok")
    p = {"module": "reference_private/t1/evaluator.py", "func": "metric", "file": "out.txt",
         "oracle_dir": "reference_private/t1/private_gt"}
    assert get("module_metric")(sub, p, {}, ctx) == (1.0, {"note": "compared", "func": "metric"})
    score, detail = get("module_metric")(sub, {**p, "func": "judge"}, {}, ctx)
    assert score == 0.5 and detail["metrics"][0]["name"] == "m"
    score, detail = get("module_metric")(sub, {**p, "file": "missing.txt"}, {}, ctx)
    assert score == 0.0 and "not collected" in detail["error"]


def test_sibling_imports_stay_per_task(tmp_path):
    """Two tasks ship different files of the same helper name; each evaluator
    must see its own copy."""
    ctx, priv = _ctx(tmp_path)
    other = priv.parent / "t2"
    other.mkdir()
    for d, x in ((priv, 1), (other, 2)):
        (d / "common.py").write_text(f"X = {x}\n")
        (d / "evaluator.py").write_text("import common\ndef m(result, expected=None, **_):\n    return float(common.X) / 2, 'x'\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "f").write_text("")
    s1, _ = get("module_metric")(sub, {"module": "reference_private/t1/evaluator.py", "func": "m", "file": "f"}, {}, ctx)
    s2, _ = get("module_metric")(sub, {"module": "reference_private/t2/evaluator.py", "func": "m", "file": "f"}, {}, ctx)
    assert (s1, s2) == (0.5, 1.0)


def test_package_metric_and_text_score(tmp_path):
    ctx, priv = _ctx(tmp_path)
    (priv / "verify_submission.py").write_text(textwrap.dedent('''
        import logging, os
        _FIXTURE_DIR = "unset"
        log = logging.getLogger("desktopenv.metric.test")
        def check(result_state, **options):
            paths = result_state if isinstance(result_state, list) else [result_state]
            log.info("criterion 1 k=%s", options.get("k"))
            ok = all(os.path.exists(p) for p in paths) and options.get("k") == 1 and _FIXTURE_DIR.endswith("t1")
            return 1.0 if ok else 0.0
        def score_submission_text(agent_text, reference_text):
            return {"score": 1.0 if agent_text.strip() == reference_text.strip() else 0.0, "reason": "cmp"}
    '''))
    (priv / "reference.txt").write_text("x\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    for f in ("a.pdb", "b.pdb"):
        (sub / f).write_text("ATOM")
    (sub / "answer.txt").write_text("x\n")
    gt = {"k": 1, "_source": {"ignored": True}}
    base = {"module": "reference_private/t1/verify_submission.py", "func": "check",
            "fixture_dir": "reference_private/t1"}
    score, detail = get("package_metric")(sub, {**base, "files": ["a.pdb", "b.pdb"]}, gt, ctx)
    assert score == 1.0 and detail["criteria"] == ["criterion 1 k=1"] and detail["missing"] == []
    score, _ = get("package_metric")(sub, {**base, "files": ["a.pdb"], "arg": "path"}, gt, ctx)
    assert score == 1.0
    score, detail = get("package_metric")(sub, {**base, "files": ["a.pdb", "nope.pdb"]}, gt, ctx)
    assert score == 0.0 and detail["missing"] == ["nope.pdb"]
    p = {"module": "reference_private/t1/verify_submission.py", "file": "answer.txt",
         "reference": "reference_private/t1/reference.txt"}
    assert get("package_text_score")(sub, p, {}, ctx) == (1.0, {"reason": "cmp"})
    score, detail = get("package_text_score")(sub, {**p, "file": "none.txt"}, {}, ctx)
    assert score == 0.0 and "not collected" in detail["error"]


def test_text_include_exclude_and_file_min_bytes(tmp_path):
    ctx, _ = _ctx(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "sha.txt").write_text("abc123\n")
    ok, d = get("text_include_exclude")(sub, {"file": "sha.txt", "include": ["abc123"]}, {}, ctx)
    assert ok == 1.0 and d["missing"] == []
    bad, d = get("text_include_exclude")(sub, {"file": "sha.txt", "include": ["zzz"], "exclude": ["abc"]}, {}, ctx)
    assert bad == 0.0 and d["missing"] == ["zzz"] and d["forbidden_present"] == ["abc"]
    assert get("text_include_exclude")(sub, {"file": "gone.txt", "include": ["a"]}, {}, ctx)[0] == 0.0
    assert get("file_min_bytes")(sub, {"file": "sha.txt", "min_bytes": 3}, {}, ctx) == (1.0, {"bytes": 7})
    assert get("file_min_bytes")(sub, {"file": "sha.txt", "min_bytes": 100}, {}, ctx)[0] == 0.0


def _task(tmp_path: Path, raw: dict) -> Task:
    d = tmp_path / "tasks" / "dom"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{raw['id']}.json"
    p.write_text(json.dumps(raw))
    return Task.load(p, domain="dom")


def test_vm_command_line_results_validate_and_collect(tmp_path):
    raw = {
        "id": "t", "snapshot": "s", "instruction": "do", "budget": {"max_steps": 5},
        "config": [],
        "deliverables": ["/home/user/work/project.qpproj", "/home/user/work/out.bin"],
        "evaluator": {
            "func": ["text_include_exclude", "file_min_bytes"], "weights": [0.0, 1.0],
            "labels": ["gate", "value"],
            "params": [{"file": "head.txt", "include": ["images"]}, {"file": "out.bin", "min_bytes": 1}],
            "result": [
                {"type": "vm_command_line", "command": ["bash", "-c", "head -c 100 /home/user/work/project.qpproj"],
                 "dest": "head.txt", "timeout": 30},
                {"type": "vm_file", "path": "/home/user/work/out.bin", "dest": "out.bin"},
            ],
        },
    }
    t = _task(tmp_path, raw)
    specs = t.results
    assert specs[0].type == "vm_command_line" and specs[0].command[0] == "bash" and specs[0].timeout == 30
    assert validate_task(t, evaluator_names={"text_include_exclude", "file_min_bytes"}, snapshot_names={"s"}) == []

    class FakeGuest:
        port = 1

        def alive(self):
            return True

        def execute(self, command, timeout):
            return {"output": "{\"images\": []}", "error": "", "returncode": 0}

        def pull(self, path, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            return True

    out = tmp_path / "sub"
    summary = collect(t, FakeGuest(), out, log=lambda *_: None)
    assert summary["collected"] == 2 and (out / "head.txt").read_text().strip() == '{"images": []}'
    assert (out / "out.bin").read_bytes() == b"x"

    bad = dict(raw, id="u")
    bad["evaluator"] = dict(raw["evaluator"], result=[{"type": "vm_screenshot", "dest": "x"}])
    errs = validate_task(_task(tmp_path, bad), evaluator_names=None, snapshot_names=None)
    assert any("unsupported" in e for e in errs) and any("neither pulled nor inspected" in e for e in errs)
