#!/usr/bin/env bash
# ubuntu_openfoam verification (runs inside the guest through `bash -lc`): the r3 README "Verify" list, VM edition.
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
gsettings get org.gnome.shell favorite-apps | grep -q paraview; chk "ParaView in the dash favorites" $?
gsettings get org.gnome.shell favorite-apps | grep -qiE 'chrome|firefox'; [ $? != 0 ]; chk "no browser in the dash favorites" $?
command -v python >/dev/null && python -c "import pyautogui" 2>/dev/null; chk "python (no 3) has pyautogui — operator contract" $?
rm -rf /tmp/cav /tmp/a.png
echo "verify: $fails failure(s)"; exit $((fails>0))
