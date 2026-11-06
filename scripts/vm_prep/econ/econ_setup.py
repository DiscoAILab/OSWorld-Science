#!/usr/bin/env python3
"""Install gretl and LibreOffice Calc into a running OSWorld guest (Ubuntu 22.04 jammy).

    scripts/vm_prep/econ/econ_setup.py --port 5160 [--gretl-version 2021d-1build1] [--no-pin]

gretl ships gretl_x11 (the GUI) and gretlcli; libreoffice-calc brings Calc with the GTK3 VCL plug-in.
LibreOffice's first-run dialogs (tip of the day, "keep current format?") are switched off in the
user profile so agents do not burn steps on them. apt is retried because unattended-upgrades may hold
the lock right after boot. Deliberately NOT installed: R/statsmodels-style Python econometrics
packages (the task is about gretl; python3 + pandas come with the base image and are allowed on the
hybrid variant only because the instruction permits a terminal there).

The reference values in data/econ/private were computed with gretl 2023c (Ubuntu 24.04). After
baking, run reference_private/ECON-001/make_gt.py inside the guest and diff against gt.json: the
Johansen/VECM estimators are closed-form, so a packaged 2021d/2022x gretl is expected to agree to the
tolerances, but verify before publishing the image.
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

LO_PROFILE = r"""<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ShowTipOfTheDay" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Save/Document"><prop oor:name="WarnAlienFormat" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Recovery/RecoveryInfo"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5160)
    ap.add_argument("--gretl-version", default="", help="apt version string to pin; '' = repository candidate")
    a = ap.parse_args()
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"no guest on :{a.port}")
    gretl = f"gretl={a.gretl_version}" if a.gretl_version else "gretl"
    profile = LO_PROFILE.replace("'", "'\"'\"'")
    installer = f"""set -e
export DEBIAN_FRONTEND=noninteractive
echo '--- apt-get update (retries: unattended-upgrades may hold the lock right after boot) ---'
updated=0
for attempt in $(seq 1 30); do
  if echo password | sudo -S apt-get update -qq 2>&1 | tail -2; then updated=1; break; fi
  echo "  waiting for package-manager lock or mirror (attempt $attempt/30)"; sleep 5
done
test "$updated" = 1
echo '--- install {gretl} gretl-common gretl-data libreoffice-calc libreoffice-gtk3 ---'
echo password | sudo -S apt-get -o DPkg::Lock::Timeout=180 install -y -qq --no-install-recommends \\
    {gretl} gretl-common gretl-data libreoffice-calc libreoffice-gtk3 python3-openpyxl 2>&1 | tail -5
echo '--- LibreOffice: no first-run / tip-of-the-day / alien-format dialogs ---'
mkdir -p /home/user/.config/libreoffice/4/user
printf '%s' '{profile}' > /home/user/.config/libreoffice/4/user/registrymodifications.xcu
echo '--- verify ---'
command -v gretl
command -v gretlcli
command -v libreoffice
gretlcli --version | head -1
dpkg-query -W -f='${{Package}} ${{Version}}\\n' gretl libreoffice-calc
timeout 60 libreoffice --headless --version
echo ECON_OK"""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(installer)
        script = pathlib.Path(fh.name)
    try:
        return run(g, "econ_setup", script, minutes=40)
    finally:
        script.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
