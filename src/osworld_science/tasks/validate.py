"""Structural validation of a task JSON: what the harness and grader need.

Domain-specific policy checks (answer leaks, undeclared numbers, course
identifiers) live with the task-authoring pipelines under scripts/task_build.
"""
from __future__ import annotations

from typing import Any

from .model import Task

REQUIRED_TOP = ("id", "snapshot", "instruction", "config", "evaluator")
CONFIG_TYPES = {"execute", "upload", "launch", "sleep"}
RESULT_TYPES = {"vm_file", "vm_command_line"}


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
    results = task.results
    for r in results:
        if r.type not in RESULT_TYPES:
            errs.append(f"result type {r.type!r} unsupported")
        elif r.type == "vm_file" and not r.path:
            errs.append(f"result {r.dest!r}: vm_file needs a guest path")
        elif r.type == "vm_command_line" and not r.command:
            errs.append(f"result {r.dest!r}: vm_command_line needs a command")
        if not r.dest:
            errs.append("every result needs a dest")
    dests = {r.dest for r in results}
    paths = {r.path for r in results if r.type == "vm_file"}
    commands = " ".join(" ".join(r.command) for r in results if r.type == "vm_command_line")
    for d in task.deliverables:
        if d not in paths and d not in commands:
            errs.append(f"deliverable {d} is neither pulled nor inspected by evaluator.result")
    # every graded file (params.file / files[]) must be collected
    for i, p in enumerate(ev.get("params") or []):
        if not isinstance(p, dict):
            continue
        graded = [p["file"]] if "file" in p else []
        for x in p.get("files", []):
            graded.append(x.get("path") if isinstance(x, dict) else x)
        for f in graded:
            if f and f not in dests:
                errs.append(f"check {i} grades {f!r} which is not in evaluator.result")
    return errs
