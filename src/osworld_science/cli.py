"""`osci` — the OSWorld-Science command line.

    osci tasks list [--domain d]            osci tasks validate [ids…]
    osci vm start|reset|stop|status|check --snapshot s [--port p]
    osci vm bake --snapshot s [--port p] --out FILE [--force]
    osci vm-cache list|clean [--all]
    osci stage TASK [--port p]               osci collect TASK [--port p] --out DIR
    osci score TASK --submission DIR [--out FILE] [--json]
    osci score-run --run NAME [--force]
    osci doctor [--workers N]
    osci preflight --models a,b
    osci run --models a,b [--tasks t1,t2|DOMAIN|all] [--workers N] [--port-base p] …
    osci report --run NAME [--exclude m] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings


def _settings(a) -> Settings:
    return Settings.load(repo_root=Path(a.root) if getattr(a, "root", None) else None)


def _tasks(s: Settings):
    from .tasks.registry import TaskSet
    return TaskSet(s.data_dir)


def _snapshots(s: Settings):
    from .vm.snapshots import SnapshotRegistry
    return SnapshotRegistry(s.configs_dir / "snapshots.yaml")


def _vm(s: Settings, snapshot: str, port: int | None, image: str | None = None):
    from .vm.docker import VM
    reg = _snapshots(s)
    return VM(reg[snapshot], reg.defaults, s, port=port, image=Path(image) if image else None)


# ── tasks ──────────────────────────────────────────────────────────────────
def cmd_tasks_list(a) -> int:
    s = _settings(a)
    ts = _tasks(s)
    for d in ts.domains():
        if a.domain and d != a.domain:
            continue
        print(f"{d}:")
        for tid in ts.ids(d):
            t = ts.get(tid)
            print(f"  {tid:44s} {t.snapshot:20s} {t.max_steps:4d} steps  {', '.join(t.related_apps)}")
    return 0


def cmd_tasks_validate(a) -> int:
    from .evaluators.registry import names
    from .tasks.validate import validate_task
    s = _settings(a)
    ts, snaps = _tasks(s), _snapshots(s)
    ids = a.ids or ts.ids()
    bad = 0
    for tid in ids:
        errs = validate_task(ts.get(tid), evaluator_names=names(), snapshot_names=set(snaps.names()))
        print(f"{'ok  ' if not errs else 'FAIL'} {tid}")
        for e in errs:
            print(f"      - {e}")
        bad += bool(errs)
    print(f"{len(ids) - bad}/{len(ids)} task definitions valid")
    return 1 if bad else 0


# ── vm ─────────────────────────────────────────────────────────────────────
def cmd_vm(a) -> int:
    s = _settings(a)
    vm = _vm(s, a.snapshot, a.port, getattr(a, "image", None))
    if a.action == "start":
        vm.start(rebuild=a.rebuild)
    elif a.action == "reset":
        vm.reset()
    elif a.action == "stop":
        vm.stop()
        print(f"stopped {vm.name}")
    elif a.action == "remove":
        vm.remove()
        print(f"removed {vm.name}")
    elif a.action == "status":
        print(json.dumps(vm.status(), indent=2))
    elif a.action == "check":
        st = vm.status()
        print(f"guest on :{vm.port}: {'alive' if st['alive'] else 'NOT ANSWERING'}")
        if not st["alive"]:
            return 1
        print(vm.check())
    elif a.action == "bake":
        from .vm.bake import bake
        out = Path(a.out) if a.out else vm.default_bake_target
        bake(vm, out, force=a.force)
    return 0


def cmd_vm_cache(a) -> int:
    """Local image copies under OSCI_VM_FAST_DIR: list them, or remove the unused ones."""
    from .vm.docker import cache_entries, clean_cache
    s = _settings(a)
    if a.action == "clean":
        removed = clean_cache(s, all_copies=a.all)
        print(f"{len(removed)} file(s) removed from {s.vm_fast_dir}")
        return 0
    entries = cache_entries(s)
    print(f"{s.vm_fast_dir}  (OSCI_VM_COPY_IMAGES={s.vm_copy_images})")
    if not entries:
        print("  (no local copies)")
    for e in entries:
        print(f"  {e['bytes'] / 1e9:7.1f} GB  {Path(e['path']).name:40s} "
              f"{'mounted by ' + ', '.join(e['used_by']) if e['used_by'] else 'not in use'}")
    return 0


# ── harness ────────────────────────────────────────────────────────────────
def cmd_stage(a) -> int:
    from .data.layout import DataLayout
    from .guest.client import Guest
    from .harness.stage import stage
    s = _settings(a)
    t = _tasks(s).get(a.task)
    port = a.port or _snapshots(s)[t.snapshot].default_port
    stage(t, Guest(port), DataLayout(s.data_dir))
    return 0


def cmd_collect(a) -> int:
    from .guest.client import Guest
    from .harness.collect import collect
    s = _settings(a)
    t = _tasks(s).get(a.task)
    port = a.port or _snapshots(s)[t.snapshot].default_port
    out = Path(a.out) if a.out else s.runs_dir / "manual" / t.id / (a.run_id or "submission")
    collect(t, Guest(port), out, run_id=a.run_id)
    print(f"grade with:\n  osci score {t.id} --submission {out}")
    return 0


def cmd_score(a) -> int:
    from .data.layout import DataLayout
    from .evaluators.registry import EvalContext
    from .evaluators.score import format_report, grade
    s = _settings(a)
    t = _tasks(s).get(a.task)
    layout = DataLayout(s.data_dir)
    gt = layout.load_gt(t.domain, t.id)
    sub = Path(a.submission)
    if not sub.is_dir():
        sys.exit(f"submission directory not found: {sub}")
    res = grade(t, gt, sub, EvalContext(domain=t.domain, layout=layout, rscript=s.rscript, task=t))
    out = Path(a.out) if a.out else sub.parent / "score.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2) if a.json else format_report(res) + f"\n  -> {out}")
    return 0 if res["verdict"] == "PASS" else 1


def cmd_score_run(a) -> int:
    """(Re)score every collected cell of a run — for hosts that ran agents
    without the private ground truth present, or after an evaluator fix."""
    from .data.layout import DataLayout
    from .runner.pipeline import RunContext, score_cell, score_fields
    from .runner.summary import Summary
    s = _settings(a)
    ts = _tasks(s)
    run_dir = s.runs_dir / a.run
    summary = Summary(run_dir / "summary.json")
    ctx = RunContext(settings=s, layout=DataLayout(s.data_dir), snapshots=_snapshots(s), run_dir=run_dir)
    n = 0
    for model, rows in list(summary.data["results"].items()):
        for tid, entry in rows.items():
            if tid not in ts:
                continue
            cell = run_dir / tid / model
            if not (cell / "submission").is_dir():
                continue
            if (cell / "score.json").exists() and entry.get("scored") and not a.force:
                continue
            res = score_cell(ts.get(tid), cell / "submission", ctx, cell / "score.json")
            if res is None:
                print(f"{model} × {tid}: no ground truth")
                continue
            entry.update({**score_fields(res), "scored": True})
            summary.set_entry(model, tid, entry)
            print(f"{model} × {tid}: {res['verdict']} {res['score']:.4f}")
            n += 1
    print(f"{n} cell(s) scored")
    return 0


def cmd_doctor(a) -> int:
    from .doctor import main_doctor
    return main_doctor(_settings(a), workers=a.workers)


# ── models ─────────────────────────────────────────────────────────────────
def cmd_preflight(a) -> int:
    from .llm.models import ModelRegistry
    from .llm.preflight import preflight
    s = _settings(a)
    reg = ModelRegistry(s.configs_dir / "models.yaml")
    names = [m.strip() for m in a.models.split(",") if m.strip()] if a.models else reg.names()
    bad = 0
    for m in names:
        try:
            rm = reg.resolve(m, s.env)
        except Exception as e:  # noqa: BLE001
            print(f"✗ {m:20s} {e}")
            bad += 1
            continue
        ok, why = preflight(rm, reasoning_effort=s.reasoning_effort)
        print(f"{'✓' if ok else '✗'} {m:20s} {rm.backend.name:11s} {rm.model_id:28s} {why}")
        bad += not ok
    return 1 if bad else 0


def cmd_models(a) -> int:
    from .llm.models import ModelRegistry
    s = _settings(a)
    reg = ModelRegistry(s.configs_dir / "models.yaml")
    for m in reg.models.values():
        print(f"{m.name:24s} {m.backend.name:11s} {m.model_id:30s} agent={m.agent or 'prompt'}")
    return 0


# ── run ────────────────────────────────────────────────────────────────────
def cmd_run(a) -> int:
    from .runner.sweep import SweepOptions, run_sweep
    from .tasks.registry import select_tasks
    s = _settings(a)
    ts = _tasks(s)
    tasks = select_tasks(ts, a.tasks, domain=a.domain)
    if not tasks:
        sys.exit("no tasks selected")
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    opts = SweepOptions(
        run_name=a.run_name, models=models, tasks=tasks, workers=a.workers,
        port_base=a.port_base, force=a.force, no_reset=a.no_reset,
        skip_preflight=a.skip_preflight, agent=a.agent,
        agent_options={"kimi_mode": a.kimi_mode, "kimi_budget": a.kimi_budget,
                       "history_window": a.history_window},
        track_windows=not a.no_window_snapshot, max_tokens=a.max_tokens,
        max_actions_per_step=a.max_actions_per_step)
    run_sweep(s, opts)
    return 0


def cmd_report(a) -> int:
    from .reporting.tables import write_tables
    s = _settings(a)
    ts = _tasks(s)
    run_dir = s.runs_dir / a.run
    out = Path(a.out) if a.out else run_dir / "csv"
    per_model = write_tables(run_dir / "summary.json", out, exclude=a.exclude,
                             task_order=ts.ids())
    for m, row in per_model.items():
        print(f"{m:24s} PASS {row['PASS']:3d}  FAIL {row['FAIL']:3d}  VOID {row['VOID']:3d}  "
              f"mean(valid) {row['mean_score_valid'] if row['mean_score_valid'] is not None else float('nan'):.4f}")
    print(f"-> {out}")
    return 0


# ── parser ─────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="osci", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="repository root (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tasks", help="list / validate task definitions (data/<domain>/tasks/*.json)")
    ss = p.add_subparsers(dest="action", required=True)
    q = ss.add_parser("list")
    q.add_argument("--domain")
    q.set_defaults(fn=cmd_tasks_list)
    q = ss.add_parser("validate")
    q.add_argument("ids", nargs="*")
    q.set_defaults(fn=cmd_tasks_validate)

    p = sub.add_parser("vm-cache", help="local image copies: list, or remove the ones no container uses")
    p.add_argument("action", choices=["list", "clean"])
    p.add_argument("--all", action="store_true", help="clean: also copies still mounted (stop those containers first)")
    p.set_defaults(fn=cmd_vm_cache)

    p = sub.add_parser("vm", help="guest lifecycle")
    p.add_argument("action", choices=["start", "reset", "stop", "remove", "status", "check", "bake"])
    p.add_argument("--snapshot", required=True)
    p.add_argument("--port", type=int)
    p.add_argument("--image", help="qcow2 to boot instead of the snapshot's image (e.g. the base)")
    p.add_argument("--rebuild", action="store_true", help="start: destroy and recreate")
    p.add_argument("--out", help="bake: output qcow2 (default data/<domain>/vm/<snapshot image>)")
    p.add_argument("--force", action="store_true", help="bake: even if a task ran on this guest")
    p.set_defaults(fn=cmd_vm)

    p = sub.add_parser("stage", help="run a task's config block against a guest")
    p.add_argument("task")
    p.add_argument("--port", type=int)
    p.set_defaults(fn=cmd_stage)

    p = sub.add_parser("collect", help="pull a task's result files out of a guest")
    p.add_argument("task")
    p.add_argument("--port", type=int)
    p.add_argument("--out")
    p.add_argument("--run-id")
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("score", help="grade a collected submission (offline)")
    p.add_argument("task")
    p.add_argument("--submission", required=True)
    p.add_argument("--out")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("score-run", help="(re)score every collected cell of a run")
    p.add_argument("--run", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_score_run)

    p = sub.add_parser("doctor", help="check this host against the VM requirements")
    p.add_argument("--workers", type=int, default=1, help="how many concurrent guests you intend to run")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("preflight", help="probe model endpoints (text + image)")
    p.add_argument("--models", help="comma-separated; default: every model in configs/models.yaml")
    p.set_defaults(fn=cmd_preflight)

    p = sub.add_parser("models", help="list configured models")
    p.set_defaults(fn=cmd_models)

    p = sub.add_parser("run", help="run models × tasks")
    p.add_argument("--models", required=True, help="comma-separated names from configs/models.yaml")
    p.add_argument("--tasks", default="all", help="'all', a domain name, or comma-separated ids")
    p.add_argument("--domain", help="restrict --tasks all to one domain")
    p.add_argument("--run-name", default="default")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--port-base", type=int)
    p.add_argument("--force", action="store_true", help="rerun finished cells")
    p.add_argument("--no-reset", action="store_true", help="debug: stage onto the current guest state")
    p.add_argument("--skip-preflight", action="store_true")
    p.add_argument("--agent", help="override the agent (default: prompt, or the model's `agent`)")
    p.add_argument("--max-tokens", type=int)
    p.add_argument("--history-window", type=int, default=5, help="PromptAgent screenshot window")
    p.add_argument("--kimi-mode", choices=["gui", "hybrid"], default="gui")
    p.add_argument("--kimi-budget", choices=["matched", "upstream"], default="upstream",
                   help="upstream (default) = Moonshot factory budget, as in the recorded stat sweep; "
                        "matched = aligned with the PromptAgent arm (6 screenshots, history 5, CLI max-tokens)")
    p.add_argument("--no-window-snapshot", action="store_true")
    p.add_argument("--max-actions-per-step", type=int,
                   help="execute at most this many parsed actions per reply (0 = unlimited; "
                        "default OSCI_MAX_ACTIONS_PER_STEP or 10)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("report", help="CSV tables from a run's summary.json")
    p.add_argument("--run", required=True)
    p.add_argument("--exclude", action="append", default=[])
    p.add_argument("--out")
    p.set_defaults(fn=cmd_report)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
