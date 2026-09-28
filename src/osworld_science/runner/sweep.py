"""models × tasks with one VM slot per worker."""
from __future__ import annotations

import datetime
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..data.layout import DataLayout
from ..llm.models import MissingCredential, ModelRegistry, ResolvedModel
from ..llm.preflight import preflight
from ..tasks.model import Task
from ..vm.docker import VM, owned_port_containers, port_is_free
from ..vm.locks import PortBusy, PortLock, port_lock_held
from ..vm.snapshots import SnapshotRegistry
from .pipeline import RunContext, run_cell
from .summary import Summary


@dataclass
class SweepOptions:
    run_name: str
    models: list[str]
    tasks: list[Task]
    workers: int = 1
    port_base: int | None = None
    force: bool = False
    no_reset: bool = False
    skip_preflight: bool = False
    agent: str | None = None
    agent_options: dict = field(default_factory=dict)
    track_windows: bool = True
    max_tokens: int | None = None
    max_actions_per_step: int | None = None


def plan_jobs(models: list[str], tasks: list[Task], summary: Summary, force: bool,
              log=print) -> list[tuple[str, Task]]:
    jobs = []
    for m in models:
        for t in tasks:
            prev = summary.entry(m, t.id)
            if prev and prev.get("finished") and not force:
                log(f"── {m} × {t.id}: already finished, skipping (--force to rerun)")
                continue
            jobs.append((m, t))
    return jobs


def run_sweep(settings: Settings, opts: SweepOptions, log=print) -> Summary:
    registry = ModelRegistry(settings.configs_dir / "models.yaml")
    snapshots = SnapshotRegistry(settings.configs_dir / "snapshots.yaml")
    layout = DataLayout(settings.data_dir)
    run_dir = settings.runs_dir / opts.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = Summary(run_dir / "summary.json")
    summary.set_meta(run_name=opts.run_name, started=datetime.datetime.now().isoformat(timespec="seconds"),
                     settings=settings.describe(), agent=opts.agent, agent_options=opts.agent_options)

    resolved: dict[str, ResolvedModel] = {}
    for m in opts.models:
        try:
            resolved[m] = registry.resolve(m, settings.env)
        except MissingCredential as e:
            raise SystemExit(str(e)) from None

    log("╔══ preflight (per model: one text and one image request)")
    ok_models = []
    for m, rm in resolved.items():
        if opts.skip_preflight:
            ok, why = True, "skipped"
        else:
            ok, why = preflight(rm, reasoning_effort=settings.reasoning_effort, log=log)
        log(f"║ {'✓' if ok else '✗'} {m:20s} {rm.backend.name:11s} {rm.model_id:28s} {why}")
        summary.set_preflight(m, ok, why, rm.describe())
        if ok:
            ok_models.append(m)
        else:
            log(f"!! {m} failed preflight, its whole column is skipped")
    if not ok_models:
        raise SystemExit("no model passed preflight")

    for t in opts.tasks:
        if t.snapshot not in snapshots:
            raise SystemExit(f"task {t.id} needs snapshot {t.snapshot!r}, not in configs/snapshots.yaml")

    jobs = plan_jobs(ok_models, opts.tasks, summary, opts.force, log)
    if not jobs:
        log("nothing to run")
        return summary

    workers = max(1, min(opts.workers, settings.max_workers, len(jobs)))
    port_base = opts.port_base or settings.port_base
    owned = owned_port_containers(settings, [snapshots[n].container_prefix for n in snapshots.names()])
    ports = allocate_ports(port_base, workers, settings.vm_dir / "locks", log, owned=owned)
    log(f"╔══ {len(jobs)} cell(s) on {workers} worker(s); control ports "
        f"{', '.join(str(p) for p in ports)}; run dir {run_dir}")

    stop_event = threading.Event()
    ctx = RunContext(settings=settings, layout=layout, snapshots=snapshots, run_dir=run_dir,
                     agent_override=opts.agent, agent_options=opts.agent_options,
                     no_reset=opts.no_reset, track_windows=opts.track_windows,
                     max_tokens=opts.max_tokens or settings.max_tokens,
                     max_actions_per_step=(settings.max_actions_per_step if opts.max_actions_per_step is None
                                           else opts.max_actions_per_step),
                     stop_event=stop_event)
    q: queue.Queue = queue.Queue()
    for j in jobs:
        q.put((j[0], j[1], 0))

    def run_one(port: int, m: str, task: Task, wlog) -> dict:
        vm = VM(snapshots[task.snapshot], snapshots.defaults, settings, port=port, log=wlog)
        return run_cell(resolved[m], task, ctx, vm, log=wlog)

    def worker(port: int) -> None:
        prefix = f"[:{port}]" if workers > 1 else ""
        try:
            with PortLock(settings.vm_dir / "locks", port):
                worker_loop(port, q, stop_event, run_one, summary, log, prefix=prefix)
        except PortBusy as e:
            log(f"!! worker on port {port} could not start: {e}")

    try:
        if workers == 1:
            worker(ports[0])
        else:
            threads = [threading.Thread(target=worker, args=(p,), name=f"osci-worker-{p}", daemon=True)
                       for p in ports]
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(timeout=0.5)
    except KeyboardInterrupt:
        stop_event.set()
        log("\ninterrupt: stopping — running cells end at their next step (up to ~30 s); "
            "press Ctrl-C again to exit immediately")
        try:
            deadline = time.time() + 30
            while time.time() < deadline and any(t.is_alive() for t in threading.enumerate()
                                                 if t.name.startswith("osci-worker-")):
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        summary.save()
        log(f"interrupted: finished cells are in {summary.path}; unfinished ones rerun next time")
        raise SystemExit(130) from None

    _print_totals(summary, ok_models, opts.tasks, log)
    return summary


def allocate_ports(base: int, n: int, lock_dir: Path, log=print, span: int = 200,
                   owned: dict[int, str] | None = None) -> list[int]:
    """The first `n` control ports from `base` that are neither bound on this host
    (another container, another program) nor locked by another sweep. A port
    bound only by one of our own containers (`<prefix>_<port>`, left over from
    an earlier run) is reused: reset recreates it anyway. Skipped ports are
    reported so a collision with a legacy container is visible."""
    owned = owned or {}
    ports: list[int] = []
    port = base
    while len(ports) < n and port < base + span:
        if port_lock_held(lock_dir, port):
            log(f"!! port {port} is locked by another osci run — skipping it")
        elif not port_is_free(port) and port not in owned:
            log(f"!! port {port} (or one of {port + 3006}/{port + 3080}/{port + 4202}) is already in use "
                f"on this host — skipping it")
        else:
            if port in owned and not port_is_free(port):
                log(f"   port {port}: reusing our container {owned[port]} from an earlier run")
            ports.append(port)
        port += 1
    if len(ports) < n:
        raise SystemExit(f"could not find {n} free control ports from {base} upwards")
    return ports


def worker_loop(port: int, q: queue.Queue, stop_event: threading.Event, run_one, summary: Summary,
                log=print, prefix: str = "", max_reset_failures: int = 2, max_requeues: int = 2) -> None:
    """Take (model, task, attempt) jobs until the queue is empty or the stop
    event is set. A cell that fails before staging (VM reset) is put back for
    another worker; after `max_reset_failures` such failures in a row this
    worker's VM slot is considered unusable and the worker retires."""

    def wlog(msg: str) -> None:
        if not prefix:
            log(msg)
        elif msg.startswith("\n"):          # keep the cell separator on its own line
            log("\n" + "\n".join(f"{prefix} {line}" for line in msg[1:].splitlines()))
        else:
            log("\n".join(f"{prefix} {line}" for line in msg.splitlines()) or prefix)

    reset_failures = 0
    while not stop_event.is_set():
        try:
            m, task, attempt = q.get_nowait()
        except queue.Empty:
            return
        wlog(f"\n════ {m} × {task.id}   ({datetime.datetime.now():%H:%M:%S})")
        entry = run_one(port, m, task, wlog)
        if entry.get("infra_stage") == "reset" and not entry.get("finished"):
            reset_failures += 1
            if attempt < max_requeues and not stop_event.is_set():
                wlog(f"   !! VM slot on port {port} failed before staging; handing the cell back "
                     f"to the queue (attempt {attempt + 1}/{max_requeues})")
                q.put((m, task, attempt + 1))
            else:
                summary.set_entry(m, task.id, entry)
            if reset_failures >= max_reset_failures:
                wlog(f"!! worker on port {port} retiring after {reset_failures} consecutive VM failures")
                return
            continue
        reset_failures = 0
        summary.set_entry(m, task.id, entry)


def _print_totals(summary: Summary, models: list[str], tasks: list[Task], log=print) -> None:
    log(f"\n══════════ totals (details in {summary.path})")
    log(f"  {'model':20s} {'done':>7s} {'PASS':>5s} {'in':>11s} {'out':>11s} {'total':>12s} {'USD':>9s}")
    tot = summary.data.get("totals", {})
    for m in models:
        rows = summary.data["results"].get(m, {})
        done = [r for r in rows.values() if r.get("finished")]
        passed = [r for r in done if r.get("verdict") == "PASS"]
        b = (tot.get("by_model") or {}).get(m) or {}
        log(f"  {m:20s} {len(done):3d}/{len(tasks):<3d} {len(passed):5d}"
            f" {b.get('input_tokens', 0):11d} {b.get('output_tokens', 0):11d}"
            f" {b.get('total_tokens', 0):12d} {b.get('cost_usd', 0.0):9.4f}"
            + ("  ← some cells lack a price; lower bound" if b.get("cost_usd_incomplete") else ""))
