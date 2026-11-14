#!/usr/bin/env bash
# Runs INSIDE the guest (delivered by guest_run.py): the desktop of the R-in-a-GUI
# linguistics labs. Same package set and launchers as the task authors' own setup
# script (the task package's runtime/guest_install.sh), installed once and baked
# instead of on every run.
#
#   R (r-base), RKWard, r-cran-tidyverse (ggplot2 included), Praat, LibreOffice Writer and
#   Calc, Evince, wmctrl, xclip, all from the Ubuntu 22.04 archive
#   ~/.config/rkwardrc   R's native graphics device (Ubuntu's RKWard/R graphics API mismatch)
#   ~/Desktop/*.desktop  launchers for RKWard, Praat, Writer, Calc, the PDF reader and the lab folders
#
# Safe to run again on an image that already has part of this (an image baked before Praat was
# added gets only Praat and the two new launchers).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo '--- apt (retry: unattended-upgrades may hold the lock right after boot) ---'
updated=0
for attempt in $(seq 1 30); do
  if echo password | sudo -S -p '' apt-get -o DPkg::Lock::Timeout=180 update -qq 2>&1 | tail -1; then updated=1; break; fi
  echo "  waiting for the package mirror or lock ($attempt/30)"; sleep 5
done
test "$updated" = 1
echo password | sudo -S -p '' env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=180 install -y --no-install-recommends \
  r-base rkward r-cran-tidyverse praat libreoffice-writer libreoffice-calc evince wmctrl xclip 2>&1 | tail -3

echo '--- RKWard graphics device + desktop launchers ---'
python3 - <<'PY'
from pathlib import Path
import subprocess
desktop = Path('/home/user/Desktop')
desktop.mkdir(parents=True, exist_ok=True)
config = Path('/home/user/.config/rkwardrc')
config.parent.mkdir(parents=True, exist_ok=True)
config.write_text('[Graphics Device Windows]\ndefault_device=1\nreplace_device=2\n')
apps = {
    'RKWard': ('rkward', 'rkward'),
    'Praat': ('praat --hide-picture', 'praat'),
    'Lab report Writer': ('libreoffice --writer', 'libreoffice-writer'),
    'Lab tables Calc': ('libreoffice --calc', 'libreoffice-calc'),
    'PDF reader': ('evince', 'evince'),
    'Hoosier Lab1 files': ('xdg-open /home/user/Desktop/HoosierLab1', 'folder'),
    'Vowel duration Lab2 files': ('xdg-open /home/user/Desktop/VowelDurationLab2', 'folder'),
}
for name, (command, icon) in apps.items():
    path = desktop / (name + '.desktop')
    path.write_text('[Desktop Entry]\nVersion=1.0\nType=Application\nName=' + name +
                    '\nExec=' + command + '\nIcon=' + icon + '\nTerminal=false\n')
    path.chmod(0o755)
    subprocess.run(['gio', 'set', str(path), 'metadata::trusted', 'true'],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PY

echo '--- verify ---'
for c in rkward R Rscript praat libreoffice evince wmctrl xclip; do command -v "$c"; done
Rscript --vanilla -e 'suppressPackageStartupMessages(library(tidyverse)); cat(R.version.string, "| tidyverse", as.character(packageVersion("tidyverse")), "\n")'
dpkg-query -W -f='${Package} ${Version}\n' r-base rkward r-cran-tidyverse praat libreoffice-writer libreoffice-calc evince wmctrl xclip
ls /home/user/Desktop/*.desktop
echo RKWARD_OK
