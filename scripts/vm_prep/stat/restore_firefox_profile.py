#!/usr/bin/env python3
"""Pre-task hook for SAS tasks: restore a Firefox profile that carries a
SAS OnDemand session, the saved password and the pop-up permission.

    scripts/vm_prep/stat/restore_firefox_profile.py --port 5040

The archive (`firefox_profile.tgz` next to this script) is NOT published —
it holds live credentials. Without it this hook exits 0 after printing a
note (the hook is declared optional in configs/snapshots.yaml), so the
task runs with the R route only, exactly as the legacy sweep behaved when
the restore failed. Create the archive with snapshot_firefox_profile.sh
after signing in by hand through noVNC.

What it restores is the saved password and the pop-up permission, not a
session: the guest comes up at "Sign In" with the form pre-filled, and the
agent signs itself in from there (kimi-k3 did so unaided on
stat_liver_cohort and stat_group_tests in the 2026-09-01 sweep). What it
cannot touch at all is the ODA account, which is cloud-side and survives
every guest reset — run check_oda_home.py by hand before a SAS sweep.

Why a profile archive rather than scripting the login: three attempts at
driving SAS's sign-in page by pixel coordinates failed three different ways.
A web page owned by someone else is not a stable API; a profile directory is.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
ARCHIVE = HERE / "firefox_profile.tgz"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5040)
    a = ap.parse_args()
    if not ARCHIVE.exists():
        print(f"note: no {ARCHIVE.name} here — SAS OnDemand session not restored; the R route "
              f"remains available (sign in by hand, then scripts/vm_prep/stat/snapshot_firefox_profile.sh)")
        return 0
    from osworld_science.guest.client import Guest
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"no guest on :{a.port}")
    print("closing firefox")
    g.execute(["bash", "-lc", "pkill -x firefox 2>/dev/null; sleep 5; echo ok"], 120)
    print(f"uploading the profile ({ARCHIVE.stat().st_size / 1e6:.1f} MB)")
    t0 = time.time()
    g.upload(ARCHIVE, "/home/user/ff_profile.tgz")
    print(f"  uploaded in {time.time() - t0:.0f}s")
    r = g.execute(["bash", "-lc",
                   "set -e; cd /home/user/snap/firefox/common/.mozilla; rm -rf firefox; "
                   "tar xzf /home/user/ff_profile.tgz; rm -f /home/user/ff_profile.tgz; du -sh firefox"], 400)
    out = (r.get("output") or "") + (r.get("error") or "")
    print("  " + out.strip())
    if "firefox" not in out:
        print("!! no profile directory after unpacking")
        return 1
    g.execute(["bash", "-lc", "DISPLAY=:0 setsid nohup firefox --new-window "
               "https://welcome.oda.sas.com/ >/dev/null 2>&1 < /dev/null & echo ok"], 60)
    time.sleep(35)
    print("windows:", (g.windows().strip().splitlines()[-1:] or ["none"])[0])
    print("note: the ODA account keeps whatever the last run uploaded; this hook "
          "cannot see it. Confirm it is empty with check_oda_home.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
