#!/usr/bin/env python3
"""Provision the ubuntu_stm guest through the control plane (the host side of provision_guest.sh).

    STM_TASKS_JSON=<private tasks.json> scripts/vm_prep/stm/stm_setup.py --port 5190 [--smoke] [--no-scrub]

Uploads the pins and the private task list to /tmp/stm-prov, runs bootstrap_guest.sh detached (common/guest_run.py:
the control plane's /execute times out after ~120 s), which fetches the pinned STM-Bench commit and runs its
deploy/osworld/guest/install.sh. Then:

  * checks what the agent's account sees (it must not read the private directory, must read the export, the
    launcher's FIFO must exist, the service must be active);
  * with --smoke, starts the console as the agent's account would (stm-start with the first task id), takes a
    screenshot, prints the console window's WM_CLASS and the export's files, and resets the guest to its
    pristine state (stm-bench-reset), because an image must be baked from a guest that has not run a task;
  * overwrites the free space with zeros (unless --no-scrub), so that the deleted private copy of the task list
    leaves no readable blocks in the baked image;

Seeds are never printed: the task list is read for its shape only.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
PIN_NAMES = ("STM_COMMIT", "STM_REPO_URL", "PY_RELEASE", "PY_VERSION", "PY_SHA256", "PY_URL", "PYSIDE_PIN",
             "AGENT_USER", "STM_USER")
TASK_ID = re.compile(r"[a-z][a-z0-9_]{0,47}")
PROV = "/tmp/stm-prov"


def load_pins() -> dict[str, str]:
    """The pins of pins.sh, with the caller's environment taking precedence (as the shell file itself does)."""
    script = 'set -a; . "$1"; set +a; for n in "${@:2}"; do printf "%s=%s\\0" "$n" "${!n}"; done'
    out = subprocess.run(["bash", "-c", script, "_", str(HERE / "pins.sh"), *PIN_NAMES], capture_output=True,
                         check=True).stdout
    pins = dict(item.split("=", 1) for item in out.decode().split("\0") if item)
    if not re.fullmatch(r"[0-9a-f]{12,40}", pins["STM_COMMIT"]) or set(pins["STM_COMMIT"]) == {"0"}:
        raise SystemExit("STM_COMMIT is the placeholder or not hex (pins.sh)")
    return pins


def check_tasks(path: pathlib.Path) -> list[str]:
    """The task ids of the private list; its shape is checked, its seeds are never printed."""
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, dict) or not tasks:
        raise SystemExit("the task list must be a non-empty {task_id: {scenario, seed}} object")
    for tid, e in tasks.items():
        ok = (TASK_ID.fullmatch(tid) and isinstance(e, dict) and set(e) == {"scenario", "seed"}
              and isinstance(e["scenario"], str) and isinstance(e["seed"], int)
              and not isinstance(e["seed"], bool) and e["seed"] >= 0)
        if not ok:
            raise SystemExit(f"task {tid!r} is malformed (need exactly scenario and a non-negative integer seed)")
    return sorted(tasks)


def install_env(pins: dict[str, str]) -> str:
    """The pins as a sourceable file for install.sh (values are single-quoted)."""
    return "".join(f"export {k}={json.dumps(v)}\n".replace("$", "\\$") for k, v in pins.items())


def run(guest, command: str, timeout: int = 120) -> tuple[int, str]:
    r = guest.execute(["bash", "-lc", command], timeout)
    return int(r.get("returncode", -1)), ((r.get("output") or "") + (r.get("error") or "")).strip()


def verify(guest) -> bool:
    """What the agent's account can and cannot do, checked as that account."""
    checks = [
        ("stm-start is installed", "test -x /usr/local/bin/stm-start"),
        ("the launcher service is active", "systemctl is-active --quiet stm-bench"),
        ("the start FIFO exists and is writable by this account", "test -p /run/stm-bench/start && test -w /run/stm-bench/start"),
        ("the private directory cannot be read", "! ls /var/lib/stm-bench/private >/dev/null 2>&1"),
        ("the task list cannot be read", "! cat /var/lib/stm-bench/private/tasks.json >/dev/null 2>&1"),
        ("the export directory can be read", "ls /var/lib/stm-bench/export >/dev/null"),
        ("the export directory cannot be written", "! touch /var/lib/stm-bench/export/.probe 2>/dev/null"),
        ("the venv imports the console and Qt", "/opt/stm-bench/venv/bin/python -c 'import stmbench.gui.app, PySide6'"),
        ("stm-lockdown is installed", "test -f /usr/local/sbin/stm-lockdown && test \"$(stat -c %a /usr/local/sbin/stm-lockdown)\" = 750"),
        ("no episode has started", "test -z \"$(ls -A /var/lib/stm-bench/export)\" && ! test -e /run/stm-bench/task"),
    ]
    good = True
    for name, cmd in checks:
        rc, out = run(guest, cmd, 90)
        print(f"  [{'ok' if rc == 0 else 'FAIL'}] {name}" + ("" if rc == 0 else f"  ({out[-200:]})"))
        good &= rc == 0
    return good


def smoke(guest, task_id: str, shot: pathlib.Path, pw: str) -> bool:
    print(f"smoke test: stm-start {task_id} as the agent's account")
    rc, out = run(guest, f"DISPLAY=:0 /usr/local/bin/stm-start {task_id}", 120)
    print(f"  stm-start exit {rc}: {out[-300:]}")
    png = guest.screenshot()
    if png:
        shot.write_bytes(png)
        print(f"  screenshot: {shot} ({len(png)} bytes)")
    print("  windows (wmctrl -lx):", guest.windows().strip().replace("\n", " | ")[:600])
    rc2, files = run(guest, "ls -la /var/lib/stm-bench/export | tail -n +2")
    print("  export:\n" + "\n".join("    " + ln for ln in files.splitlines()))
    rc3, out3 = run(guest, f"echo {pw} | sudo -S /usr/local/sbin/stm-bench-reset", 120)
    print(f"  reset: exit {rc3} {out3[-80:]}")
    rc4, _ = run(guest, "test -z \"$(ls -A /var/lib/stm-bench/export)\" && ! test -e /run/stm-bench/task", 60)
    print(f"  pristine again: {'yes' if rc4 == 0 else 'NO'}")
    return rc == 0 and rc3 == 0 and rc4 == 0 and bool(png)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--tasks-json", default=os.environ.get("STM_TASKS_JSON"))
    ap.add_argument("--no-tasks", action="store_true",
                    help="bake no task list into the image (a seed in an image file is readable by whoever holds it)")
    ap.add_argument("--smoke", action="store_true", help="start the console once through stm-start, then reset")
    ap.add_argument("--no-scrub", action="store_true", help="do not overwrite the free space with zeros")
    ap.add_argument("--minutes", type=float, default=40)
    ap.add_argument("--screenshot", default="stm_smoke.png")
    a = ap.parse_args()
    if a.no_tasks and a.smoke:
        raise SystemExit("--smoke needs a task list")
    if not a.tasks_json and not a.no_tasks:
        raise SystemExit("give the private task list: STM_TASKS_JSON=<file> or --tasks-json (or --no-tasks)")
    tasks_path = pathlib.Path(a.tasks_json) if a.tasks_json and not a.no_tasks else None
    ids = check_tasks(tasks_path) if tasks_path else []
    pins = load_pins()
    if a.no_tasks:
        pins["STM_NO_TASKS"] = "1"
    pw = os.environ.get("SUDO_PASSWORD", "password")

    sys.path.insert(0, str(REPO / "src"))
    sys.path.insert(0, str(HERE.parent / "common"))
    import guest_run                                   # noqa: E402  (common/guest_run.py)
    from osworld_science.guest.client import Guest     # noqa: E402

    guest = Guest(a.port)
    if not guest.alive(require_display=False):
        raise SystemExit(f"no guest on :{a.port}")
    run(guest, f"rm -rf {PROV}; mkdir -p -m 700 {PROV}")
    with tempfile.TemporaryDirectory() as td:
        env_file = pathlib.Path(td) / "install.env"
        env_file.write_text(install_env(pins), encoding="utf-8")
        guest.upload(env_file, f"{PROV}/install.env")
    if tasks_path:
        guest.upload(tasks_path, f"{PROV}/tasks.json")
    run(guest, f"chmod 600 {PROV}/*")
    print(f"installing STM-Bench {pins['STM_COMMIT'][:12]} ({len(ids)} tasks baked; the list is not printed)")
    rc = guest_run.run(guest, "stm_bootstrap", HERE / "bootstrap_guest.sh", a.minutes)
    if rc != 0:
        return rc
    run(guest, f"rm -rf {PROV}")
    ok = verify(guest)
    if a.smoke:
        ok &= smoke(guest, ids[0], pathlib.Path(a.screenshot), pw)
        ok &= verify(guest)
    if not a.no_scrub:
        print("overwriting free space with zeros (the deleted private copy of the task list must not be recoverable)")
        fill = ("avail=$(df --output=avail -B1M / | tail -1 | tr -d ' '); "
                f"echo {pw} | sudo -S dd if=/dev/zero of=/zero.fill bs=1M count=$((avail-1024)) status=none || true; "
                f"sync; echo {pw} | sudo -S rm -f /zero.fill; sync")
        script = pathlib.Path(tempfile.gettempdir()) / "stm_scrub.sh"
        script.write_text("#!/bin/bash\n" + fill + "\n", encoding="utf-8")
        ok &= guest_run.run(guest, "stm_scrub", script, a.minutes) == 0
        script.unlink(missing_ok=True)
    print("RESULT:", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
