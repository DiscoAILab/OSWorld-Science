#!/usr/bin/env python3
"""Pre-task hook for the Mnova (MestReNova) NMR task: put the operator's Mnova
licence files into the guest at /home/user/licenses/.

    scripts/vm_prep/biomed/install_mnova_license.py --port 5100

Mnova will not open Bruker data until a licence validates, and a licence is
issued to one institution, so none is published with the benchmark. Put your
own `.lic` file(s) in `mnova_licenses/` next to this script, or point
OSCI_MNOVA_LICENSE_DIR at a directory holding them; both are ignored by git.
The task instruction tells the agent to import the NMR licence from
/home/user/licenses, which staging creates but never empties.

A floating (campus or site) licence also needs the guest to reach its licence
server. If `mnova_site.sh` exists next to this script (also ignored by git) it
is copied into the guest and run as root once the licences are in place; use it
for whatever your site needs, for example an /etc/hosts entry that sends the
server name through a relay the guest can reach.

Without a licence file this hook fails, and configs/snapshots.yaml declares it
required for tasks whose related_apps contain "mnova": grading an agent against
an application that cannot be licensed measures nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
GUEST_DIR = "/home/user/licenses"
SITE_SCRIPT = HERE / "mnova_site.sh"


def licence_dir() -> Path:
    env = os.environ.get("OSCI_MNOVA_LICENSE_DIR")
    return Path(env).expanduser() if env else HERE / "mnova_licenses"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5100)
    a = ap.parse_args()
    src = licence_dir()
    lics = sorted(p for p in src.glob("*.lic") if p.is_file()) if src.is_dir() else []
    if not lics:
        print(f"no Mnova licence (*.lic) in {src}: this task cannot run without one "
              f"(see scripts/vm_prep/biomed/README.md)", file=sys.stderr)
        return 1
    from osworld_science.guest.client import Guest
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"no guest on :{a.port}")
    g.execute(["bash", "-c", f"mkdir -p {GUEST_DIR}"], 60)
    for p in lics:
        g.upload(p, f"{GUEST_DIR}/{p.name}")
    # only the count is logged: a licence file name identifies the institution it was issued to
    print(f"installed {len(lics)} licence file(s) into {GUEST_DIR}")
    if SITE_SCRIPT.is_file():
        g.upload(SITE_SCRIPT, "/tmp/mnova_site.sh")
        r = g.execute(["bash", "-c", "echo password | sudo -S bash /tmp/mnova_site.sh; rc=$?; "
                                     "rm -f /tmp/mnova_site.sh; exit $rc"], 300)
        if r.get("returncode") not in (0, None):
            out = ((r.get("output") or "") + (r.get("error") or "")).strip()
            print(f"mnova_site.sh failed (rc={r.get('returncode')}): {out[-300:]}", file=sys.stderr)
            return 1
        print("ran mnova_site.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
