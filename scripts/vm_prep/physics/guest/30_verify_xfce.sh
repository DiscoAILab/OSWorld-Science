#!/usr/bin/env bash
# ubuntu_openfoam (Xfce edition) verification, run inside the guest through `bash -lc`: the r3 README "Verify" list.
# Prints PASS/FAIL lines; exit 1 if any FAIL.
fails=0
chk() { if [ "$2" = 0 ]; then echo "[PASS] $1"; else echo "[FAIL] $1"; fails=$((fails+1)); fi; }
v=$(foamVersion 2>/dev/null | head -1 || true); [[ "$v" == *2512* ]] || v=$(echo "$WM_PROJECT_VERSION"); [[ "$v" == *2512* ]]; chk "OpenFOAM v2512 in a login shell ($v)" $?
command -v blockMesh icoFoam simpleFoam foamDictionary decomposePar mpirun >/dev/null; chk "solver binaries on PATH" $?
n=$(ls $WM_PROJECT_DIR/tutorials 2>/dev/null | wc -l); [ "$n" -ge 10 ]; chk "tutorials present ($n top-level dirs)" $?
rm -rf /tmp/cav && cp -r $FOAM_TUTORIALS/incompressible/icoFoam/cavity/cavity /tmp/cav && cd /tmp/cav && blockMesh >/dev/null 2>&1 && icoFoam >/tmp/cav/log 2>&1 && [ -d 0.5 ]; chk "cavity tutorial: blockMesh + icoFoam to t=0.5" $?
grep -q '^ExecutionTime' /tmp/cav/log; chk "icoFoam log has ExecutionTime" $?
[ "$(nproc)" -ge 2 ]; chk "nproc = $(nproc)" $?
/opt/paraview/bin/pvpython -c "import paraview; print(paraview.__version__)" 2>/dev/null | grep -q '^5\.11\.2$'; chk "pvpython imports, ParaView 5.11.2" $?
( timeout 5 cat; echo "rc=$?" ) 2>&1 | grep -q 'rc=0'; chk "stdin is /dev/null under /execute (cat returns at once)" $?
touch /tmp/cav/case.foam; DISPLAY=:0 timeout 100 /opt/paraview/bin/pvpython -c "
from paraview.simple import *
r=OpenFOAMReader(FileName='/tmp/cav/case.foam'); r.MeshRegions=['internalMesh']; r.CellArrays=['U','p']; r.UpdatePipeline(0.5)
v=CreateRenderView(); v.ViewSize=[400,300]; d=Show(r,v); ColorBy(d,('CELLS','p')); Render(v); SaveScreenshot('/tmp/cav/shot.png',v)
import os; print('png', os.path.getsize('/tmp/cav/shot.png'))" 2>/dev/null | grep -q '^png [1-9]'; chk "pvpython renders the cavity case to PNG (software GL)" $?
[ "$(python3 -c 'import matplotlib; print(matplotlib.get_backend())')" = agg ]; chk "matplotlib backend agg under bash -lc" $?
timeout 20 python3 -c "import matplotlib.pyplot as plt; plt.plot([0,1]); plt.savefig('/tmp/a.png'); plt.show(); print('ok')" | grep -q ok; chk "plt.show() returns at once" $?
python3 -c "import scipy, pandas, numpy; print(scipy.__version__, pandas.__version__, numpy.__version__)" >/dev/null 2>&1; chk "scipy/pandas/numpy import" $?
gnuplot -e "set term" 2>&1 </dev/null | grep -qE '^ *(qt|x11|wxt) '; chk "gnuplot has an interactive terminal" $?
out=$(xdg-open https://example.com 2>&1); rc=$?; [ $rc = 0 ] && echo "$out" | grep -q 'no web browser'; chk "xdg-open URL: quiet no-browser handler (rc=$rc)" $?
[ "$(xdg-mime query default application/x-openfoam)" = paraview.desktop ]; chk ".foam opens ParaView" $?
[ "$(xdg-mime query default application/x-vtk)" = paraview.desktop ]; chk ".vtu opens ParaView" $?
grep -q 'stdin=subprocess.DEVNULL' /home/user/server/main.py; chk "guest server /execute has stdin=DEVNULL" $?
sudo -n true 2>/dev/null; chk "passwordless sudo" $?
df -BG / | awk 'NR==2{gsub("G","",$4); exit ($4>=15)?0:1}'; chk "root free space >= 15 GB ($(df -h / | awk 'NR==2{print $4}'))" $?
systemctl is-enabled unattended-upgrades 2>/dev/null | grep -q enabled; [ $? != 0 ]; chk "unattended-upgrades disabled" $?
systemctl is-enabled fstrim.timer 2>/dev/null | grep -q masked; chk "fstrim.timer masked" $?
pgrep -x xfce4-session >/dev/null && pgrep -x xfwm4 >/dev/null && pgrep -x xfce4-panel >/dev/null; chk "Xfce session, xfwm4 and panel running on :0" $?
[ "$(xdpyinfo 2>/dev/null | awk '/dimensions/{print $2}')" = 1920x1080 ]; chk "screen is 1920x1080 ($(xdpyinfo 2>/dev/null | awk '/dimensions/{print $2}'))" $?
grep -q 'paraview' /etc/xdg/xfce4/panel/default.xml && ! grep -qi 'web-browser\|WebBrowser' /etc/xdg/xfce4/panel/default.xml; chk "panel: ParaView launcher, no Web Browser launcher" $?
grep -q '^WebBrowser=osworld-no-browser' /etc/xdg/xfce4/helpers.rc; chk "Xfce WebBrowser helper is the no-op wrapper" $?
grep -q 'NoDisplay=true' /usr/share/applications/xfce4-web-browser.desktop /usr/share/applications/xfce4-session-logout.desktop; chk "dead menu entries hidden (web browser, log out)" $?
[ -f /home/user/Desktop/ParaView.desktop ] && [ -f /home/user/Desktop/Terminal.desktop ]; chk "desktop shortcuts Terminal + ParaView" $?
[ "$(md5sum < $WM_PROJECT_DIR/platforms/linux64GccDPInt32Opt/bin/simpleFoam | cut -c1-32)" = d66bb581dd92389773d9a7583f36e555 ]; chk "simpleFoam binary identical to the r3 container (md5 d66bb581)" $?
[ "$(dpkg-query -W -f='${Version}' openfoam2512)" = 2512.0-1 ] && [ "$(gcc -dumpversion)" = 13 ]; chk "openfoam2512 2512.0-1, gcc 13 (same as r3)" $?
. /etc/os-release; [ "$VERSION_ID" = 24.04 ]; chk "Ubuntu 24.04 (same as r3)" $?
timeout 20 exo-open https://example.com >/dev/null 2>&1; rc=$?; [ $rc = 0 ]; chk "exo-open URL returns at once, no modal (rc=$rc)" $?
sleep 1; ! pgrep -f 'xfce4-mime-helpe[r]' >/dev/null; chk "no stuck xfce4-mime-helper (1 s after exo-open)" $?   # the helper lives ~0.3 s after exo-open returns; bracket: pgrep -f must not match its caller
! grep -q 'FS Error' <(sudo tune2fs -l $(findmnt -n -o SOURCE /) 2>/dev/null | grep -E 'FS Error count: *[1-9]'); chk "ext4 error count 0" $?
command -v python >/dev/null && python -c "import pyautogui" 2>/dev/null; chk "python (no 3) has pyautogui — operator contract" $?
! systemctl is-active --quiet snapd 2>/dev/null && ! command -v snap >/dev/null; chk "no snapd" $?
[ "$(hostname)" = osworld ]; chk "hostname osworld" $?
rm -rf /tmp/cav /tmp/a.png
echo "verify: $fails failure(s)"; exit $((fails>0))
