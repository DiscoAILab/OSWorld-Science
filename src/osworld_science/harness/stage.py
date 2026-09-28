"""Stage a task into a running guest: run its `config` block."""
from __future__ import annotations

import time

from ..data.layout import DataLayout
from ..guest.client import Guest, sha256_file
from ..tasks.model import Task
from ..vm.bake import TASK_MARKER


class StagingError(RuntimeError):
    pass


def stage(task: Task, guest: Guest, layout: DataLayout, log=print) -> None:
    if not guest.alive():
        raise StagingError(f"no guest answering on :{guest.port}")
    for i, step in enumerate(task.config, 1):
        kind, par = step.type, step.parameters
        if kind == "sleep":
            time.sleep(float(par.get("seconds", 1)))
            log(f"  [{i}] sleep {par.get('seconds', 1)}s")
        elif kind == "execute":
            r = guest.execute(par["command"], par.get("timeout", 300))
            out = ((r.get("output") or "") + (r.get("error") or "")).strip()
            log(f"  [{i}] execute: rc={r.get('returncode')} {out[:200]}")
            if r.get("returncode") not in (0, None):
                raise StagingError(f"setup step {i} failed; refusing to continue "
                                   f"— a half-staged task grades as a failed solver")
        elif kind == "launch":
            r = guest.launch(par["command"])
            log(f"  [{i}] launch: {par['command'][0]} (detached) {(r.get('output') or '').strip()[:60]}")
        elif kind == "upload":
            src = layout.resolve(task.domain, par["local"])
            if not src.is_file():
                raise StagingError(f"[{i}] asset missing: {src}\n"
                                   f"(download the data: scripts/data_prep/hf_download.py)")
            if par.get("sha256"):
                got = sha256_file(src)
                if got != par["sha256"]:
                    raise StagingError(f"[{i}] asset hash mismatch for {src.name}\n"
                                       f"      want {par['sha256']}\n      got  {got}")
            guest.upload(src, par["guest"])
            log(f"  [{i}] upload {src.name} -> {par['guest']} ({src.stat().st_size:,} bytes)")
        else:
            raise StagingError(f"[{i}] unknown config type {kind!r}")
    guest.execute(["bash", "-c", f"touch {TASK_MARKER}"], 30)
    log(f"\nstaged {task.id}; budget {task.max_steps} steps")
