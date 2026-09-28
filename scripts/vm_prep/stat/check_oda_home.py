#!/usr/bin/env python3
"""Confirm the SAS OnDemand home directory is empty before a SAS run.

    scripts/vm_prep/stat/check_oda_home.py --port 5040

The ODA account is cloud-side, so it is the one part of the environment a
guest reset cannot touch: rebuild the container all you like and the account
still holds whatever the last run uploaded. On 2026-08-12 it held seven files
from earlier sessions, one of them `bili-dataset.txt` — the liver task's own
source data. A solver on that task would have found dataset A already sitting
in SAS without ever reading the file the task delivered. The sweep of
2026-09-01 left `liver_a.dat`, `liver_b.dat`, `growth_a.csv`, `growth_b.csv`,
`results_a.csv` and `results_b.csv` behind, so the account is dirty now.

Every click here is yours. The one attempt at driving this page by pixel
coordinates (`reset_sas_session.py` in the legacy suite) had two steps that
silently did the opposite of what they claimed: the shift-click range
selection deleted a single file rather than the range, and the tab-close
clicks landed on the new-tab control. On this page a click that lands on
nothing returns success, so this script only screenshots and asks — it never
clicks.

It puts the pristine profile back at the end, which signs the browser out.
That is parity, not tidiness: in the recorded sweep the episode began signed
OUT and the agent signed itself in off the saved password (kimi-k3 did
exactly that on `stat_liver_cohort` and `stat_group_tests`). Leaving a live
session behind would hand the next agent a step the task means to measure.
Pass --keep-session when you are debugging and want the browser left as is.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))

ARCHIVE = HERE / "firefox_profile.tgz"
KEEP = "sasuser.v94"          # SAS's own directory; removing it breaks the session

CHECKLIST = """\
  1  Sign In (top right). Accept the autofilled e-mail and password — do not
     type them. Typing appends to what autofill already put there, and the
     resulting user@x.eduuser@x.edu is rejected.
  2  Tick the terms box, submit.
  3  Click "Clear my saved tabs" BEFORE Launch. Closing tabs by their X
     buttons creates tabs instead of closing them.
  4  Press Launch, then expand Server Files and Folders -> Files (Home) so the
     whole tree is on screen.
"""

DELETE_HELP = f"""\
  Select the file, click the trash icon, then PRESS ENTER to confirm.
  Do not click the confirm button: the dialog is sized to its message, so the
  button's x position depends on how long the file name is. Coordinates
  measured on a two-line message land in dead space on a one-line one, which
  does nothing and reports success.
  Everything goes except {KEEP}.
"""


def novnc_url(port: int) -> str:
    return f"http://localhost:{port + 3006}"


def ask(prompt: str) -> str:
    if not sys.stdin.isatty():
        sys.exit("this tool needs a terminal: it asks you to confirm what is on screen")
    return input(prompt).strip().lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5040)
    ap.add_argument("--keep-session", action="store_true",
                    help="leave the browser signed in instead of restoring the pristine profile")
    ap.add_argument("--note", default="", help="recorded in the receipt")
    a = ap.parse_args()

    if not ARCHIVE.exists():
        sys.exit(f"no {ARCHIVE.name} next to this script — without the saved password "
                 f"there is nothing to sign in with, and the SAS route is unavailable "
                 f"anyway")

    from osworld_science.config import Settings
    from osworld_science.guest.client import Guest

    settings = Settings.load(REPO)
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"no guest on :{a.port}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = settings.runs_dir / "oda_checks" / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    print("restoring the profile so the sign-in form fills itself")
    subprocess.run([sys.executable, str(HERE / "restore_firefox_profile.py"),
                    "--port", str(a.port)], cwd=REPO, check=True)

    print(f"\nopen the guest desktop at {novnc_url(a.port)} and do this:\n\n{CHECKLIST}")

    shots: list[str] = []
    while True:
        ask("press Enter when the Files (Home) tree is on screen ")
        png = g.screenshot()
        if not png:
            print("!! the guest returned no screenshot; is it still up?")
            continue
        shot = outdir / f"oda_home_{len(shots) + 1:02d}.png"
        shot.write_bytes(png)
        shots.append(shot.name)
        print(f"  saved {shot}")
        answer = ask(f"does the tree hold nothing but {KEEP}? [y/n] ")
        if answer.startswith("y"):
            break
        print(f"\n{DELETE_HELP}")

    receipt = {
        "checked_at": stamp,
        "port": a.port,
        "confirmed_clean": True,
        "screenshots": shots,
        "session_left": "signed-in" if a.keep_session else "signed-out",
        "note": a.note,
    }

    if a.keep_session:
        print("\nleaving the browser signed in (--keep-session): the next episode will "
              "NOT match the recorded sweep, where the agent signed itself in")
    else:
        print("\nputting the pristine profile back so the episode starts signed out")
        subprocess.run([sys.executable, str(HERE / "restore_firefox_profile.py"),
                        "--port", str(a.port)], cwd=REPO, check=True)
        time.sleep(2)

    (outdir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"\nreceipt -> {outdir / 'receipt.json'}")
    print("the account is clean; the run can start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
