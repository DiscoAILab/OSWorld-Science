"""Pull the task's result files out of the guest into a submission directory.
Missing deliverables are a solver outcome, not a harness error.

Three kinds of result: `vm_file` pulls a guest file; `vm_command_line` runs a
command in the guest and keeps its stdout+stderr as a text file (a sha256 of a
protected input that is too large to pull, the head of a project file, ...);
`vm_glob` pulls every guest file matching a pattern into a directory of the
submission, under the files' own names and at most `max_files` of them (the
solver chose the names: screenshots, a program's session transcripts).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..guest.client import Guest
from ..tasks.model import Task


def _file_name(guest_path: str) -> str:
    """The last component of a guest path, or "" when it cannot name a file here. The list
    comes from the guest, so it is never trusted to stay inside the destination directory."""
    name = re.split(r"[\\/]", guest_path)[-1]
    return "" if name in ("", ".", "..") else name


def _collect_glob(item, guest: Guest, dest: Path, log) -> tuple[int, int, dict]:
    """Pull the files matching `item.path` into `dest/`. Returns (collected, missing, record).
    No match is not a miss: the grader decides what an empty directory means. Matches are taken
    in sorted order, so which ones survive `max_files` does not depend on the guest."""
    label = item.dest.rstrip("/") + "/"
    record = {"dest": item.dest, "pattern": item.path, "max_files": item.max_files}
    try:
        found = guest.glob(item.path)
    except Exception as e:  # noqa: BLE001 — recorded as evidence, graded as a miss
        log(f"  MISSING {label:<32s} ({item.path}): {type(e).__name__}: {e}")
        return 0, 1, {**record, "error": f"{type(e).__name__}: {e}"}
    got = missing = 0
    names: set[str] = set()
    for path in found[:max(0, item.max_files)]:
        name = _file_name(path)
        if not name or name in names:
            log(f"  skipped {label:<32s} {path!r} (no usable or a repeated file name)")
            continue
        names.add(name)
        if guest.pull(path, dest / name):
            got += 1
        else:
            missing += 1
            log(f"  MISSING {label + name:<32s} ({path})")
    capped = f", capped at max_files={item.max_files}" if len(found) > item.max_files else ""
    log(f"  glob    {label:<32s} {got} of {len(found)} file(s) matching {item.path}{capped}")
    return got, missing, {**record, "matched": len(found), "collected": got}


def collect(task: Task, guest: Guest, out: Path, run_id: str | None = None, log=print) -> dict:
    if not guest.alive(require_display=False):      # the files come back even when X is wedged
        raise RuntimeError(f"no guest answering on :{guest.port}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    got = missing = 0
    globs: list[dict] = []
    for item in task.results:
        dest = out / item.dest
        if item.type == "vm_glob":
            n_got, n_missing, record = _collect_glob(item, guest, dest, log)
            got += n_got
            missing += n_missing
            globs.append(record)
            continue
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
    if globs:
        summary["globs"] = globs
    (out / "collect.json").write_text(json.dumps(summary, indent=2))
    log(f"\n{got} collected, {missing} missing -> {out}")
    return summary
