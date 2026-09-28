#!/usr/bin/env python3
"""Install Praat into a running guest from the Ubuntu 22.04 (jammy) repository.

    scripts/vm_prep/linguistics/praat_setup.py --port 5080 [--version 6.2.09-1]

Pinned to 6.2.09-1, the version the task author validated. The package also
provides /usr/bin/sendpraat, which the task's staging uses to open the
annotation editor. apt is retried because unattended-upgrades may hold the
lock right after boot. Deliberately NOT installed: parselmouth or any Python
speech library (the instruction requires the GUI route).
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent / "common"))
from guest_run import run  # noqa: E402

from osworld_science.guest.client import Guest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5080)
    ap.add_argument("--version", default="6.2.09-1", help="apt version string; '' = repository candidate")
    a = ap.parse_args()
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"no guest on :{a.port}")
    pkg = f"praat={a.version}" if a.version else "praat"
    installer = f"""set -e
export DEBIAN_FRONTEND=noninteractive
echo '--- apt-get update (retries: unattended-upgrades may hold the lock right after boot) ---'
updated=0
for attempt in $(seq 1 30); do
  if echo password | sudo -S apt-get update -qq 2>&1 | tail -2; then updated=1; break; fi
  echo "  waiting for package-manager lock or mirror (attempt $attempt/30)"; sleep 5
done
test "$updated" = 1
echo '--- install {pkg} ---'
echo password | sudo -S apt-get -o DPkg::Lock::Timeout=180 install -y -qq {pkg} 2>&1 | tail -5
echo '--- verify ---'
command -v praat
command -v sendpraat
praat --version
dpkg-query -W -f='${{Package}} ${{Version}}\\n' praat
echo PRAAT_OK"""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(installer)
        script = pathlib.Path(fh.name)
    try:
        return run(g, "praat_setup", script, minutes=20)
    finally:
        script.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
