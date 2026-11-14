"""Structural validation of a task JSON: what the harness and grader need.

Domain-specific policy checks (answer leaks, undeclared numbers, course
identifiers) live with the task-authoring pipelines under scripts/task_build.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any

from .model import Task

REQUIRED_TOP = ("id", "snapshot", "instruction", "config", "evaluator")
CONFIG_TYPES = {"execute", "upload", "launch", "sleep"}
RESULT_TYPES = {"vm_file", "vm_command_line", "vm_glob"}


def _split_guest_path(path: str) -> tuple[str, str]:
    """(directory, file name) of a guest path, POSIX or Windows."""
    parts = re.split(r"[\\/]", path)
    return path[:len(path) - len(parts[-1])].rstrip("\\/"), parts[-1]


def validate_task(task: Task, evaluator_names: set[str] | None = None,
                  snapshot_names: set[str] | None = None) -> list[str]:
    errs: list[str] = []
    raw: dict[str, Any] = task.raw
    for k in REQUIRED_TOP:
        if k not in raw:
            errs.append(f"missing top-level key {k!r}")
    if errs:
        return errs
    if raw["id"] != task.path.stem:
        errs.append(f"id {raw['id']!r} != file name {task.path.stem!r}")
    if snapshot_names is not None and task.snapshot not in snapshot_names:
        errs.append(f"snapshot {task.snapshot!r} not in configs/snapshots.yaml")
    if task.max_steps <= 0:
        errs.append("budget.max_steps must be positive")

    for i, step in enumerate(task.config, 1):
        if step.type not in CONFIG_TYPES:
            errs.append(f"config[{i}]: unknown type {step.type!r}")
            continue
        p = step.parameters
        if step.type in ("execute", "launch") and not p.get("command"):
            errs.append(f"config[{i}]: {step.type} needs parameters.command")
        if step.type == "upload" and not (p.get("local") and p.get("guest")):
            errs.append(f"config[{i}]: upload needs parameters.local and parameters.guest")

    ev = task.evaluator
    funcs = ev.get("func") or []
    if not funcs:
        errs.append("evaluator.func is empty")
        return errs
    n = len(funcs)
    for key in ("labels", "params", "weights"):
        arr = ev.get(key)
        if arr is None:
            if key == "weights":
                continue
            errs.append(f"evaluator.{key} missing")
        elif len(arr) != n:
            errs.append(f"evaluator.{key} has {len(arr)} entries, func has {n}")
    weights = ev.get("weights") or [1 / n] * n
    if abs(sum(float(w) for w in weights) - 1.0) > 1e-6:
        errs.append(f"evaluator.weights sum to {sum(weights):.6f}, not 1")
    if evaluator_names is not None:
        for f in funcs:
            if f not in evaluator_names:
                errs.append(f"evaluator.func names unregistered evaluator {f!r}")
    for st in ev.get("stages") or []:
        g, b = st.get("gate"), st.get("blocks_from")
        if not (isinstance(g, int) and 0 <= g < n):
            errs.append(f"stage gate {g!r} is not a check index")
        if not (isinstance(b, int) and 0 <= b <= n):
            errs.append(f"stage blocks_from {b!r} out of range")
    req = ev.get("required")
    if req is not None:
        if not isinstance(req, list):
            errs.append("evaluator.required must be a list of check indices")
        else:
            for r in req:
                if not (isinstance(r, int) and not isinstance(r, bool) and 0 <= r < n):
                    errs.append(f"evaluator.required entry {r!r} is not a check index")
    try:
        results = task.results
    except (TypeError, ValueError) as e:
        errs.append(f"evaluator.result: timeout and max_files must be integers ({e})")
        return errs
    for r in results:
        if r.type not in RESULT_TYPES:
            errs.append(f"result type {r.type!r} unsupported")
        elif r.type == "vm_file" and not r.path:
            errs.append(f"result {r.dest!r}: vm_file needs a guest path")
        elif r.type == "vm_command_line" and not r.command:
            errs.append(f"result {r.dest!r}: vm_command_line needs a command")
        elif r.type == "vm_glob":
            folder, name = _split_guest_path(r.path)
            if not folder or not name:
                errs.append(f"result {r.dest!r}: vm_glob needs a guest pattern <directory>/<file pattern>")
            elif any(c in folder for c in "*?"):
                # matches land flat in `dest`, so they must come from one directory
                errs.append(f"result {r.dest!r}: vm_glob takes wildcards in the file name only")
            if r.max_files <= 0:
                errs.append(f"result {r.dest!r}: vm_glob max_files must be positive")
        if not r.dest:
            errs.append("every result needs a dest")
    dests = {r.dest for r in results}
    paths = {r.path for r in results if r.type == "vm_file"}
    commands = " ".join(" ".join(r.command) for r in results if r.type == "vm_command_line")
    globs = [r.path for r in results if r.type == "vm_glob"]
    glob_dests = {r.dest.rstrip("/") for r in results if r.type == "vm_glob"}

    def globbed(deliverable: str) -> bool:
        """A vm_glob covers a directory deliverable (its own directory) or a file it matches."""
        d = deliverable.rstrip("\\/")
        return any(d == _split_guest_path(g)[0] or fnmatch.fnmatchcase(deliverable, g) for g in globs)

    for d in task.deliverables:
        if d not in paths and d not in commands and not globbed(d):
            errs.append(f"deliverable {d} is neither pulled nor inspected by evaluator.result")
    # every graded file (params.file / files[]) must be collected
    for i, p in enumerate(ev.get("params") or []):
        if not isinstance(p, dict):
            continue
        if i < n and funcs[i] == "artifact_glob" and str(p.get("dir", "")).rstrip("/") not in glob_dests:
            errs.append(f"check {i} grades directory {p.get('dir')!r} which is not the dest of a vm_glob result")
        graded = [p["file"]] if "file" in p else []
        for x in p.get("files", []):
            graded.append(x.get("path") if isinstance(x, dict) else x)
        for f in graded:
            if f and f not in dests:
                errs.append(f"check {i} grades {f!r} which is not in evaluator.result")
    return errs
