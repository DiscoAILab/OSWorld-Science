#!/usr/bin/env python3
"""Install Weasis (DICOM viewer) into a running guest from the official .deb.

    scripts/vm_prep/radiology/weasis_setup.py --port 5050 [--version 4.7.3]

The .deb brings a bundled runtime, a desktop entry and the application/dicom
association (a GUI agent finds it through the menu or a double-click).
jpackage debs do not land in /usr/bin, so `weasis` is symlinked onto PATH —
the legacy v1 image lacked that and `which weasis` was misread as "not
installed". The guest's `user` is in sudo with password `password`.
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
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--version", default="4.7.3")
    a = ap.parse_args()
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"no guest on :{a.port}")
    installer = f"""set -e
export DEBIAN_FRONTEND=noninteractive
cd /home/user
echo '--- download weasis_{a.version}-1_amd64.deb ---'
curl -sSL -o weasis.deb 'https://github.com/nroduit/Weasis/releases/download/v{a.version}/weasis_{a.version}-1_amd64.deb'
ls -lh weasis.deb
echo '--- install ---'
echo password | sudo -S apt-get install -y -qq ./weasis.deb
rm -f weasis.deb
echo '--- file association: .dcm double-click opens Weasis ---'
xdg-mime default weasis-Weasis.desktop application/dicom 2>/dev/null || true
echo '--- PATH ---'
echo password | sudo -S ln -sf /opt/weasis/bin/Weasis /usr/local/bin/weasis
echo password | sudo -S ln -sf /opt/weasis/bin/Weasis /usr/local/bin/Weasis
echo '--- disable the start-up update check (first-run disclaimer state lives in ~/.weasis) ---'
CFG=/opt/weasis/lib/app/Weasis.cfg
grep -q 'weasis.update.release=false' $CFG || echo 'java-options=-Dweasis.update.release=false' | sudo -S -p '' tee -a $CFG >/dev/null <<< password
echo '--- verify ---'
ls /opt/weasis/bin/Weasis
command -v weasis
echo WEASIS_OK"""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(installer)
        script = pathlib.Path(fh.name)
    try:
        return run(g, "weasis_setup", script, minutes=15)
    finally:
        script.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
