"""Grade one collected submission against its task definition.

Scoring follows the task's own weights. A `stages` entry makes one check a
gate: if it fails, every check from `blocks_from` on scores zero, because a
number extracted from tampered inputs is not a partially correct number.
"""
from __future__ import annotations

from pathlib import Path

from ..tasks.model import Task
from .registry import EvalContext, get


def grade(task: Task, gt: dict, submission: Path, ctx: EvalContext) -> dict:
    ev = task.evaluator
    funcs = ev["func"]
    params = ev.get("params", [{}] * len(funcs))
    labels = ev.get("labels", [""] * len(funcs))
    weights = ev.get("weights") or [1 / len(funcs)] * len(funcs)

    checks = []
    for i, name in enumerate(funcs):
        fn = get(name)
        if fn is None:
            checks.append({"index": i, "func": name, "label": labels[i],
                           "weight": weights[i], "score": 0.0,
                           "detail": {"error": f"unknown evaluator {name!r}"}})
            continue
        try:
            score, detail = fn(submission, params[i], gt, ctx)
        except Exception as e:  # noqa: BLE001
            score, detail = 0.0, {"error": f"{type(e).__name__}: {e}"}
        checks.append({"index": i, "func": name, "label": labels[i],
                       "weight": weights[i], "score": round(float(score), 6),
                       "detail": detail})

    blocked: set[int] = set()
    for st in ev.get("stages", []):
        if checks[st["gate"]]["score"] < 1.0:
            blocked |= set(range(st["blocks_from"], len(checks)))
    for i in sorted(blocked):
        if checks[i]["score"] > 0:
            checks[i]["detail"] = {"blocked_by_gate": True,
                                   "original_score": checks[i]["score"],
                                   **checks[i]["detail"]}
        checks[i]["score"] = 0.0

    total = sum(c["weight"] * c["score"] for c in checks)
    threshold = ev.get("pass_score", 1.0)
    return {
        "task": task.id,
        "verdict": "PASS" if total >= threshold - 1e-9 else "FAIL",
        "score": round(total, 6),
        "pass_score": threshold,
        "gate_blocked": sorted(blocked),
        "checks": checks,
    }


def format_report(res: dict) -> str:
    lines = [f"{res['task']}: {res['verdict']}  score {res['score']:.4f} / {res['pass_score']:g}"]
    for c in res["checks"]:
        mark = "ok  " if c["score"] == 1.0 else ("FAIL" if c["score"] == 0 else "part")
        lines.append(f"  [{mark}] w={c['weight']:.2f} {c['score']:.3f}  {c['label']}")
        d = c["detail"]
        if "error" in d:
            lines.append(f"          error: {d['error']}")
        for f in d.get("failures", [])[:6]:
            lines.append(f"          - {f['field']}: {f['detail']}")
        if d.get("n_failures", 0) > 6:
            lines.append(f"          ... {d['n_failures'] - 6} more")
    return "\n".join(lines)
