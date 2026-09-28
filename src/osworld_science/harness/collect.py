"""Pull the task's result files out of the guest into a submission directory.
Missing deliverables are a solver outcome, not a harness error.

Two kinds of result: `vm_file` pulls a guest file; `vm_command_line` runs a
command in the guest and keeps its stdout+stderr as a text file (a sha256 of a
protected input that is too large to pull, the head of a project file, ...).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..guest.client import Guest
from ..tasks.model import Task


def collect(task: Task, guest: Guest, out: Path, run_id: str | None = None, log=print) -> dict:
    if not guest.alive():
        raise RuntimeError(f"no guest answering on :{guest.port}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    got = missing = 0
    for item in task.results:
        dest = out / item.dest
        if item.type == "vm_command_line":
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                r = guest.execute(list(item.command), item.timeout)
            except Exception as e:  # noqa: BLE001 — recorded as evidence, graded as a miss
                r = {"output": "", "error": f"{type(e).__name__}: {e}", "returncode": -1}
            text = ((r.get("output") or "") + (r.get("error") or "")).strip()
            dest.write_text(text + "\n", encoding="utf-8")
            got += 1
            log(f"  ran     {item.dest:<32s} rc={r.get('returncode')} {text[:70]!r}")
            continue
        if item.type != "vm_file":
            log(f"  ?? unsupported result type {item.type!r}")
            continue
        if guest.pull(item.path, dest):
            got += 1
            log(f"  ok      {item.dest:<32s} {dest.stat().st_size:>9,d} bytes")
        else:
            missing += 1
            log(f"  MISSING {item.dest:<32s} ({item.path})")
    summary = {"task": task.id, "run_id": run_id, "port": guest.port,
               "collected": got, "missing": missing}
    (out / "collect.json").write_text(json.dumps(summary, indent=2))
    log(f"\n{got} collected, {missing} missing -> {out}")
    return summary
