#!/usr/bin/env python3
"""Pre-task hook for the `ubuntu_astro` snapshot: put a freshly reset guest into
the per-episode initial state the Chandra/DS9 tasks assume.

    scripts/vm_prep/astro/prepare_episode.py --port 5090

Declared in configs/snapshots.yaml (`pre_task_hooks`, required), so `osci run`
executes it after the VM reset and before staging. `osci stage` on its own does
not run hooks: run this script by hand first when staging a task manually.

Everything below is run-time state that a container reset wipes, so it is
redone every episode and none of it is baked into the image:

1. Trust the two desktop launchers (`gio set metadata::trusted`). Untrusted
   launchers show a red cross and do nothing on double-click; an agent then
   concludes the software is broken.
2. Silence the "Update available for Snap Store" pop-up: stop and mask the
   user service `snap.snapd-desktop-integration` and hold snap refreshes (its
   check fires four times a day). Snaps themselves are never removed; that
   has crashed the guest before.
3. Scrub the leak channels: `~/cxcds_param4` (CIAO parameter files keep the
   last command line, so a tool run with ground-truth coordinates would leave
   the answer behind), the DS9 auto-backup, `~/.ds9`, the current-file
   marker, and the contents (never the directory) of `/tmp/ds9_dax.user`.
4. Install `start_ds9.sh` from this directory over `/home/user/start_ds9.sh`.
   The baked script always reopens the task-1 image and deletes the dax
   temporary directory, which dax never recreates; the replacement restores
   whatever DS9 was showing and only empties the directory. The desktop icon
   runs the same file, so a mis-click no longer swaps the episode's image.
5. Run it: DS9 comes up with the baseline image (ObsID 13858) in the § 2.1
   state: frame 1, block 1, log scale, min-max, grey, zoom 4, panned to the
   target, no regions. Tasks whose initial state differs re-point DS9 in
   their own staging steps; those steps require DS9 to be running.
6. Inject the cube-key guard: DS9 binds the + and - keys to CubeNext and
   CubePrev on every frame, and on a 2-D image that raises a modal Tcl error
   which freezes the coordinate read-out until dismissed. The guard wraps
   both procs so they act only on a real cube. This is a run-time Tcl change
   to stock DS9 and should be mentioned when the runs are written up.

Exits 0 only when DS9 is up with the baseline image loaded.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
from osworld_science.guest.client import Guest  # noqa: E402

# start_ds9.sh opens the baseline by a path relative to /home/user/CIAO_001, so DS9 reports it that way
BASELINE = "acisf13858_broad_thresh.img"
CONDA = "source /home/user/miniconda3/etc/profile.d/conda.sh && conda activate ciao-4.18"
XENV = "export DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority XDG_RUNTIME_DIR=/run/user/1000"

TRUST = r'''export DISPLAY=:0
PID=$(pgrep -u user -x gnome-shell | head -1)
[ -n "$PID" ] && export $(tr "\0" "\n" < /proc/$PID/environ | grep ^DBUS_SESSION_BUS_ADDRESS=)
for f in /home/user/Desktop/*.desktop; do [ -e "$f" ] || continue; chmod +x "$f"; gio set "$f" metadata::trusted true 2>/dev/null; done
echo TRUSTED'''

QUIET = r'''export XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
systemctl --user stop snap.snapd-desktop-integration.snapd-desktop-integration.service 2>/dev/null
systemctl --user mask snap.snapd-desktop-integration.snapd-desktop-integration.service 2>/dev/null
echo password | sudo -S -p "" bash -c "snap refresh --hold=forever >/dev/null 2>&1 || snap refresh --hold >/dev/null 2>&1; systemctl mask snapd.snap-repair.timer snapd.refresh.timer >/dev/null 2>&1; true"
echo "QUIET: snapd-desktop-integration=$(systemctl --user is-active snap.snapd-desktop-integration.snapd-desktop-integration.service 2>&1) processes=$(pgrep -c -x snapd-desktop-i 2>/dev/null)"'''

SCRUB = r'''rm -rf /home/user/cxcds_param4 /home/user/ds9.auto /home/user/ds9.auto.dir /home/user/.ds9 /home/user/.ds9_current_file
mkdir -p /tmp/ds9_dax.user && find /tmp/ds9_dax.user -mindepth 1 -delete 2>/dev/null
echo SCRUBBED'''

LAUNCH = r'''chmod +x /home/user/start_ds9.sh && bash -n /home/user/start_ds9.sh || { echo BAD_SCRIPT; exit 1; }
rm -f /tmp/start_ds9.log /tmp/start_ds9.done
setsid nohup bash -c '/home/user/start_ds9.sh > /tmp/start_ds9.log 2>&1; echo $? > /tmp/start_ds9.done' >/dev/null 2>&1 < /dev/null &
echo LAUNCHED'''

GUARD = XENV + "\n" + CONDA + r'''
XPA=$(xpaget xpans 2>/dev/null | awk '{print $4}' | head -1)
[ -n "$XPA" ] || { echo "GUARD_SKIP: DS9 not running"; exit 1; }
cat > /tmp/ds9_cube_guard.tcl <<'TCLEOF'
if {[info procs _orig_CubeNext] eq {}} {
    rename CubeNext _orig_CubeNext
    rename CubePrev _orig_CubePrev
    proc _ciao_frame_is_cube {} {
        global current
        if {![info exists current(frame)] || $current(frame) eq {}} {return 0}
        if {[catch {$current(frame) has fits cube} _c]} {return 0}
        return $_c
    }
    proc CubeNext {} { if {[_ciao_frame_is_cube]} { _orig_CubeNext } }
    proc CubePrev {} { if {[_ciao_frame_is_cube]} { _orig_CubePrev } }
}
TCLEOF
xpaset "$XPA" tcl < /tmp/ds9_cube_guard.tcl
rm -f /tmp/_guardchk.txt
cat <<'TCLEOF' | xpaset "$XPA" tcl
set _f [open /tmp/_guardchk.txt w]
puts $_f "orig=[expr {[info procs _orig_CubeNext] ne {}}] wrapped=[string length [info body CubeNext]] iscube=[_ciao_frame_is_cube]"
close $_f
TCLEOF
sleep 1
echo "GUARD: $(cat /tmp/_guardchk.txt 2>/dev/null || echo no-receipt)"
rm -f /tmp/ds9_cube_guard.tcl /tmp/_guardchk.txt'''

STATE = XENV + "\n" + CONDA + r'''
XPA=$(xpaget xpans 2>/dev/null | awk '{print $4}' | head -1)
[ -n "$XPA" ] || { echo "NO_DS9"; exit 1; }
echo "STATE: file=$(xpaget "$XPA" file | sed 's/\[.*$//') frame=$(xpaget "$XPA" frame) block=$(xpaget "$XPA" block) zoom=$(xpaget "$XPA" zoom) regions=$(xpaget "$XPA" regions -format ds9 | grep -cE '^(circle|annulus|box|ellipse|polygon|point)') param_dir=$([ -e /home/user/cxcds_param4 ] && echo present || echo absent) dax_dir=$([ -d /tmp/ds9_dax.user ] && echo ok || echo missing)"'''


def sh(g: Guest, script: str, timeout: int = 90) -> tuple[str, int | None]:
    r = g.execute(["bash", "-c", script], timeout)
    return ((r.get("output") or "") + (r.get("error") or "")).strip(), r.get("returncode")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5090)
    ap.add_argument("--timeout", type=float, default=180, help="seconds to wait for DS9 to come up")
    a = ap.parse_args()
    g = Guest(a.port)
    if not g.alive():
        print(f"[astro-hook] no guest on :{a.port}")
        return 1
    for label, script in (("trust", TRUST), ("quiet", QUIET), ("scrub", SCRUB)):
        out, rc = sh(g, script)
        print(f"[astro-hook] {label}: {out.splitlines()[-1] if out else '(no output)'}")
    g.upload(HERE / "start_ds9.sh", "/home/user/start_ds9.sh")
    out, rc = sh(g, LAUNCH)
    if "LAUNCHED" not in out:
        print(f"[astro-hook] could not launch start_ds9.sh: {out[-300:]}")
        return 1
    deadline = time.time() + a.timeout
    log = ""
    while time.time() < deadline:
        time.sleep(3)
        flag, _ = sh(g, "cat /tmp/start_ds9.done 2>/dev/null", 30)
        if flag != "":
            log, _ = sh(g, "cat /tmp/start_ds9.log; rm -f /tmp/start_ds9.log /tmp/start_ds9.done", 30)
            break
    else:
        print("[astro-hook] start_ds9.sh did not finish within the timeout (not a success)")
        return 1
    ready = [ln for ln in log.splitlines() if ln.startswith("DS9 ready:")]
    print(f"[astro-hook] {ready[0] if ready else log[-400:]}")
    if flag.strip() != "0" or not ready or BASELINE not in ready[0]:
        print(f"[astro-hook] DS9 is not showing the baseline image (exit {flag.strip()})")
        return 1
    out, _ = sh(g, GUARD, 120)
    print(f"[astro-hook] {out.splitlines()[-1] if out else 'guard: no output'}")
    if "GUARD: orig=1" not in out:
        print("[astro-hook] cube-key guard was not installed")
        return 1
    out, _ = sh(g, STATE, 60)
    print(f"[astro-hook] {out}")
    return 0 if out.startswith("STATE: file=") and BASELINE in out.split()[1] else 1


if __name__ == "__main__":
    sys.exit(main())
