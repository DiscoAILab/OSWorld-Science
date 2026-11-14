"""`vm_glob` results (validation and collection) and the `artifact_glob` check that grades them."""
import json
import shutil
import subprocess
from pathlib import Path

from osworld_science.data.layout import DataLayout
from osworld_science.evaluators.registry import EvalContext, get
from osworld_science.evaluators.score import grade
from osworld_science.guest.client import Guest
from osworld_science.harness.collect import collect
from osworld_science.tasks.model import DEFAULT_MAX_FILES, Task
from osworld_science.tasks.validate import validate_task

PNG = b"\x89PNG\r\n\x1a\n"


def _task(tmp_path: Path, raw: dict) -> Task:
    d = tmp_path / "dom" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{raw['id']}.json"
    p.write_text(json.dumps(raw))
    return Task.load(p, domain="dom")


def _raw(guest_dir: str, max_files: int | None = 3) -> dict:
    glob_result = {"type": "vm_glob", "path": f"{guest_dir}/*.png", "dest": "figures"}
    if max_files is not None:
        glob_result["max_files"] = max_files
    return {
        "id": "t", "snapshot": "s", "instruction": "do", "budget": {"max_steps": 5}, "config": [],
        "deliverables": [f"{guest_dir}/answers.json", f"{guest_dir}/"],
        "evaluator": {
            "func": ["file_min_bytes", "artifact_glob"], "weights": [1.0, 0.0], "required": [1],
            "labels": ["value", "screenshots"],
            "params": [{"file": "answers.json", "min_bytes": 1},
                       {"dir": "figures", "pattern": "*.png", "min_count": 2, "kind": "png",
                        "min_bytes": 16, "distinct": True}],
            "result": [{"type": "vm_file", "path": f"{guest_dir}/answers.json", "dest": "answers.json"},
                       glob_result],
        },
    }


class LocalGuest(Guest):
    """A guest whose file system is this host: `execute` runs the command here, `pull` copies."""

    def __init__(self):
        super().__init__(1)
        self.commands: list = []

    def alive(self, require_display=True):
        return True

    def execute(self, command, timeout=300):
        self.commands.append(command)
        r = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return {"output": r.stdout, "error": r.stderr, "returncode": r.returncode}

    def pull(self, guest_path, dest):
        if not Path(guest_path).is_file():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(guest_path, dest)
        return True


def test_vm_glob_validates(tmp_path):
    names = {"file_min_bytes", "artifact_glob"}
    t = _task(tmp_path, _raw("/home/user/out"))
    spec = t.results[1]
    assert spec.type == "vm_glob" and spec.path == "/home/user/out/*.png" and spec.max_files == 3
    assert validate_task(t, evaluator_names=names, snapshot_names={"s"}) == []
    assert _task(tmp_path, _raw("/home/user/out", max_files=None)).results[1].max_files == DEFAULT_MAX_FILES

    def problems(edit) -> str:
        raw = _raw("/home/user/out")
        edit(raw["evaluator"])
        return " | ".join(validate_task(_task(tmp_path, raw), evaluator_names=names, snapshot_names={"s"}))

    assert "max_files must be positive" in problems(lambda ev: ev["result"][1].update(max_files=0))
    assert "must be integers" in problems(lambda ev: ev["result"][1].update(max_files="many"))
    assert "wildcards in the file name only" in problems(lambda ev: ev["result"][1].update(path="/home/*/out/*.png"))
    assert "needs a guest pattern" in problems(lambda ev: ev["result"][1].update(path="*.png"))
    assert "not the dest of a vm_glob" in problems(lambda ev: ev["params"][1].update(dir="elsewhere"))
    # without the glob the directory deliverable is not covered by any result
    assert "deliverable /home/user/out/ is neither pulled" in problems(lambda ev: ev["result"].pop())
    assert "entry 2 is not a check index" in problems(lambda ev: ev.update(required=[2]))
    assert "must be a list" in problems(lambda ev: ev.update(required=1))
    # Windows guest patterns (the Ansys Fluent tasks) are well-formed too
    win = {"type": "vm_glob", "path": "C:\\Users\\user\\work\\fluent-*.trn", "dest": "transcripts/work", "max_files": 20}
    assert problems(lambda ev: ev["result"].append(win)) == ""


def test_vm_glob_collects_sorted_capped_and_flat(tmp_path):
    guest_dir = tmp_path / "guest dir"          # a space in the path must survive
    guest_dir.mkdir()
    (guest_dir / "answers.json").write_text("{}")
    for name in ("d.png", "b.png", "a.png", "c.png"):
        (guest_dir / name).write_bytes(PNG + name.encode() * 8)
    (guest_dir / "notes.txt").write_text("not a match")
    (guest_dir / "sub.png").mkdir()             # a directory is not a file to pull
    t = _task(tmp_path, _raw(str(guest_dir)))
    guest, out, lines = LocalGuest(), tmp_path / "sub", []
    summary = collect(t, guest, out, log=lines.append)
    assert sorted(p.name for p in (out / "figures").iterdir()) == ["a.png", "b.png", "c.png"]   # first 3 of 4, sorted
    assert (out / "figures" / "a.png").read_bytes() == (guest_dir / "a.png").read_bytes()
    assert summary["collected"] == 4 and summary["missing"] == 0
    assert summary["globs"] == [{"dest": "figures", "pattern": f"{guest_dir}/*.png", "max_files": 3,
                                 "matched": 4, "collected": 3}]
    assert any("3 of 4 file(s)" in ln and "capped at max_files=3" in ln for ln in lines)
    assert json.loads((out / "collect.json").read_text())["globs"][0]["matched"] == 4
    # graded end to end: three distinct PNGs satisfy min_count 2
    ctx = EvalContext(domain="dom", layout=DataLayout(tmp_path))
    res = grade(t, {}, out, ctx)
    assert res["verdict"] == "PASS" and res["score"] == 1.0 and res["required_failed"] == []


def test_vm_glob_no_match_and_listing_failure(tmp_path):
    guest_dir = tmp_path / "g"
    guest_dir.mkdir()
    (guest_dir / "answers.json").write_text("{}")
    t = _task(tmp_path, _raw(str(guest_dir)))
    out = tmp_path / "sub"
    summary = collect(t, LocalGuest(), out, log=lambda *_: None)
    # nothing matched: not a miss of the harness, and the required screenshot check decides
    assert summary["collected"] == 1 and summary["missing"] == 0 and summary["globs"][0]["matched"] == 0
    assert not (out / "figures").exists()
    res = grade(t, {}, out, EvalContext(domain="dom", layout=DataLayout(tmp_path)))
    assert res["score"] == 1.0 and res["verdict"] == "FAIL" and res["required_failed"] == [1]
    assert "was not collected" in res["checks"][1]["detail"]["error"]

    class Broken(LocalGuest):
        def execute(self, command, timeout=300):
            return {"output": "", "error": "python3: command not found", "returncode": 127}

    lines: list[str] = []
    summary = collect(t, Broken(), tmp_path / "sub2", log=lines.append)
    assert summary["missing"] == 1 and "could not list" in summary["globs"][0]["error"]
    assert any(ln.lstrip().startswith("MISSING figures/") for ln in lines)

    class Hostile(LocalGuest):                  # a guest that names files outside the destination
        def glob(self, pattern, timeout=120):
            return ["/home/user/out/..", "/home/user/out/", str(guest_dir / "answers.json")]

    out3 = tmp_path / "sub3"
    collect(t, Hostile(), out3, log=lambda *_: None)
    assert sorted(p.name for p in (out3 / "figures").iterdir()) == ["answers.json"]


def test_artifact_glob(tmp_path):
    fn = get("artifact_glob")
    ctx = EvalContext(domain="dom", layout=DataLayout(tmp_path))
    figs = tmp_path / "figs"
    figs.mkdir()
    params = {"dir": "figs", "pattern": "*.png", "min_count": 4, "kind": "png", "min_bytes": 64, "distinct": True}
    for i in range(4):
        (figs / f"shot{i}.png").write_bytes(PNG + bytes([i]) * 100)
    (figs / "readme.txt").write_text("ignored: does not match the pattern")
    score, detail = fn(tmp_path, params, {}, ctx)
    assert score == 1.0 and detail["checked"] == 4 and detail["passed"] == 4 and detail["n_failures"] == 0
    (figs / "SHOT9.PNG").write_bytes(PNG + b"z" * 100)                  # the pattern ignores case
    assert fn(tmp_path, params, {}, ctx)[1]["passed"] == 5
    (figs / "SHOT9.PNG").unlink()
    # a duplicate counts once
    (figs / "shot3.png").write_bytes((figs / "shot0.png").read_bytes())
    score, detail = fn(tmp_path, params, {}, ctx)
    assert score == 0.75 and detail["failures"][0]["detail"] == "3 qualifying file(s), need 4"
    assert {"field": "shot3.png", "detail": "identical to shot0.png"} in detail["failures"]
    assert fn(tmp_path, {**params, "distinct": False}, {}, ctx)[0] == 1.0
    # a stub and a file of the wrong kind do not count
    (figs / "shot3.png").write_bytes(PNG + b"x")
    (figs / "shot2.png").write_bytes(b"GIF89a" + b"y" * 100)
    score, detail = fn(tmp_path, params, {}, ctx)
    assert score == 0.5 and [f["detail"] for f in detail["failures"][1:]] == ["not a PNG file", "only 9 bytes (need 64)"]
    # a missing directory scores zero with an error
    score, detail = fn(tmp_path, {**params, "dir": "nowhere"}, {}, ctx)
    assert score == 0.0 and "nowhere/ was not collected" in detail["error"]
