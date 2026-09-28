#!/usr/bin/env bash
# Install the OSWorld-Science OpenFOAM desktop into a running guest (the pristine base or a partial image).
#
#   scripts/vm_prep/physics/provision_guest.sh --port 5100
#
# Ports env/Dockerfile of the former native-container guest (osworld-openfoam-guest:v2512-pv5.11.2 r3)
# onto the QEMU/KVM OSWorld base: OpenFOAM ESI v2512 from dl.openfoam.com, ParaView 5.11.2 (the same
# official tarball, md5-checked), gnuplot-qt, python scipy/matplotlib/pandas, the CLI tool list, the
# no-browser URL handler, MIME associations, the guest-server stdin patch, non-blocking tool defaults.
# Long steps run detached inside the guest (guest_run.py) because /execute caps at 120 s.
set -o pipefail
PORT=5100
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; *) shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
PY=${PY:-$REPO/.venv/bin/python}; [ -x "$PY" ] || PY="uv --directory $REPO run python"
say() { echo "[$(date +%H:%M:%S)] $*"; }
run_guest() { $PY "$REPO/scripts/vm_prep/common/guest_run.py" "$@" --port "$PORT"; }
# --- python helpers (one interpreter, the framework's Guest client) ---
gpy() { PYTHONPATH="$REPO/src" $PY -c "$1" "$PORT" "${@:2}"; }
upload() { gpy 'import sys,pathlib
from osworld_science.guest.client import Guest
Guest(int(sys.argv[1])).upload(pathlib.Path(sys.argv[2]), sys.argv[3]); print("uploaded", sys.argv[3])' "$1" "$2"; }
gbash() { gpy 'import sys
from osworld_science.guest.client import Guest
r=Guest(int(sys.argv[1])).execute(["bash","-lc",sys.argv[2]], int(sys.argv[3]))
print(((r.get("output") or "")+(r.get("error") or "")).rstrip()); sys.exit(0 if r.get("returncode") in (0,None) else 1)' "$1" "${2:-120}"; }

say "guest check on :$PORT"
gbash 'echo alive; . /etc/os-release; echo $PRETTY_NAME; df -h / | tail -1' || { echo "no guest on :$PORT"; exit 1; }

say "uploading the build-context files (etc/, bin/, desktop/) and the ParaView tarball (584 MB, md5 36d2cb9a…)"
TMPTAR=$(mktemp /tmp/physics_files.XXXX.tar.gz)
tar -C "$HERE/files" -czf "$TMPTAR" etc bin desktop guest_server
upload "$TMPTAR" /home/user/physics_files.tar.gz; rm -f "$TMPTAR"
gbash 'rm -rf ~/.osci_physics_files && mkdir -p ~/.osci_physics_files && tar -C ~/.osci_physics_files -xzf ~/physics_files.tar.gz && rm ~/physics_files.tar.gz && ls ~/.osci_physics_files'
upload "$HERE/files/ParaView-5.11.2-MPI-Linux-Python3.9-x86_64.tar.gz" /home/user/paraview.tar.gz
gbash 'md5sum ~/paraview.tar.gz'

say "stage 1/3: system (partition, apt repo, OpenFOAM v2512, packages, profile.d, server patch) — detached, up to 60 min"
run_guest of_system "$HERE/guest/10_system.sh" --minutes 60 || { say "!! stage 1 failed"; exit 1; }

say "stage 2/3: ParaView 5.11.2 + desktop — detached, up to 20 min"
run_guest of_desktop "$HERE/guest/20_paraview_desktop.sh" --minutes 20 || { say "!! stage 2 failed"; exit 1; }

say "restarting the guest server so the stdin patch and its environment take effect"
gbash 'setsid nohup bash -c "sleep 2; sudo systemctl restart osworld" >/dev/null 2>&1 < /dev/null & echo restarting' 30
sleep 12
for i in $(seq 1 30); do gbash 'echo back' 15 >/dev/null 2>&1 && break; sleep 4; done
gbash 'systemctl show osworld -p Environment | tr " " "\n" | grep -E "MPLBACKEND|PAGER|LIBGL"'

say "stage 3/3: verification from a login shell"
upload "$HERE/guest/30_verify.sh" /home/user/verify_openfoam.sh
gbash 'bash ~/verify_openfoam.sh 2>&1; rc=$?; rm -f ~/verify_openfoam.sh; rm -rf ~/.osci_physics_files; exit $rc' 300
rc=$?
[ $rc = 0 ] && say "provisioning complete" || say "!! verification reported failures (exit $rc)"
exit $rc
