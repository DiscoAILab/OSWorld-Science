"""Run a task's judge module over a cell and record what it decided.

A judge module is a Python file shipped with the task's private data; the
task JSON names it:

    "judge": {"module": "reference_private/<task>/osci_judge.py", "func": "review"}

and it exposes

    review(cell: Path, client: JudgeClient, ctx: JudgeContext) -> dict

which reads the cell (submission, trajectory, whatever the grader left), asks
the judge model through `client`, writes the review file the grader expects,
and returns at least `{"valid": bool, "reason": str}`. Everything the judge
produced lands under `<cell>/judge/` (transcript, raw request log, verdict),
next to but never inside the submission.
"""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..data.layout import DataLayout
from ..evaluators.external import load_module
from ..llm.models import MissingCredential, ModelRegistry, ResolvedModel
from ..llm.preflight import preflight
from ..tasks.model import Task
from .client import JudgeClient, JudgeError


def select_judge(registry: ModelRegistry, env: dict, tasks, judge_name: str | None = None,
                 no_judge: bool = False) -> tuple[ResolvedModel | None, str]:
    """Which judge a sweep uses: none when disabled or when no selected task declares one;
    otherwise the named judge or `judges.default`. Resolution failures raise JudgeError, because
    a run whose tasks need a judge should not start without one unless the user said --no-judge."""
    needing = [t.id for t in tasks if t.judge]
    if no_judge:
        return None, "disabled (--no-judge); pending cells stay pending"
    if not needing and judge_name is None:
        return None, "no selected task declares a judge"
    try:
        rm = registry.resolve_judge(judge_name, env)
    except (KeyError, MissingCredential) as e:
        raise JudgeError(f"{e} (tasks needing a judge: {', '.join(needing) or 'none'}; "
                         f"pass --no-judge to run without automatic review)") from None
    what = f"for {', '.join(needing)}" if needing else "(no selected task declares a judge; kept for --judge)"
    return rm, f"{rm.name} {what}"


def judge_preflight(judge: ResolvedModel, reasoning_effort: str = "", log=print) -> tuple[bool, str]:
    """One text request, plus one image request when the judge is declared `vision: true`."""
    ok, why = preflight(judge, reasoning_effort=reasoning_effort, log=log)
    if ok or judge.spec.extra.get("vision"):
        return ok, why
    if why.startswith("text ok"):
        return True, "text ok (no vision declared, image request not required)"
    return ok, why


@dataclass
class JudgeContext:
    task: Task
    layout: DataLayout
    solver: str | None          # the solver's model name (cell directory name) when known
    solver_model_id: str | None
    log: Any = print

    def resolve(self, rel: str) -> Path:
        return self.layout.resolve(self.task.domain, rel)


def solver_identity(cell: Path, solver: str | None) -> tuple[str | None, str | None]:
    """(model name, upstream model id) of the run that produced the cell, from its meta."""
    name, model_id = solver or cell.name, None
    try:
        meta = json.loads((cell / "agent" / "meta.json").read_text(encoding="utf-8"))
        name = meta.get("model") or name
        model_id = meta.get("model_id")
    except (OSError, ValueError):
        pass
    return name, model_id


def judge_cell(task: Task, cell: Path, judge: ResolvedModel, layout: DataLayout,
               solver: str | None = None, log=print) -> dict:
    """Review one cell with `judge`. Returns the verdict dict that also lands in <cell>/judge/judge.json."""
    cell = Path(cell)
    spec = task.judge
    out_dir = cell / "judge"
    out_dir.mkdir(parents=True, exist_ok=True)
    name, model_id = solver_identity(cell, solver)
    verdict: dict = {"task": task.id, "cell": str(cell), "solver": name, "solver_model_id": model_id,
                     "judge": judge.name, "judge_model_id": judge.model_id,
                     "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    if not spec:
        verdict.update(valid=False, reason="the task declares no judge module")
    elif _same_model(model_id, judge.model_id):
        # a model does not review its own run; the task graders require an independent reviewer
        verdict.update(valid=False, reason=f"judge {judge.model_id} is the model that produced this run; "
                                            f"pick another judge (--judge NAME)")
    else:
        log(f"   judge {judge.name} ({judge.model_id}) reviewing {cell.name} × {task.id}")
        client = JudgeClient(judge, out_dir, log=log)
        ctx = JudgeContext(task=task, layout=layout, solver=name, solver_model_id=model_id, log=log)
        try:
            mod = load_module(layout.resolve(task.domain, spec["module"]))
            fn = getattr(mod, spec.get("func", "review"))
            result = fn(cell, client, ctx)
            if not isinstance(result, dict) or "valid" not in result:
                raise JudgeError("judge module returned no {'valid': ...} dict")
            verdict.update(result)
        except JudgeError as e:
            verdict.update(valid=False, reason=str(e))
        except Exception as e:  # noqa: BLE001 — the verdict file must say what broke
            verdict.update(valid=False, reason=f"{type(e).__name__}: {e}")
        verdict["usage"] = client.stats()
        log(f"   judge: {'review written' if verdict.get('valid') else 'no valid review'}"
            f" ({verdict.get('reason', '')[:140]}) cost=${(client.stats().get('cost_usd') or 0):.4f}")
    (out_dir / "judge.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verdict


def _same_model(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().split("/")[-1])  # noqa: E731
    return norm(a) == norm(b)


def pending_cells(run_dir: Path, tasks, summary_results: dict, force: bool = False):
    """(model, task, cell) for every cell whose score is pending (or every scored cell with force)."""
    for model, rows in summary_results.items():
        for tid in rows:
            if tid not in tasks:
                continue
            task = tasks.get(tid)
            cell = run_dir / tid / model
            score = cell / "score.json"
            if not task.judge or not score.is_file():
                continue
            try:
                res = json.loads(score.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if res.get("pending") or force:
                yield model, task, cell
