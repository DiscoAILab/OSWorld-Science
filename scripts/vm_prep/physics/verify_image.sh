#!/usr/bin/env bash
# Acceptance of data/physics/vm/ubuntu_openfoam.qcow2 from a FRESH container + FRESH volume (never the bake
# container: its overlay proves "installed", only a cold start proves "stored in the image").
#
#   scripts/vm_prep/physics/verify_image.sh [--port 5100] [--out DIR]
#
# Order matters: leak channels are inspected BEFORE any tool runs (foamDictionary / paraview write ~/.config).
set -o pipefail
PORT=5100; OUT=""
while [ $# -gt 0 ]; do case "$1" in --port) PORT=$2; shift 2;; --out) OUT=$2; shift 2;; *) shift;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../../.." && pwd); cd "$REPO"
PY=$REPO/.venv/bin/python; [ -x "$PY" ] || PY="uv run python"
# osci talks to the rootless daemon when one exists; so must every docker call here
[ -S "/run/user/$(id -u)/docker.sock" ] && export DOCKER_HOST="${DOCKER_HOST:-unix:///run/user/$(id -u)/docker.sock}"
OUT=${OUT:-$REPO/vm/verify_ubuntu_openfoam_$(date +%Y%m%d_%H%M%S)}; mkdir -p "$OUT"
say() { echo "[$(date +%H:%M:%S)] $*"; }
gpy() { PYTHONPATH="$REPO/src" $PY -c "$1" "$PORT" "${@:2}"; }
gbash() { gpy 'import sys
from osworld_science.guest.client import Guest
r=Guest(int(sys.argv[1])).execute(["bash","-lc",sys.argv[2]], int(sys.argv[3]))
print(((r.get("output") or "")+(r.get("error") or "")).rstrip()); sys.exit(0 if r.get("returncode") in (0,None) else 1)' "$1" "${2:-120}"; }
fails=0; chk() { if [ "$2" = 0 ]; then echo "[PASS] $1"; else echo "[FAIL] $1"; fails=$((fails+1)); fi; }

say "1. cold start from the image (fresh container + volume); the tools probe must pass without provisioning"
$PY -m osworld_science.cli vm reset --snapshot ubuntu_openfoam --port "$PORT" 2>&1 | tee "$OUT/reset.log" | tail -5
grep -q 'toolchain present' "$OUT/reset.log"; chk "toolchain present on cold start (no provisioning ran)" $?
docker inspect "osci_of_$PORT" --format 'container {{.Id}} created={{.Created}} image-mount={{range .Mounts}}{{if eq .Destination "/System.qcow2"}}{{.Source}}{{end}}{{end}}' | tee "$OUT/container.txt"

say "2. leak channels, BEFORE any tool runs"
gbash 'ls -la ~/work 2>&1 | head -3; echo "--- history:"; wc -c ~/.bash_history 2>/dev/null || echo "no ~/.bash_history"; echo "--- paraview config:"; ls -la ~/.config/ParaView 2>&1; cat ~/.config/ParaView/ParaView5.11.2.ini 2>&1; echo "--- recently-used:"; ls ~/.local/share/recently-used.xbel 2>&1; echo "--- viminfo/lesshst:"; ls ~/.viminfo ~/.lesshst 2>&1' 60 | tee "$OUT/leaks.txt"
gbash '[ ! -s ~/.bash_history ] && [ ! -f ~/.config/ParaView/ParaView-UserSettings.json ] && [ ! -f ~/.local/share/recently-used.xbel ] && [ -z "$(ls -A ~/work 2>/dev/null)" ] && ! grep -qi recent ~/.config/ParaView/ParaView5.11.2.ini' 30 >/dev/null; chk "no history / no ParaView recent files / empty ~/work" $?

say "3. filesystem health (static, then after a real 2 GB write)"
gbash 'sudo tune2fs -l $(findmnt -n -o SOURCE /) | grep -E "Filesystem state|Errors behavior|FS Error count|Last checked"; dd if=/dev/zero of=/home/user/work/_fill bs=1M count=2048 conv=fsync status=none && sync && rm -f /home/user/work/_fill && echo "2GB write ok"; sudo tune2fs -l $(findmnt -n -o SOURCE /) | grep -E "Filesystem state|FS Error count"; df -h / | tail -1' 110 | tee "$OUT/fs.txt"
grep -q 'Filesystem state: *clean' "$OUT/fs.txt" && ! grep -qE 'FS Error count: *[1-9]' "$OUT/fs.txt"; chk "ext4 clean, 0 errors, before and after a 2 GB write" $?
{ gbash 'systemctl is-enabled fstrim.timer 2>&1' 20 || true; } | grep -q masked; chk "fstrim.timer masked" $?

say "4. the r3 verify list (30_verify_xfce.sh) from a login shell"
gpy 'import sys,pathlib
from osworld_science.guest.client import Guest
Guest(int(sys.argv[1])).upload(pathlib.Path(sys.argv[2]), "/home/user/verify_openfoam.sh")' "$HERE/guest/30_verify_xfce.sh"
gbash 'bash ~/verify_openfoam.sh 2>&1; rc=$?; rm -f ~/verify_openfoam.sh; exit $rc' 300 | tee "$OUT/verify.txt"
grep -q 'verify: 0 failure' "$OUT/verify.txt"; chk "30_verify_xfce.sh: all PASS" $?

say "5. what a solver's terminal sees + screenshot"
$PY -m osworld_science.cli vm check --snapshot ubuntu_openfoam --port "$PORT" > "$OUT/check.txt" 2>&1; cat "$OUT/check.txt"
! grep -q MISSING "$OUT/check.txt" && grep -q 'simpleFoam */usr/lib/openfoam' "$OUT/check.txt"; chk "osci vm check: nothing MISSING" $?
gpy 'import sys,pathlib
from osworld_science.guest.client import Guest
b=Guest(int(sys.argv[1])).screenshot(); pathlib.Path(sys.argv[2]).write_bytes(b or b""); print("screenshot", len(b or b""), "bytes")' "$OUT/desktop.png"
[ -s "$OUT/desktop.png" ]; chk "screenshot captured ($OUT/desktop.png)" $?
gbash 'xdpyinfo 2>/dev/null | grep dimensions; wmctrl -m | head -1; grep -c paraview /etc/xdg/xfce4/panel/default.xml' 30 | tee "$OUT/desktop.txt"

say "6. residue after verification (tools ran now, so ~/.config may have grown — informational)"
gbash 'ls -la ~/.config/ParaView; ls ~/work' 30 | tee -a "$OUT/leaks_after.txt"
echo "verify_image: $fails failure(s); artefacts in $OUT" | tee "$OUT/RESULT.txt"
exit $((fails>0))
