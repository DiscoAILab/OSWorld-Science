"""Grade one collected submission against its task definition.

Scoring follows the task's own weights. A `stages` entry makes one check a
gate: if it fails, every check from `blocks_from` on scores zero, because a
number extracted from tampered inputs is not a partially correct number.

A weighted check may answer "not yet": it puts a `pending` reason in its
detail (a grader that needs an independent review of a free-text report, or
whose sandbox is not provisioned on this host). The submission then has no
score and no verdict, which the report tables count as VOID; what the other
checks add up to is kept as `provisional_score`. A zero is a statement about
the submission, and a pending check has not made one.

Checks listed in `evaluator.required` must all pass for the task to PASS: if
one scores below 1.0 the verdict is FAIL whatever the weighted total; the
continuous score itself is not changed. They usually carry weight 0 (a
deliverable is present, an input came back unmodified, an artifact check), so
a required check that answers "not yet" withholds the verdict like a weighted
one does.
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
    # `evaluator.required`: checks that must pass for the task to PASS. Without this step a
    # missing required deliverable cost nothing (weight 0) and the task could still PASS.
    required = [int(i) for i in ev.get("required") or []]
    pending = [{"index": c["index"], "reason": str(c["detail"]["pending"])} for c in checks
               if (c["weight"] > 0 or c["index"] in required) and c["index"] not in blocked
               and isinstance(c["detail"], dict) and c["detail"].get("pending")]
    waiting = {p["index"] for p in pending}
    # Any required check below 1.0 forces FAIL; the continuous score stays as the weights
    # give it (the eeg grader suite pins "same score, FAIL" for a missing required analysis.m).
    required_failed = [i for i in required if i not in waiting and checks[i]["score"] < 1.0]
    if pending:
        return {
            "task": task.id,
            "verdict": None,
            "score": None,
            "provisional_score": round(total, 6),
            "pending": pending,
            "pass_score": threshold,
            "gate_blocked": sorted(blocked),
            "required": required,
            "required_failed": required_failed,
            "checks": checks,
        }
    passed = not required_failed and total >= threshold - 1e-9
    return {
        "task": task.id,
        "verdict": "PASS" if passed else "FAIL",
        "score": round(total, 6),
        "pass_score": threshold,
        "gate_blocked": sorted(blocked),
        "required": required,
        "required_failed": required_failed,
        "checks": checks,
    }


def format_report(res: dict) -> str:
    if res.get("pending"):
        lines = [f"{res['task']}: PENDING  no score yet (counts as VOID); "
                 f"the other checks add up to {res['provisional_score']:.4f} / {res['pass_score']:g}"]
        lines += [f"  pending check {p['index']}: {p['reason']}" for p in res["pending"]]
    else:
        lines = [f"{res['task']}: {res['verdict']}  score {res['score']:.4f} / {res['pass_score']:g}"]
    if res.get("required_failed"):
        lines.append(f"  required check(s) failed: {res['required_failed']} -> FAIL"
                     + (" whatever the pending check says" if res.get("pending") else ""))
    waiting = {p["index"] for p in res.get("pending") or []}
    for c in res["checks"]:
        mark = "wait" if c["index"] in waiting else \
            "ok  " if c["score"] == 1.0 else ("FAIL" if c["score"] == 0 else "part")
        lines.append(f"  [{mark}] w={c['weight']:.2f} {c['score']:.3f}  {c['label']}")
        d = c["detail"]
        if "error" in d:
            lines.append(f"          error: {d['error']}")
        for f in d.get("failures", [])[:6]:
            lines.append(f"          - {f['field']}: {f['detail']}")
        if d.get("n_failures", 0) > 6:
            lines.append(f"          ... {d['n_failures'] - 6} more")
    return "\n".join(lines)
