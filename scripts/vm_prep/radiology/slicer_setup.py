#!/usr/bin/env python3
"""Install 3D Slicer into a running guest.

    scripts/vm_prep/radiology/slicer_setup.py --port 5050 [--tarball Slicer-5.12.3-linux-amd64.tar.gz]

Slicer ships only a tar.gz on Linux, so the desktop entry, PATH symlinks and
icon are added here; without them a GUI agent cannot find it in the menu.
Steps: apt deps (the official Ubuntu 22.04 list + mesa-utils to check
OpenGL >= 3.2), tarball (host-uploaded via multipart when --tarball is given,
otherwise downloaded in-guest), extract to /opt/Slicer-<ver> (+ /opt/Slicer
symlink, /usr/local/bin/{Slicer,slicer}, slicer.desktop), tree chowned to
`user` (Slicer writes Extensions-<rev> next to itself). First-run settings are
a separate step (slicer_firstrun.sh, needs a GUI launch).
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

VERSION = "5.12.3"
DIRNAME = f"Slicer-{VERSION}-linux-amd64"
URL = "https://download.slicer.org/download?os=linux&stability=release"

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=3D Slicer
Comment=Medical image computing platform
Exec=/opt/Slicer/Slicer %F
Icon=/opt/Slicer/Slicer.png
Terminal=false
Categories=Science;MedicalSoftware;Graphics;
MimeType=application/x-nifti;application/x-nrrd;
"""

INSTALLER = f"""set -e
export DEBIAN_FRONTEND=noninteractive
echo '--- apt deps (Slicer docs, Ubuntu 22.04) ---'
for attempt in $(seq 1 30); do
  if echo password | sudo -S apt-get update -qq 2>&1 | tail -1; then break; fi
  echo "  waiting for the package-manager lock ($attempt/30)"; sleep 5
done
echo password | sudo -S apt-get -o DPkg::Lock::Timeout=180 install -y -qq libglu1-mesa libpulse-mainloop-glib0 \\
  libnss3 libasound2 qt5dxcb-plugin libsm6 mesa-utils 2>&1 | tail -2
echo '--- OpenGL ---'
DISPLAY=:0 glxinfo -B 2>/dev/null | grep -iE "OpenGL (renderer|core profile version|version)" | head -3
cd /home/user
if [ ! -s slicer.tar.gz ]; then
  echo '--- no uploaded tarball; downloading in guest ---'
  curl -sSL -o slicer.tar.gz '{URL}'
fi
ls -lh slicer.tar.gz
echo '--- extract to /opt ---'
echo password | sudo -S rm -rf /opt/{DIRNAME}
echo password | sudo -S tar xzf slicer.tar.gz -C /opt
rm -f slicer.tar.gz
echo password | sudo -S ln -sfn /opt/{DIRNAME} /opt/Slicer
echo password | sudo -S ln -sf /opt/Slicer/Slicer /usr/local/bin/Slicer
echo password | sudo -S ln -sf /opt/Slicer/Slicer /usr/local/bin/slicer
echo '--- desktop entry (written as user, installed as root) ---'
base64 -d > /tmp/slicer.desktop <<< '{__import__("base64").b64encode(DESKTOP_ENTRY.encode()).decode()}'
echo password | sudo -S install -m 644 /tmp/slicer.desktop /usr/share/applications/slicer.desktop
rm -f /tmp/slicer.desktop
echo password | sudo -S update-desktop-database /usr/share/applications 2>/dev/null || true
echo '--- ownership: Slicer creates slicer.org/Extensions-<rev> beside its install ---'
echo password | sudo -S chown -R user:user /opt/{DIRNAME}
echo '--- verify ---'
ls -la /opt/Slicer/Slicer /usr/local/bin/slicer /usr/share/applications/slicer.desktop
command -v slicer
echo SLICER_OK"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--tarball", type=pathlib.Path, help="host-side Slicer tar.gz to push into the guest")
    a = ap.parse_args()
    g = Guest(a.port)
    if not g.alive():
        sys.exit(f"no guest on :{a.port}")
    if a.tarball:
        if not a.tarball.is_file():
            sys.exit(f"no such tarball: {a.tarball}")
        print(f"[slicer-setup] uploading {a.tarball.name} ({a.tarball.stat().st_size / 1e6:.0f} MB)...",
              flush=True)
        g.upload(a.tarball, "/home/user/slicer.tar.gz")
        print("[slicer-setup] uploaded (size verified)")
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(INSTALLER)
        script = pathlib.Path(fh.name)
    try:
        return run(g, "slicer_install", script, minutes=40)
    finally:
        script.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
