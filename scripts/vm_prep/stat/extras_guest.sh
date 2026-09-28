#!/usr/bin/env bash
# Everything in the stat image besides statenv: root-partition growth, RStudio
# Desktop, Python stats packages, ImageMagick dev headers, and the package
# needed by growpart. Runs with sudo inside the guest (password `password`).
#
#   scripts/vm_prep/stat/extras_guest.sh --port 5040
#
# Reconstructed from vm/BAKED_IMAGE.md of the legacy suite (these steps were
# done by hand there); versions are the ones the legacy image reports.
# SAS is deliberately absent (a licensed product that cannot be redistributed).
set -euo pipefail
PORT=5040
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; *) shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
RSTUDIO_DEB=${RSTUDIO_DEB_URL:-https://download1.rstudio.org/electron/jammy/amd64/rstudio-2025.09.1-401-amd64.deb}
TMP=$(mktemp)
cat > "$TMP" <<GUEST
set -e
export DEBIAN_FRONTEND=noninteractive
echo '--- apt (retry: unattended-upgrades may hold the lock right after boot) ---'
for attempt in \$(seq 1 30); do
  if echo password | sudo -S apt-get update -qq 2>&1 | tail -1; then break; fi
  echo "  waiting for the package-manager lock (\$attempt/30)"; sleep 5
done
echo password | sudo -S apt-get -o DPkg::Lock::Timeout=180 install -y -qq cloud-guest-utils python3-pip libmagick++-dev 2>&1 | tail -2
echo '--- root partition 29.5G -> full disk (growpart + resize2fs) ---'
ROOTDEV=\$(findmnt -n -o SOURCE /)
DISK=\$(lsblk -no PKNAME "\$ROOTDEV")
PART=\$(echo "\$ROOTDEV" | grep -o '[0-9]*\$')
echo password | sudo -S growpart /dev/\$DISK \$PART || echo "  (growpart: nothing to do)"
echo password | sudo -S resize2fs "\$ROOTDEV" || true
df -h / | tail -1
echo '--- RStudio Desktop ---'
cd /home/user
curl -sSL -o rstudio.deb '$RSTUDIO_DEB'
echo password | sudo -S apt-get install -y -qq ./rstudio.deb 2>&1 | tail -2
rm -f rstudio.deb
grep -q RSTUDIO_WHICH_R /etc/environment || echo 'RSTUDIO_WHICH_R=/home/user/statenv/bin/R' | sudo -S -p '' tee -a /etc/environment >/dev/null <<< password
echo '--- Python stats packages (user site) ---'
python3 -m pip install --user -q pandas scipy numpy matplotlib openpyxl 2>&1 | tail -1
python3 -c 'import pandas, scipy, numpy, matplotlib, openpyxl; print("python packages ok")'
echo '--- verify ---'
command -v rstudio && rstudio --version 2>/dev/null | head -1 || true
rm -rf /home/user/.cache/pip
echo EXTRAS_OK
GUEST
uv --directory "$REPO" run python "$HERE/../common/guest_run.py" stat_extras "$TMP" --port "$PORT" --minutes 40
rc=$?; rm -f "$TMP"; exit $rc
