#!/usr/bin/env bash
# After install_xfce_openfoam.sh: reboot into the desktop, wait for the guest server, screenshot, run the Xfce
# verify list through /execute (the same channel the harness uses), and report. Does NOT shut down or flatten.
set -o pipefail
B=$(cd "$(dirname "$0")" && pwd); cd "$B"
S="ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -i $B/id_build -p 2222 user@127.0.0.1"
REPO=/diskarray/home/tl688/orion/OSWorld-Science; PY=$REPO/.venv/bin/python; PORT=5102
say() { echo "[$(date +%H:%M:%S)] $*"; }
say "rebooting the build VM into the desktop"
$S 'sudo systemctl reboot' 2>/dev/null || true
sleep 20
for i in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:$PORT/screenshot 2>/dev/null); [ "$code" = 200 ] && break; sleep 5
done
[ "$code" = 200 ] || { say "guest server never answered on :$PORT"; $S 'systemctl status osworld --no-pager | tail -15; systemctl status lightdm --no-pager | tail -5; ls -la /home/user/.Xauthority /tmp/.X11-unix'; exit 1; }
say "guest server up after ~$((i*5)) s"
curl -s -o "$B/desktop_xfce.png" http://127.0.0.1:$PORT/screenshot && ls -la "$B/desktop_xfce.png"
PYTHONPATH=$REPO/src $PY - "$PORT" <<'PY'
import sys, pathlib
from osworld_science.guest.client import Guest
g=Guest(int(sys.argv[1]))
g.upload(pathlib.Path('/diskarray/home/tl688/orion/OSWorld-Science/scripts/vm_prep/physics/guest/30_verify_xfce.sh'), '/home/user/verify_openfoam.sh')
r=g.execute(["bash","-lc","bash ~/verify_openfoam.sh 2>&1; rc=$?; rm -f ~/verify_openfoam.sh; echo exit=$rc"], 300)
print((r.get("output") or "")+(r.get("error") or ""))
r=g.execute(["bash","-lc","wmctrl -m | head -2; xdpyinfo | grep dimensions; xrandr | grep -E ' connected|\\*' | head -3; ps -eo comm | grep -E 'xfce4-session|xfwm4|xfce4-panel|xfdesktop|Xorg|lightdm' | sort -u | tr '\\n' ' '; echo; systemctl is-active osworld lightdm; cat /proc/cmdline | cut -c1-80"], 60)
print((r.get("output") or "")+(r.get("error") or ""))
PY
