"""One cell: reset → pre-task hooks → stage → episode → collect → score."""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..agents.base import AgentRunConfig
from ..agents.registry import get_agent_factory
from ..config import Settings
from ..data.layout import DataLayout
from ..evaluators.registry import EvalContext
from ..evaluators.score import grade
from ..guest.controller import DesktopController
from ..harness.collect import collect
from ..harness.stage import stage
from ..llm.models import ResolvedModel
from ..tasks.model import Task
from ..vm.docker import VM
from ..vm.snapshots import SnapshotRegistry
from .episode import run_episode
from .summary import ENTRY_KEYS


@dataclass
class RunContext:
    settings: Settings
    layout: DataLayout
    snapshots: SnapshotRegistry
    run_dir: Path                       # runs/<run_name>
    agent_override: str | None = None
    agent_options: dict = field(default_factory=dict)
    no_reset: bool = False
    track_windows: bool = True
    max_tokens: int = 16000
    max_actions_per_step: int = 10
    stop_event: threading.Event | None = None

    def cell_dir(self, task: Task, model_name: str) -> Path:
        return self.run_dir / task.id / model_name


def run_hooks(vm: VM, task: Task, ctx: RunContext, log=print) -> None:
    for hook in vm.snapshot.pre_task_hooks:
        if not hook.applies_to(task.related_apps):
            continue
        script = ctx.settings.repo_root / hook.script
        cmd = [str(script), "--port", str(vm.port)]
        if script.suffix == ".py":
            cmd = [sys.executable, *cmd]  # the venv interpreter, not whatever python3 is first on PATH
        log(f"   hook {script.name}")
        r = subprocess.run(cmd, cwd=ctx.settings.repo_root, capture_output=True, text=True)
        if r.returncode != 0:
            msg = f"pre-task hook {script.name} failed: {(r.stdout + r.stderr)[-300:]}"
            if hook.required:
                raise RuntimeError(msg)
            log(f"   !! {msg} — continuing (hook is optional)")


def score_cell(task: Task, submission: Path, ctx: RunContext, out: Path) -> dict | None:
    """Grade a collected submission; None when no ground truth is available."""
    if not ctx.layout.has_gt(task.domain, task.id):
        return None
    gt = ctx.layout.load_gt(task.domain, task.id)
    ectx = EvalContext(domain=task.domain, layout=ctx.layout, rscript=ctx.settings.rscript, task=task)
    res = grade(task, gt, submission, ectx)
    out.write_text(json.dumps(res, indent=2))
    return res


def score_fields(res: dict) -> dict:
    """What a graded cell contributes to summary.json.

    `score` is the weighted total, and a domain whose grader is pass/fail binarises it,
    so the per check breakdown goes in as well: a zero weight check that carries a
    grader's own continuous score is partial credit the total cannot show. Labels stay
    out — the task definition names the checks in this order.
    """
    return {"score": res.get("score"), "verdict": res.get("verdict"),
            "checks": [{"func": c.get("func"), "score": c.get("score"), "weight": c.get("weight")}
                       for c in (res.get("checks") or [])]}


def run_cell(model: ResolvedModel, task: Task, ctx: RunContext, vm: VM, log=print) -> dict:
    entry = {"finished": False, "error": None,
             "ts": datetime.datetime.now().isoformat(timespec="seconds"),
             "snapshot": task.snapshot, "port": vm.port}
    cell = ctx.cell_dir(task, model.name)
    agent_dir, submission = cell / "agent", cell / "submission"
    entry["run_dir"] = str(cell)
    stage_name = "reset"
    try:
        vm.stop_event = ctx.stop_event
        if not ctx.no_reset:
            vm.reset()
        stage_name = "stage"
        run_hooks(vm, task, ctx, log)
        stage(task, vm.guest, ctx.layout, log)

        stage_name = "episode"
        agent_dir.mkdir(parents=True, exist_ok=True)
        raw_log = agent_dir / "llm_raw.jsonl"
        raw_log.write_text("")
        agent_name = ctx.agent_override or model.spec.agent or "prompt"
        cfg = AgentRunConfig(max_steps=task.max_steps, max_tokens=ctx.max_tokens, run_dir=agent_dir,
                             image_detail=ctx.settings.image_detail,
                             reasoning_effort=ctx.settings.reasoning_effort,
                             options={**ctx.agent_options, "settings": ctx.settings})
        agent = get_agent_factory(agent_name)(model, task, cfg, raw_log, log)
        controller = DesktopController(vm.port)
        loop = run_episode(agent, task, controller, vm.guest, agent_dir, task.max_steps,
                           window_classes=vm.snapshot.window_classes,
                           track_windows=ctx.track_windows, stop_event=ctx.stop_event,
                           max_actions_per_step=ctx.max_actions_per_step, log=log)
        # agents report their own max_tokens in describe(): the Kimi "upstream" budget
        # keeps the factory cap (65536) regardless of the CLI value, and meta must say so
        meta = {"task": task.id, "domain": task.domain, "snapshot": task.snapshot,
                "requested_max_tokens": ctx.max_tokens,
                **model.describe(), **agent.describe(), **loop, **agent.token_stats()}
        (agent_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        entry.update({k: meta.get(k) for k in ENTRY_KEYS})
        if loop["status"] == "interrupted":
            entry["error"] = "interrupted"
            return entry

        collect(task, vm.guest, submission, run_id=model.name, log=log)
        res = score_cell(task, submission, ctx, cell / "score.json")
        if res is None:
            entry.update({"score": None, "verdict": None, "scored": False, "finished": True})
            log("   → collected; no ground truth on this host (score later with `osci score-run`)")
        else:
            entry.update({**score_fields(res), "scored": True, "finished": True})
            cost = entry.get("cost_usd")
            log(f"   → {res.get('verdict')}  score={res.get('score')}  steps={entry['steps_used']}"
                f"  tokens={entry['input_tokens']}in+{entry['output_tokens']}out"
                + (f"  ${cost:.4f}" if isinstance(cost, (int, float)) else ""))
    except KeyboardInterrupt:
        entry["error"] = "interrupted"
        raise
    except Exception as e:  # noqa: BLE001
        entry["error"] = f"{type(e).__name__}: {e}"
        entry["infra_stage"] = stage_name if stage_name in ("reset", "stage") else None
        log(f"   !! {entry['error']}")
    return entry
