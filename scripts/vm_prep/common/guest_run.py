#!/usr/bin/env python3
"""Run a long script inside the guest, detached, and poll its completion flag.

    scripts/vm_prep/common/guest_run.py <name> <local-script.sh> --port 5050 [--minutes 30] [--keep]

Why: the guest control plane's /execute has a ~120 s server-side timeout that
the client cannot raise; apt, conda and tarball extraction all exceed it. The
script is uploaded to /home/user/<name>.sh, started with setsid in the
background, stdout+stderr go to /home/user/<name>.log and the exit code to
<name>.done. Success = flag present AND exit code 0; an exhausted poll loop
is reported as a timeout, never as success. The three files are removed
afterwards so the image carries no trace (--keep to inspect them).
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
from osworld_science.guest.client import Guest  # noqa: E402


def run(guest: Guest, name: str, script: pathlib.Path, minutes: float, keep: bool = False) -> int:
    remote, log, flag = (f"/home/user/{name}.sh", f"/home/user/{name}.log", f"/home/user/{name}.done")
    guest.upload(script, remote)
    guest.execute(["bash", "-lc", f"rm -f {flag}; chmod +x {remote}"], 60)
    guest.execute(["bash", "-lc",
                   f"setsid nohup bash -c 'bash {remote} > {log} 2>&1; echo $? > {flag}' "
                   f">/dev/null 2>&1 < /dev/null & echo launched"], 60)
    deadline, last = time.time() + minutes * 60, ""
    while time.time() < deadline:
        time.sleep(10)
        rc = (guest.execute(["bash", "-lc", f"cat {flag} 2>/dev/null"], 30).get("output") or "").strip()
        tail = (guest.execute(["bash", "-lc", f"tail -1 {log} 2>/dev/null | cut -c1-110"], 30)
                .get("output") or "").strip()
        if tail and tail != last:
            print(f"  … {tail}", flush=True)
            last = tail
        if rc != "":
            out = guest.execute(["bash", "-lc", f"tail -30 {log}"], 60).get("output") or ""
            print(f"── guest exit {rc}; log tail:\n{out.strip()}")
            if not keep:
                guest.execute(["bash", "-lc", f"rm -f {remote} {log} {flag}"], 60)
            return 0 if rc == "0" else 1
    print("── TIMED OUT — this is a timeout, not a success")
    print(guest.execute(["bash", "-lc", f"tail -30 {log}"], 60).get("output") or "")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("script")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"no guest on :{a.port}")
    return run(g, a.name, pathlib.Path(a.script), a.minutes, a.keep)


if __name__ == "__main__":
    sys.exit(main())
